"""多语言分段发送插件 - 入口文件。

管道架构：clean(文本清洗) → detect(多语言检测) → send(智能发送)
每个步骤可独立开关、自由调节顺序。
"""

import asyncio
import sys

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain
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

    @filter.command("mls_install_langdetect")
    async def install_langdetect(self, event: AstrMessageEvent):
        """一键安装 langdetect（仅管理员可用）。

        说明：
        - 通过 sys.executable 调用当前 AstrBot 正在运行的 Python
        - 用 python -m pip install langdetect 安装到“正确的环境”
        - Docker/云服务器/本机均适用
        """
        if not event.is_admin():
            await event.send(MessageChain([Plain("无权限：该指令仅管理员可用")]))
            return

        # 已安装则直接提示
        try:
            import langdetect  # noqa: F401
            await event.send(MessageChain([Plain("langdetect 已安装，无需重复安装")]))
            return
        except Exception:
            pass

        await event.send(MessageChain([Plain("开始安装 langdetect，请稍等... ")]))

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "langdetect",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            output = (out or b"").decode("utf-8", errors="ignore")

            if proc.returncode == 0:
                await event.send(
                    MessageChain(
                        [Plain("安装完成：langdetect 已可用。建议在 WebUI 重载插件或重启 AstrBot。")]
                    )
                )
            else:
                # 输出太长会刷屏，这里截断显示最后一部分
                tail = output[-1500:] if output else ""
                await event.send(
                    MessageChain(
                        [Plain("安装失败（请检查网络/镜像源/权限）。日志末尾：\n" + tail)]
                    )
                )
        except Exception as e:
            await event.send(MessageChain([Plain(f"安装异常：{e}")]))

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
