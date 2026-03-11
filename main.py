"""多语言分段发送插件 - 入口文件。

管道架构：clean(文本清洗) → detect(多语言检测) → send(智能发送)
每个步骤可独立开关、自由调节顺序。
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse

from .core.config import PluginConfig
from .core.model import OutContext
from .core.pipeline import Pipeline


@register("multilanguage_split", "chen292-spec",
          "多语言分段发送 - 文本清洗 → 多语言检测 → 智能发送(含合并转发)",
          "2.0.0")
class MultiLanguageSplitPlugin(Star):
    """多语言分段发送插件（管道架构 v2）。

    管道默认顺序：
    1. clean  - 文本清洗（去括号、去情绪标签、去特殊符号）
    2. detect - 多语言检测（识别语言并拆分成多段）
    3. send   - 智能发送（短段直发、长段合并转发）

    每个步骤可在 AstrBot WebUI 中独立开关、调节顺序。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 将原始配置字典转换为强类型配置对象
        self.cfg = PluginConfig(config or {})
        # 构建管道（根据配置决定启用哪些步骤、执行顺序）
        self.pipeline = Pipeline(self.cfg)

    async def initialize(self):
        """插件启动时初始化管道中的所有步骤。"""
        await self.pipeline.initialize()

    async def terminate(self):
        """插件卸载时清理管道中的所有步骤。"""
        await self.pipeline.terminate()
        logger.info("[MultiLangSplit] 插件已卸载")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """当 LLM 产生回复时打标记，供管道判断是否为 LLM 消息。"""
        setattr(event, "__is_llm_reply", True)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """消息发送前拦截，交给管道处理。"""
        result = event.get_result()
        if not result or not result.chain:
            return

        # 提取纯文本
        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                raw_text += comp.text
        raw_text = raw_text.strip()
        if not raw_text:
            return

        # 构建管道上下文
        ctx = OutContext(
            event=event,
            chain=result.chain,
            is_llm=getattr(event, "__is_llm_reply", False),
            plain=raw_text,
        )

        # 执行管道
        await self.pipeline.run(ctx)
