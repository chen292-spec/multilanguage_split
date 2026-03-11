"""文本清洗步骤。

在语言检测之前对文本进行"美容处理"，去掉 LLM 输出中常见的噪声。
清洗顺序固定为：中括号 → 小括号 → 情绪标签 → emoji → 句首 → 句尾 → 正则清洗
只对短文本（≤ 阈值）执行，避免误伤长文本。

学习自 outputpro 的 CleanStep。
"""

import re
from collections import defaultdict
from typing import DefaultDict, Dict, List

from astrbot.api.message_components import Plain

from ..config import PluginConfig
from ..model import OutContext, StepName, StepResult
from .base import BaseStep

# emoji 库是可选依赖，用于精确清除 emoji
try:
    import emoji as emoji_lib
    EMOJI_LIB_AVAILABLE = True
except ImportError:
    EMOJI_LIB_AVAILABLE = False


class CleanStep(BaseStep):
    """文本清洗步骤。

    遍历消息链中的 Plain 文本组件，按固定顺序逐项清洗。
    清洗完成后会同步更新 ctx.plain，供后续步骤使用。
    """
    name = StepName.CLEAN

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.clean

    async def handle(self, ctx: OutContext) -> StepResult:
        """对消息链中的每个 Plain 组件执行文本清洗。"""

        # removed 记录每种清洗类型删除了什么（用于日志）
        removed: DefaultDict[str, List[str]] = defaultdict(list)

        for seg in ctx.chain:
            # 只处理纯文本组件
            if not isinstance(seg, Plain):
                continue

            # 超过阈值的长文本不清洗（防止误伤）
            if self.cfg.text_threshold > 0 and len(seg.text) >= self.cfg.text_threshold:
                continue

            # --- 按固定顺序逐项清洗 ---

            # 1. 摘除中括号内容 [...]
            if self.cfg.bracket:
                matches = re.findall(r"\[.*?\]", seg.text)
                if matches:
                    removed["中括号内容"].extend(matches)
                    seg.text = re.sub(r"\[.*?\]", "", seg.text)

            # 2. 摘除小括号内容 (...)，支持全角括号
            if self.cfg.parenthesis:
                matches = re.findall(r"[（(].*?[）)]", seg.text)
                if matches:
                    removed["小括号内容"].extend(matches)
                    seg.text = re.sub(r"[（(].*?[）)]", "", seg.text)

            # 3. 摘除情绪标签 &&...&&
            if self.cfg.emotion_tag:
                matches = re.findall(r"&&.*?&&", seg.text)
                if matches:
                    removed["情绪标签"].extend(matches)
                    seg.text = re.sub(r"&&.*?&&", "", seg.text)

            # 4. 清除 emoji（需要 emoji 库）
            if self.cfg.emoji and EMOJI_LIB_AVAILABLE:
                emojis = [c for c in seg.text if c in emoji_lib.EMOJI_DATA]
                if emojis:
                    removed["Emoji"].extend(emojis)
                    seg.text = emoji_lib.replace_emoji(seg.text, replace="")

            # 5. 去除句首字符
            if self.cfg.lead:
                for s in self.cfg.lead:
                    if seg.text.startswith(s):
                        removed["句首字符"].append(s)
                        seg.text = seg.text[len(s):]
                        break  # 只去一个

            # 6. 去除句尾字符
            if self.cfg.tail:
                for s in self.cfg.tail:
                    if seg.text.endswith(s):
                        removed["句尾字符"].append(s)
                        seg.text = seg.text[:-len(s)]
                        break  # 只去一个

            # 7. 正则整体清洗特殊符号
            if self.cfg.punctuation:
                matches = re.findall(self.cfg.punctuation, seg.text)
                if matches:
                    removed["特殊符号"].extend(matches)
                    seg.text = re.sub(self.cfg.punctuation, "", seg.text)

        # 清洗完成后，同步更新 ctx.plain 供后续步骤使用
        updated_text = ""
        for seg in ctx.chain:
            if isinstance(seg, Plain):
                updated_text += seg.text
        ctx.plain = updated_text.strip()

        return StepResult(msg=self._build_msg(removed))

    def _build_msg(self, removed: Dict[str, List[str]]) -> str:
        """构建日志消息，记录清洗了什么内容。"""
        if not removed:
            return ""
        parts: List[str] = []
        for k, items in removed.items():
            uniq = list(dict.fromkeys(items))
            if len(uniq) == 1:
                parts.append(f"{k}: {uniq[0]}")
            else:
                preview = "、".join(uniq[:3])
                more = f" 等{len(uniq)}项" if len(uniq) > 3 else ""
                parts.append(f"{k}: {preview}{more}")
        return "[MultiLangSplit] 文本清洗：" + "；".join(parts)
