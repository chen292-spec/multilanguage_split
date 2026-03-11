import re
import asyncio
import unicodedata
from typing import List, Tuple

# langdetect 是可选依赖，支持全球 55 种语言的自动识别
# 安装后作为主检测器，未安装时回退到 Unicode 字符范围检测（仅能区分文字系统）
# 安装方法: pip install langdetect
try:
    from langdetect import detect as ld_detect  # pip install langdetect
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

# ==================== AstrBot 框架核心导入 ====================
# filter: 事件过滤器，用于注册各种事件钩子（如消息发送前拦截）
# AstrMessageEvent: 消息事件对象，包含用户消息和回复结果
# MessageChain: 消息链，用于构建要发送的消息
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
# Context: 插件上下文; Star: 插件基类; register: 插件注册装饰器
from astrbot.api.star import Context, Star, register
# AstrBotConfig: 插件配置对象; logger: 日志工具
from astrbot.api import AstrBotConfig, logger
# Plain: 纯文本消息组件; Reply: 引用回复组件
from astrbot.api.message_components import Plain, Reply
# LLMResponse: 大语言模型的回复对象
from astrbot.api.provider import LLMResponse


# register() 注册插件：插件ID、作者、描述、版本号
@register("multilanguage_split", "chen292-spec",
          "多语言分对话框回复插件 - 支持全球55种语言自动识别并分开发送",
          "1.2.0")
