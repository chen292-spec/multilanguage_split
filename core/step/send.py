"""智能发送步骤。

消费 ctx.segments（由 detect 步骤生成），逐段发送：
- 短段（≤ forward_threshold）→ 直接发送
- 长段（> forward_threshold）→ 合并转发（仅 aiocqhttp 平台支持）
- forward_threshold 设为 0 表示禁用合并转发，所有段都直接发送

如果 ctx.segments 为空（单语言），本步骤不做任何事。
"""

import asyncio

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Reply

from ..config import PluginConfig
from ..model import OutContext, StepName, StepResult
from .base import BaseStep

# 合并转发需要的组件，可能不是所有环境都有
try:
    from astrbot.api.message_components import Node, Nodes
    FORWARD_AVAILABLE = True
except ImportError:
    try:
        from astrbot.core.message.components import Node, Nodes
        FORWARD_AVAILABLE = True
    except ImportError:
        FORWARD_AVAILABLE = False

# 检查是否为 aiocqhttp 平台（合并转发仅此平台可用）
try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
    AIOCQHTTP_AVAILABLE = True
except ImportError:
    AIOCQHTTP_AVAILABLE = False


class SendStep(BaseStep):
    """智能发送步骤。

    对每个语言段独立判断发送方式：
    - 短段 → event.send() 直接发送
    - 长段 → 包装为合并转发节点发送（仅 aiocqhttp）
    - 最后一段放回 result.chain 由框架正常发送（保留对话历史）
    """
    name = StepName.SEND

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self.cfg = config.send
        self._node_name: str = self.cfg.forward_node_name

    async def handle(self, ctx: OutContext) -> StepResult:
        """逐段发送。"""
        segments = ctx.segments

        # 没有分段结果，说明是单语言，不需要分段发送
        if not segments or len(segments) <= 1:
            return StepResult()

        logger.info(
            f"[MultiLangSplit] 开始分段发送，共 {len(segments)} 段"
        )

        # 清空原始消息链
        result = ctx.event.get_result()
        result.chain.clear()

        # 发送前 N-1 段
        for i, seg_text in enumerate(segments[:-1]):
            is_first = (i == 0)
            await self._send_segment(ctx, seg_text, is_first)
            await asyncio.sleep(self.cfg.delay)

        # 最后一段放回 result.chain，由框架正常发送（保留对话历史）
        last_seg = segments[-1]
        if self._should_forward(ctx, last_seg):
            # 最后一段也需要转发时，包装为转发节点放回
            node_comp = await self._build_forward_node(ctx, last_seg)
            if node_comp:
                result.chain.append(node_comp)
            else:
                result.chain.append(Plain(last_seg))
        else:
            result.chain.append(Plain(last_seg))

        return StepResult(
            msg=f"[MultiLangSplit] 分段发送完成，共 {len(segments)} 段"
        )

    async def _send_segment(
        self, ctx: OutContext, text: str, is_first: bool
    ) -> None:
        """发送单个分段。根据长度决定直发还是合并转发。"""
        try:
            if self._should_forward(ctx, text):
                # 长段 → 合并转发
                await self._send_as_forward(ctx, text)
            else:
                # 短段 → 直接发送
                mc = MessageChain()
                if is_first and self.cfg.enable_reply and ctx.event.message_obj.message_id:
                    mc.chain.append(Reply(id=ctx.event.message_obj.message_id))
                mc.chain.append(Plain(text))
                await ctx.event.send(mc)
        except Exception as e:
            logger.error(f"[MultiLangSplit] 发送分段失败: {e}")

    def _should_forward(self, ctx: OutContext, text: str) -> bool:
        """判断某段是否应该用合并转发。"""
        # 转发功能总开关：阈值为 0 表示禁用
        if self.cfg.forward_threshold <= 0:
            return False
        # 文本长度未超过阈值
        if len(text) <= self.cfg.forward_threshold:
            return False
        # 合并转发仅 aiocqhttp 平台支持
        if not FORWARD_AVAILABLE or not AIOCQHTTP_AVAILABLE:
            return False
        if not isinstance(ctx.event, AiocqhttpMessageEvent):
            return False
        return True

    async def _send_as_forward(self, ctx: OutContext, text: str) -> None:
        """将文本包装为合并转发节点并发送。"""
        node_comp = await self._build_forward_node(ctx, text)
        if node_comp:
            mc = MessageChain()
            mc.chain.append(node_comp)
            await ctx.event.send(mc)
        else:
            # 回退：无法构建转发节点时直接发送
            mc = MessageChain()
            mc.chain.append(Plain(text))
            await ctx.event.send(mc)

    async def _build_forward_node(self, ctx: OutContext, text: str):
        """构建合并转发节点。"""
        if not FORWARD_AVAILABLE:
            return None

        name = await self._ensure_node_name(ctx)
        bot_id = ctx.event.get_self_id()

        nodes = Nodes([])
        nodes.nodes.append(
            Node(uin=bot_id, name=name, content=[Plain(text)])
        )
        return nodes

    async def _ensure_node_name(self, ctx: OutContext) -> str:
        """获取转发节点昵称。优先用配置值，否则尝试获取 bot 昵称。"""
        if self._node_name:
            return self._node_name

        if AIOCQHTTP_AVAILABLE and isinstance(ctx.event, AiocqhttpMessageEvent):
            try:
                info = await ctx.event.bot.get_login_info()
                if nickname := info.get("nickname"):
                    self._node_name = str(nickname)
                    return self._node_name
            except Exception:
                pass

        self._node_name = "AstrBot"
        return self._node_name
