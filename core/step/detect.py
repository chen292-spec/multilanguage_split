"""多语言检测步骤。

插件的核心逻辑：识别文本中的不同语言，拆分成多个段落。
检测结果写入 ctx.segments，供后续 send 步骤消费。

语言检测策略（两层）：
1. langdetect 库（主检测器，55 种语言）
2. Unicode 字符范围（兜底，区分文字系统）
"""

import re
import unicodedata
from typing import List, Optional, Tuple

from astrbot.api import logger

# langdetect 是可选依赖，安装后支持 55 种语言识别
try:
    from langdetect import detect as ld_detect
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

from ..config import PluginConfig
from ..model import OutContext, StepName, StepResult
from .base import BaseStep


class DetectStep(BaseStep):
    """多语言检测 + 分段步骤。

    读取 ctx.plain，按语言拆分后写入 ctx.segments。
    如果只有一段（单语言），segments 保持为空，表示不需要分段发送。
    """
    name = StepName.DETECT

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.detect

    async def initialize(self) -> None:
        """启动时检查 langdetect 可用性。"""
        if self.cfg.enable_langdetect and LANGDETECT_AVAILABLE:
            logger.info("[MultiLangSplit] langdetect 已加载，支持 55 种语言识别")
        elif self.cfg.enable_langdetect and not LANGDETECT_AVAILABLE:
            logger.warning(
                "[MultiLangSplit] 未安装 langdetect，回退到 Unicode 检测。"
                "英/法/德/意/西等拉丁语系无法区分。请运行: pip install langdetect"
            )

    async def handle(self, ctx: OutContext) -> StepResult:
        """检测语言并分段，结果写入 ctx.segments。"""
        if not ctx.plain:
            return StepResult()

        segments = self._split_by_language(ctx.plain)

        # 只有一段说明是单语言，不需要分段
        if len(segments) <= 1:
            return StepResult(msg="[MultiLangSplit] 单语言，无需分段")

        ctx.segments = segments
        return StepResult(
            msg=f"[MultiLangSplit] 检测到 {len(segments)} 种语言/类型"
        )

    # ==================== 核心分段逻辑 ====================

    def _split_by_language(self, text: str) -> List[str]:
        """将文本按语言类型分割成多个段落。

        流程：按行拆分 → 检测每行语言 → 合并连续同语言行
        """
        lines = text.split('\n')
        classified_parts: List[Tuple[str, str]] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 拆分行末 emoji
            if self.cfg.split_inline_emoji:
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
                current_texts.append(line_text)
            else:
                segments.append('\n'.join(current_texts))
                current_lang = lang
                current_texts = [line_text]

        if current_texts:
            segments.append('\n'.join(current_texts))

        segments = [s for s in segments if s.strip()]
        return segments if segments else [text]

    # ==================== 语言检测 ====================

    def _detect_language(self, text: str) -> str:
        """检测一行文本的主要语言类型。

        策略：emoji/颜文字 → langdetect → Unicode 兜底
        """
        if self._is_emoji_or_kaomoji(text):
            return "emoji"

        if self.cfg.enable_langdetect and LANGDETECT_AVAILABLE:
            result = self._detect_by_langdetect(text)
            if result is not None:
                return result

        return self._detect_by_unicode(text)

    def _detect_by_langdetect(self, text: str) -> Optional[str]:
        """使用 langdetect 库检测语言（主检测器，55 种语言）。"""
        try:
            lang_code = ld_detect(text)
            all_supported = {
                'af', 'ar', 'bg', 'bn', 'ca', 'cs', 'cy', 'da', 'de',
                'el', 'en', 'es', 'et', 'fa', 'fi', 'fr', 'gu', 'he',
                'hi', 'hr', 'hu', 'id', 'it', 'ja', 'kn', 'ko', 'lt',
                'lv', 'mk', 'ml', 'mr', 'ne', 'nl', 'no', 'pa', 'pl',
                'pt', 'ro', 'ru', 'sk', 'sl', 'so', 'sq', 'sv', 'sw',
                'ta', 'te', 'th', 'tl', 'tr', 'uk', 'ur', 'vi',
                'zh-cn', 'zh-tw',
            }
            return lang_code if lang_code in all_supported else None
        except Exception:
            return None

    def _detect_by_unicode(self, text: str) -> str:
        """通过 Unicode 字符范围检测语言（兜底方案）。"""
        counts = {
            'chinese': 0, 'japanese': 0, 'korean': 0,
            'latin': 0, 'cyrillic': 0, 'arabic': 0, 'emoji': 0,
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
        name_map = {'cyrillic': 'russian', 'arabic': 'arabic'}
        return name_map.get(dominant, dominant)

    # ==================== 行尾 Emoji 提取 ====================

    def _extract_trailing_emoji(self, text: str) -> Tuple[str, str]:
        """从文本末尾提取连续的 emoji 字符。

        例如："你好世界😊✨" → ("你好世界", "😊✨")
        """
        if not text:
            return (text, "")

        i = len(text)
        while i > 0:
            char = text[i - 1]
            if self._is_emoji_char(char) or char in ('\ufe0f', '\ufe0e', '\u200d', ' '):
                i -= 1
            else:
                break

        text_part = text[:i].rstrip()
        emoji_part = text[i:].strip()

        if emoji_part and not any(self._is_emoji_char(c) for c in emoji_part):
            return (text, "")

        return (text_part, emoji_part)

    # ==================== Emoji / 颜文字检测 ====================

    def _is_emoji_or_kaomoji(self, text: str) -> bool:
        """判断文本是否整体为 emoji 或颜文字。"""
        stripped = text.strip()
        if not stripped:
            return False

        # 检查1：括号包裹的颜文字
        kaomoji_pattern = (
            r'^[^\w\s]*'
            r'[（(（\[【<＜{「『《〈]'
            r'[^（(（\[【<＜{「『《〈）)\]】>＞}」』》〉]*'
            r'[）)\]】>＞}」』》〉]'
            r'[^\w\s]*$'
        )
        if re.match(kaomoji_pattern, stripped):
            alpha_count = sum(1 for c in stripped if c.isalpha() and (
                self._is_cjk(ord(c)) or self._is_hangul(ord(c))
                or self._is_hiragana(ord(c)) or self._is_latin(ord(c))
            ))
            if alpha_count <= 3:
                return True

        # 检查2：全是 emoji
        emoji_count = sum(1 for c in stripped if self._is_emoji_char(c))
        non_space = sum(1 for c in stripped if not c.isspace())
        if non_space > 0 and emoji_count / non_space > 0.5:
            return True

        # 检查3：短文本中全是特殊符号
        kaomoji_special = set(
            '╮╯╰╭╥_✿◠‿◕ᴗωﾉ゜ーノ・∀°▽≧≦╹◡╹っ♡♥☆★♪♫♬≈'
            '´`~=+<>^ˊˋˇ﹏●○◎△▲▼▽□■◆◇'
            '☉☆★※†‡'
        )
        if len(stripped) <= 25:
            real_letter_count = 0
            special_or_punct_count = 0
            for c in stripped:
                cp = ord(c)
                if self._is_cjk(cp) or self._is_hangul(cp) or self._is_latin(cp):
                    real_letter_count += 1
                elif (c in kaomoji_special
                      or unicodedata.category(c).startswith(('S', 'P'))
                      or self._is_emoji_char(c)):
                    special_or_punct_count += 1
            if (real_letter_count == 0
                    and special_or_punct_count > 0
                    and non_space > 0
                    and special_or_punct_count / non_space > 0.5):
                return True

        return False

    # ==================== Unicode 字符范围判断 ====================

    @staticmethod
    def _is_emoji_char(char: str) -> bool:
        """判断单个字符是否为 emoji。"""
        cp = ord(char)
        return (
            0x1F600 <= cp <= 0x1F64F or
            0x1F300 <= cp <= 0x1F5FF or
            0x1F680 <= cp <= 0x1F6FF or
            0x1F1E0 <= cp <= 0x1F1FF or
            0x2600 <= cp <= 0x26FF or
            0x2700 <= cp <= 0x27BF or
            0x1F900 <= cp <= 0x1F9FF or
            0x1FA00 <= cp <= 0x1FA6F or
            0x1FA70 <= cp <= 0x1FAFF or
            0xFE00 <= cp <= 0xFE0F or
            cp == 0x200D or
            0x2300 <= cp <= 0x23FF or
            0x25A0 <= cp <= 0x25FF or
            0x2B05 <= cp <= 0x2B55 or
            0x3030 == cp or
            0x303D == cp or
            0x2764 == cp or
            0x2763 == cp or
            0x2728 == cp or
            0x2705 == cp or
            0x274C == cp or
            0x274E == cp
        )

    @staticmethod
    def _is_hiragana(cp: int) -> bool:
        """平假名（あいうえお）"""
        return 0x3040 <= cp <= 0x309F

    @staticmethod
    def _is_katakana(cp: int) -> bool:
        """片假名（アイウエオ），含半角"""
        return 0x30A0 <= cp <= 0x30FF or 0xFF65 <= cp <= 0xFF9F

    @staticmethod
    def _is_hangul(cp: int) -> bool:
        """韩文字母（가나다라）"""
        return (
            0xAC00 <= cp <= 0xD7AF or
            0x1100 <= cp <= 0x11FF or
            0x3130 <= cp <= 0x318F or
            0xA960 <= cp <= 0xA97F or
            0xD7B0 <= cp <= 0xD7FF
        )

    @staticmethod
    def _is_cjk(cp: int) -> bool:
        """CJK 统一表意文字（汉字）"""
        return (
            0x4E00 <= cp <= 0x9FFF or
            0x3400 <= cp <= 0x4DBF or
            0x20000 <= cp <= 0x2A6DF or
            0x2A700 <= cp <= 0x2B73F or
            0x2B740 <= cp <= 0x2B81F or
            0xF900 <= cp <= 0xFAFF or
            0x2F800 <= cp <= 0x2FA1F
        )

    @staticmethod
    def _is_cyrillic(cp: int) -> bool:
        """西里尔字母（俄语等）"""
        return (
            0x0400 <= cp <= 0x04FF or
            0x0500 <= cp <= 0x052F or
            0x2DE0 <= cp <= 0x2DFF or
            0xA640 <= cp <= 0xA69F
        )

    @staticmethod
    def _is_arabic(cp: int) -> bool:
        """阿拉伯字母"""
        return (
            0x0600 <= cp <= 0x06FF or
            0x0750 <= cp <= 0x077F or
            0x08A0 <= cp <= 0x08FF or
            0xFB50 <= cp <= 0xFDFF or
            0xFE70 <= cp <= 0xFEFF
        )

    @staticmethod
    def _is_latin(cp: int) -> bool:
        """拉丁字母（英/法/德/意/西共用）"""
        return (
            0x0041 <= cp <= 0x005A or
            0x0061 <= cp <= 0x007A or
            0x00C0 <= cp <= 0x00FF or
            0x0100 <= cp <= 0x024F
        )