class MultiLanguageSplitPlugin(Star):
    """多语言分段发送插件。

    当机器人回复包含多种语言时，自动识别每种语言并分成不同的对话框发送。
    emoji 和颜文字也会被单独分出来发送。

    语言检测策略：
    - 安装 langdetect 后：使用 langdetect 作为主检测器，支持全球 55 种语言
      （含中文、日语、韩语、英语、法语、德语、俄语、西班牙语、意大利语、
       阿拉伯语、印地语、泰语、越南语、希腊语、希伯来语、波斯语等）
    - 未安装 langdetect：回退到 Unicode 字符范围检测，可区分文字系统
      （中文/日文/韩文/拉丁/西里尔/阿拉伯，但英法德意西无法区分）
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # config 是插件的配置字典，对应 _conf_schema.json 中定义的选项
        self.config = config or {}

        # === 读取用户配置（带默认值和类型安全） ===

        # 每条消息之间的发送延迟（秒）
        try:
            self.delay = float(self.config.get("delay", 1.0))
        except (ValueError, TypeError):
            self.delay = 1.0

        # 是否在第一条消息中引用用户的原消息
        self.enable_reply = bool(self.config.get("enable_reply", True))

        # 是否把文本行末尾的 emoji 也拆分出来
        self.split_inline_emoji = bool(self.config.get("split_inline_emoji", True))

        # 作用范围："llm_only" 只处理大模型回复，"all" 处理所有消息
        self.split_scope = self.config.get("split_scope", "llm_only")

        # 是否启用 langdetect 语言检测库（支持全球 55 种语言）
        self.enable_langdetect = bool(self.config.get("enable_langdetect", True))

        # 在初始化时检查 langdetect 是否可用
        if self.enable_langdetect and LANGDETECT_AVAILABLE:
            logger.info(
                "[MultiLangSplit] langdetect 已加载，支持全球 55 种语言识别"
            )
        elif self.enable_langdetect and not LANGDETECT_AVAILABLE:
            logger.warning(
                "[MultiLangSplit] 未安装 langdetect 库，将回退到 Unicode 字符范围检测。"
                "回退模式可区分中/日/韩/拉丁/西里尔/阿拉伯，但英法德意西无法区分。"
                "如需完整支持，请运行: pip install langdetect"
            )

    # ==================== 事件钩子 ====================

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """当大语言模型产生回复时触发。在这里做标记，方便后续判断是否为 LLM 回复。"""
        # 给事件打上标记，表示这是一条 LLM 生成的回复
        setattr(event, "__is_llm_reply", True)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在消息即将发送给用户之前触发（发送前拦截）。
        这是插件的核心逻辑：拦截回复 → 识别语言 → 分段发送。
        """
        # --- 1. 作用范围检查 ---
        # 如果设置为仅处理 LLM 回复，跳过非 LLM 消息
        if self.split_scope == "llm_only" and not getattr(event, "__is_llm_reply", False):
            return

        # --- 2. 获取当前要发送的消息结果 ---
        result = event.get_result()
        if not result or not result.chain:
            return

        # --- 3. 提取所有纯文本内容 ---
        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):  # Plain 是纯文本消息组件
                raw_text += comp.text

        raw_text = raw_text.strip()
        if not raw_text:
            return

        # --- 4. 按语言分段 ---
        segments = self.split_by_language(raw_text)

        # 如果只有一段，不需要分割，直接放行让框架正常发送
        if len(segments) <= 1:
            return

        logger.info(f"[MultiLangSplit] 检测到 {len(segments)} 种语言/类型的内容，开始分段发送")

        # --- 5. 清空原始消息链，准备分段发送 ---
        result.chain.clear()

        # --- 6. 发送前 N-1 段（通过 event.send 直接发送） ---
        for i, seg_text in enumerate(segments[:-1]):
            # 构建一条新的消息链
            mc = MessageChain()

            # 第一条消息加上"引用回复"（引用用户的原消息）
            if i == 0 and self.enable_reply and event.message_obj.message_id:
                mc.chain.append(Reply(id=event.message_obj.message_id))

            mc.chain.append(Plain(seg_text))  # 添加文本内容

            try:
                # event.send() 直接发送消息到聊天平台
                await event.send(mc)
                # 每条消息之间等待一段时间，模拟打字效果
                await asyncio.sleep(self.delay)
            except Exception as e:
                logger.error(f"[MultiLangSplit] 发送第 {i + 1} 段失败: {e}")

        # --- 7. 最后一段放回 result.chain，由框架正常发送 ---
        # 这样做的好处：框架能正确记录对话历史
        result.chain.append(Plain(segments[-1]))

        logger.info(f"[MultiLangSplit] 分段发送完成，共 {len(segments)} 段")

    # ==================== 核心分段逻辑 ====================

    def split_by_language(self, text: str) -> List[str]:
        """将文本按语言类型分割成多个段落。

        处理流程：
        1. 按换行符拆分成多行
        2. 对每一行进行"语言检测"
        3. 如果开启了 split_inline_emoji，还会把行末的 emoji 拆出来
        4. 把连续相同语言的行合并为一段
        """
        # 按换行符拆分
        lines = text.split('\n')

        # classified_parts: 存放 (语言类型, 文本内容) 的列表
        classified_parts: List[Tuple[str, str]] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue  # 跳过空行

            # 如果开启了"拆分行内 emoji"，尝试把末尾 emoji 拆出来
            if self.split_inline_emoji:
                text_part, emoji_part = self._extract_trailing_emoji(stripped)
                if text_part:
                    lang = self._detect_language(text_part)
                    classified_parts.append((lang, text_part))
                if emoji_part:
                    classified_parts.append(("emoji", emoji_part))
            else:
                lang = self._detect_language(stripped)
                classified_parts.append((lang, stripped))

        if not classified_parts:
            return [text]

        # 合并连续相同语言的行
        segments: List[str] = []
        current_lang = classified_parts[0][0]
        current_texts = [classified_parts[0][1]]

        for lang, line_text in classified_parts[1:]:
            if lang == current_lang:
                # 同一语言，合并到当前段
                current_texts.append(line_text)
            else:
                # 不同语言，保存当前段，开始新段
                segments.append('\n'.join(current_texts))
                current_lang = lang
                current_texts = [line_text]

        # 别忘了最后一段
        if current_texts:
            segments.append('\n'.join(current_texts))

        # 过滤掉空段
        segments = [s for s in segments if s.strip()]

        return segments if segments else [text]

    # ==================== 语言检测 ====================

    def _detect_language(self, text: str) -> str:
        """检测一行文本的主要语言类型。

        检测策略：
        1. 先检查是否为 emoji / 颜文字
        2. 如果安装了 langdetect，使用它作为主检测器（支持 55 种语言）
        3. 如果 langdetect 未安装或检测失败，回退到 Unicode 字符范围检测

        langdetect 支持的 55 种语言包括：
        af(南非荷兰语), ar(阿拉伯语), bg(保加利亚语), bn(孟加拉语),
        ca(加泰罗尼亚语), cs(捷克语), cy(威尔士语), da(丹麦语),
        de(德语), el(希腊语), en(英语), es(西班牙语),
        et(爱沙尼亚语), fa(波斯语), fi(芬兰语), fr(法语),
        gu(古吉拉特语), he(希伯来语), hi(印地语), hr(克罗地亚语),
        hu(匈牙利语), id(印尼语), it(意大利语), ja(日语),
        kn(卡纳达语), ko(韩语), lt(立陶宛语), lv(拉脱维亚语),
        mk(马其顿语), ml(马拉雅拉姆语), mr(马拉地语), ne(尼泊尔语),
        nl(荷兰语), no(挪威语), pa(旁遮普语), pl(波兰语),
        pt(葡萄牙语), ro(罗马尼亚语), ru(俄语), sk(斯洛伐克语),
        sl(斯洛文尼亚语), so(索马里语), sq(阿尔巴尼亚语), sv(瑞典语),
        sw(斯瓦希里语), ta(泰米尔语), te(泰卢固语), th(泰语),
        tl(菲律宾语), tr(土耳其语), uk(乌克兰语), ur(乌尔都语),
        vi(越南语), zh-cn(简体中文), zh-tw(繁体中文)

        回退模式（未安装 langdetect）返回值：
        chinese, japanese, korean, latin, russian, arabic, emoji, other
        """
        # --- 第 0 步：检查 emoji / 颜文字 ---
        if self._is_emoji_or_kaomoji(text):
            return "emoji"

        # --- 第 1 步：尝试用 langdetect 检测（主检测器，支持 55 种语言） ---
        if self.enable_langdetect and LANGDETECT_AVAILABLE:
            result = self._detect_by_langdetect(text)
            if result is not None:
                return result

        # --- 第 2 步：回退到 Unicode 字符范围检测 ---
        return self._detect_by_unicode(text)

    def _detect_by_langdetect(self, text: str) -> str:
        """使用 langdetect 库检测语言（主检测器）。

        langdetect 基于 Google 的语言检测算法，能识别 55 种语言。
        对于短文本可能不太准确，所以失败时返回 None，让调用者回退到其他方法。

        返回：语言代码字符串，或 None（检测失败）
        """
        try:
            lang_code = ld_detect(text)

            # langdetect 返回的是 ISO 639-1 语言代码，如 "en", "fr", "zh-cn" 等
            # 所有 langdetect 支持的 55 种语言代码
            all_supported = {
                'af', 'ar', 'bg', 'bn', 'ca', 'cs', 'cy', 'da', 'de',
                'el', 'en', 'es', 'et', 'fa', 'fi', 'fr', 'gu', 'he',
                'hi', 'hr', 'hu', 'id', 'it', 'ja', 'kn', 'ko', 'lt',
                'lv', 'mk', 'ml', 'mr', 'ne', 'nl', 'no', 'pa', 'pl',
                'pt', 'ro', 'ru', 'sk', 'sl', 'so', 'sq', 'sv', 'sw',
                'ta', 'te', 'th', 'tl', 'tr', 'uk', 'ur', 'vi',
                'zh-cn', 'zh-tw',
            }

            if lang_code in all_supported:
                return lang_code
            else:
                # 未知的语言代码，回退
                return None
        except Exception:
            # 检测失败（文本太短、特殊字符等），回退
            return None

    def _detect_by_unicode(self, text: str) -> str:
        """通过 Unicode 字符范围检测语言（兜底方案）。

        只能区分文字系统（中文/日文/韩文/拉丁/西里尔/阿拉伯），
        无法区分同一文字系统的不同语言（如英语 vs 法语）。
        """
        counts = {
            'chinese': 0,    # CJK 表意文字
            'japanese': 0,   # 平假名 + 片假名
            'korean': 0,     # 韩文字母
            'latin': 0,      # 拉丁字母
            'cyrillic': 0,   # 西里尔字母
            'arabic': 0,     # 阿拉伯字母
            'emoji': 0,      # emoji
        }

        for char in text:
            cp = ord(char)
            if self._is_emoji_char(char):
                counts['emoji'] += 1
            elif self._is_hiragana(cp) or self._is_katakana(cp):
                counts['japanese'] += 1
            elif self._is_hangul(cp):
                counts['korean'] += 1
            elif self._is_cjk(cp):
                counts['chinese'] += 1
            elif self._is_cyrillic(cp):
                counts['cyrillic'] += 1
            elif self._is_arabic(cp):
                counts['arabic'] += 1
            elif self._is_latin(cp):
                counts['latin'] += 1

        # 如果有平假名/片假名，说明是日语，汉字也归为日语
        if counts['japanese'] > 0:
            counts['japanese'] += counts['chinese']
            counts['chinese'] = 0

        total = sum(counts.values())
        if total == 0:
            return "other"

        if counts['emoji'] > 0 and counts['emoji'] / total > 0.5:
            return "emoji"

        lang_counts = {k: v for k, v in counts.items() if k != 'emoji'}
        if not any(lang_counts.values()):
            return "emoji" if counts['emoji'] > 0 else "other"

        dominant = max(lang_counts, key=lang_counts.get)

        # 把内部名称映射为更可读的名称
        name_map = {
            'cyrillic': 'russian',   # 西里尔字母 → 俄语
            'arabic': 'arabic',      # 阿拉伯字母 → 阿拉伯语
        }
        return name_map.get(dominant, dominant)

    # ==================== 行尾 Emoji 提取 ====================

    def _extract_trailing_emoji(self, text: str) -> Tuple[str, str]:
        """从文本末尾提取连续的 emoji 字符。

        例如：
          "你好世界😊✨" → ("你好世界", "😊✨")
          "😊✨"        → ("", "😊✨")
          "你好世界"     → ("你好世界", "")

        返回: (文本部分, emoji部分)
        """
        if not text:
            return (text, "")

        # 从末尾往前扫描，找到连续 emoji 的起始位置
        i = len(text)
        while i > 0:
            # 处理 Unicode 代理对和组合 emoji（如肤色修饰符、ZWJ 序列）
            char = text[i - 1]
            if self._is_emoji_char(char) or char in ('\ufe0f', '\ufe0e', '\u200d', ' '):
                i -= 1
            else:
                break

        # 去掉末尾可能多余的空格
        text_part = text[:i].rstrip()
        emoji_part = text[i:].strip()

        # 如果 emoji 部分没有实际 emoji 字符（只有空格等），不拆分
        if emoji_part and not any(self._is_emoji_char(c) for c in emoji_part):
            return (text, "")

        return (text_part, emoji_part)

    # ==================== Emoji / 颜文字检测 ====================

    def _is_emoji_or_kaomoji(self, text: str) -> bool:
        """判断文本是否整体为 emoji 或颜文字（kaomoji）。

        颜文字举例：(╥_╥)  (✿◠‿◠)  ╮(╯▽╰)╭  (≧▽≦)  OwO
        """
        stripped = text.strip()
        if not stripped:
            return False

        # --- 检查1：是否为颜文字 ---
        # 颜文字通常以括号包裹，内部是特殊符号
        # 匹配各种括号开头和结尾的颜文字，括号前后可有装饰字符
        kaomoji_pattern = (
            r'^[^\w\s]*'                              # 前导装饰符号（可选）
            r'[（(（\[【<＜{「『《〈]'                    # 左括号
            r'[^（(（\[【<＜{「『《〈）)\]】>＞}」』》〉]*'  # 中间内容
            r'[）)\]】>＞}」』》〉]'                      # 右括号
            r'[^\w\s]*$'                               # 尾部装饰符号（可选）
        )
        if re.match(kaomoji_pattern, stripped):
            # 进一步确认：颜文字内部通常没有太多正常文字
            # 排除像"（你好）"这种正常括号句子
            alpha_count = sum(1 for c in stripped if c.isalpha() and (
                self._is_cjk(ord(c)) or self._is_hangul(ord(c))
                or self._is_hiragana(ord(c)) or self._is_latin(ord(c))
            ))
            if alpha_count <= 3:  # 颜文字中最多有少量字母（如 T_T 中的 T）
                return True

        # --- 检查2：是否全是 emoji ---
        emoji_count = sum(1 for c in stripped if self._is_emoji_char(c))
        non_space = sum(1 for c in stripped if not c.isspace())
        if non_space > 0 and emoji_count / non_space > 0.5:
            return True

        # --- 检查3：短文本中全是特殊符号（可能是颜文字） ---
        # 常见颜文字使用的特殊字符集合
        kaomoji_special = set(
            '╮╯╰╭╥_✿◠‿◕ᴗωﾉ゜ーノ・∀°▽≧≦╹◡╹っ♡♥☆★♪♫♬≈'
            '´`~=+<>^ˊˋˇ﹏●○◎△▲▼▽□■◆◇'
            '☉☆★※†‡'
        )
        if len(stripped) <= 25:
            # 统计"明显是文字"的字符
            real_letter_count = 0
            special_or_punct_count = 0
            for c in stripped:
                cp = ord(c)
                if (self._is_cjk(cp) or self._is_hangul(cp)
                        or self._is_latin(cp)):
                    real_letter_count += 1
                elif (c in kaomoji_special
                      or unicodedata.category(c).startswith(('S', 'P'))
                      or self._is_emoji_char(c)):
                    special_or_punct_count += 1

            # 如果没有正常文字，且大部分是符号/标点，判定为颜文字
            if (real_letter_count == 0
                    and special_or_punct_count > 0
                    and non_space > 0
                    and special_or_punct_count / non_space > 0.5):
                return True

        return False

    # ==================== Unicode 字符范围判断工具方法 ====================
    # 以下方法通过 Unicode 码点范围来判断字符属于哪种文字系统

    @staticmethod
    def _is_emoji_char(char: str) -> bool:
        """判断单个字符是否为 emoji。
        覆盖了绝大部分常用 emoji 的 Unicode 范围。
        """
        cp = ord(char)
        return (
            0x1F600 <= cp <= 0x1F64F or   # 表情符号（笑脸等）
            0x1F300 <= cp <= 0x1F5FF or   # 杂项符号和象形文字
            0x1F680 <= cp <= 0x1F6FF or   # 交通和地图符号
            0x1F1E0 <= cp <= 0x1F1FF or   # 国旗
            0x2600 <= cp <= 0x26FF or     # 杂项符号（太阳、星星等）
            0x2700 <= cp <= 0x27BF or     # 装饰符号
            0x1F900 <= cp <= 0x1F9FF or   # 补充符号
            0x1FA00 <= cp <= 0x1FA6F or   # 象棋符号
            0x1FA70 <= cp <= 0x1FAFF or   # 符号扩展-A
            0xFE00 <= cp <= 0xFE0F or     # 变体选择符
            cp == 0x200D or               # 零宽连接符（用于组合emoji）
            0x2300 <= cp <= 0x23FF or     # 技术符号
            0x25A0 <= cp <= 0x25FF or     # 几何图形
            0x2B05 <= cp <= 0x2B55 or     # 箭头和几何补充
            0x3030 == cp or               # 波浪线
            0x303D == cp or               # 日语工业标准符号
            0x2764 == cp or               # 红心 ❤
            0x2763 == cp or               # 心叹号 ❣
            0x2728 == cp or               # 闪光 ✨
            0x2705 == cp or               # 对勾 ✅
            0x274C == cp or               # 叉号 ❌
            0x274E == cp                  # 带框叉号 ❎
        )

    @staticmethod
    def _is_hiragana(cp: int) -> bool:
        """判断是否为日语平假名（如：あいうえお）。"""
        return 0x3040 <= cp <= 0x309F

    @staticmethod
    def _is_katakana(cp: int) -> bool:
        """判断是否为日语片假名（如：アイウエオ）。
        包含半角片假名范围。
        """
        return 0x30A0 <= cp <= 0x30FF or 0xFF65 <= cp <= 0xFF9F

    @staticmethod
    def _is_hangul(cp: int) -> bool:
        """判断是否为韩文字母（谚文，如：가나다라）。
        覆盖韩文音节、字母和兼容字母等范围。
        """
        return (
            0xAC00 <= cp <= 0xD7AF or   # 韩文音节
            0x1100 <= cp <= 0x11FF or   # 韩文字母
            0x3130 <= cp <= 0x318F or   # 韩文兼容字母
            0xA960 <= cp <= 0xA97F or   # 韩文字母扩展-A
            0xD7B0 <= cp <= 0xD7FF      # 韩文字母扩展-B
        )

    @staticmethod
    def _is_cjk(cp: int) -> bool:
        """判断是否为 CJK 统一表意文字（中日韩共用的汉字）。
        中文和日语都会用到这些字符，需要结合假名来区分。
        """
        return (
            0x4E00 <= cp <= 0x9FFF or     # CJK 基本区
            0x3400 <= cp <= 0x4DBF or     # CJK 扩展 A
            0x20000 <= cp <= 0x2A6DF or   # CJK 扩展 B
            0x2A700 <= cp <= 0x2B73F or   # CJK 扩展 C
            0x2B740 <= cp <= 0x2B81F or   # CJK 扩展 D
            0xF900 <= cp <= 0xFAFF or     # CJK 兼容表意文字
            0x2F800 <= cp <= 0x2FA1F      # CJK 兼容表意文字补充
        )

    @staticmethod
    def _is_cyrillic(cp: int) -> bool:
        """判断是否为西里尔字母（俄语、乌克兰语、塞尔维亚语等使用）。
        如：А Б В Г Д Е Ж З И К Л М Н О П Р С Т У Ф Х Ц Ч Ш Щ Ъ Ы Ь Э Ю Я
        """
        return (
            0x0400 <= cp <= 0x04FF or   # 西里尔基本字母（俄语等）
            0x0500 <= cp <= 0x052F or   # 西里尔补充
            0x2DE0 <= cp <= 0x2DFF or   # 西里尔扩展-A
            0xA640 <= cp <= 0xA69F      # 西里尔扩展-B
        )

    @staticmethod
    def _is_arabic(cp: int) -> bool:
        """判断是否为阿拉伯字母（阿拉伯语、波斯语等使用）。"""
        return (
            0x0600 <= cp <= 0x06FF or   # 阿拉伯基本字母
            0x0750 <= cp <= 0x077F or   # 阿拉伯补充
            0x08A0 <= cp <= 0x08FF or   # 阿拉伯扩展-A
            0xFB50 <= cp <= 0xFDFF or   # 阿拉伯表现形式-A
            0xFE70 <= cp <= 0xFEFF      # 阿拉伯表现形式-B
        )

    @staticmethod
    def _is_latin(cp: int) -> bool:
        """判断是否为拉丁字母（英/法/德/意/西等西方语言共用的字母）。
        注意：仅靠字符范围无法区分这些语言，需要配合 langdetect 库。
        """
        return (
            0x0041 <= cp <= 0x005A or   # A-Z 大写
            0x0061 <= cp <= 0x007A or   # a-z 小写
            0x00C0 <= cp <= 0x00FF or   # 拉丁字母扩展（如 é, ñ, ü, ß）
            0x0100 <= cp <= 0x024F      # 拉丁字母扩展附加
        )

    async def terminate(self):
        """插件卸载/停用时调用的清理方法。"""
        logger.info("[MultiLangSplit] 多语言分段插件已卸载")
