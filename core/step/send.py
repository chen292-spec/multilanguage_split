"""智能发送步骤。

消费 ctx.segments（由 detect 步骤生成），逐段发送：
- 短段（≤ forward_threshold）→ 直接发送
- 长段（> forward_threshold）→ 合并转发（仅 aiocqhttp 平台支持）
- forward_threshold 设为 0 表示禁用合并转发，所有段都直接发送

如果 ctx.segments 为空（单语言），本步骤不做任何事。
"""

import asyncio
from typing import Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Reply

from ..config import PluginConfig
from ..model import OutContext, Segment, StepName, StepResult
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

        # 是否启用"仅写入历史的单语保留"（只针对机器人/LLM 回复）
        keep_index: Optional[int] = None
        if ctx.is_llm and self.cfg.history_single_lang:
            keep_index = self._choose_history_segment_index(segments)

        logger.debug(
            "[MultiLangSplit] SendStep: "
            f"is_llm={ctx.is_llm}, history_single_lang={self.cfg.history_single_lang}, "
            f"history_keep_lang={self.cfg.history_keep_lang!r}, keep_index={keep_index}"
        )

        try:
            seg_brief = ", ".join(
                [
                    f"{i}:{(s.lang or 'other')}({len(s.text)})"
                    for i, s in enumerate(segments)
                ]
            )
            logger.debug(f"[MultiLangSplit] SendStep segments: {seg_brief}")
        except Exception as e:
            logger.warning(f"[MultiLangSplit] SendStep segments log failed: {e}")

        logger.debug(
            f"[MultiLangSplit] 开始分段发送，共 {len(segments)} 段"
        )

        # ===== 核心修复：修改 LLM 历史，仅保留选中语言的文本 =====
        # AstrBot 的对话历史从 LLMResponse.completion_text 保存，
        # 而不是从 result.chain 保存。所以必须直接修改 LLMResponse 对象。
        if keep_index is not None:
            llm_resp = getattr(ctx.event, "__llm_resp", None)  # 由 on_llm_response 保存
            if llm_resp is not None:
                keep_text_for_history = segments[keep_index].text
                try:
                    llm_resp.completion_text = keep_text_for_history
                    logger.debug(
                        f"[MultiLangSplit] 已修改 LLM 历史，仅保留 "
                        f"lang={segments[keep_index].lang}, "
                        f"len={len(keep_text_for_history)}"
                    )
                except Exception as e:
                    logger.warning(f"[MultiLangSplit] 修改 LLM 历史失败: {e}")
            else:
                logger.debug(
                    "[MultiLangSplit] 未找到 __llm_resp，无法修改 LLM 历史"
                )

        # 清空原始消息链
        result = ctx.event.get_result()
        result.chain.clear()

        # 发送策略：
        # 1) 用户看到的顺序保持与 segments 一致
        # 2) 仅 keep_index 对应的分段写入 result.chain（由框架发送）
        #
        # - keep_index 之前的段：手动发送
        # - keep_index 这段：放到 result.chain 交给框架发送
        # - keep_index 之后的段：后台 task 在框架发送后继续手动发送

        if keep_index is None:
            keep_index = len(segments) - 1

        # 先发送 keep_index 之前的段
        first_sent = True
        for i in range(0, keep_index):
            await self._send_segment(ctx, segments[i].text, first_sent)
            first_sent = False
            await asyncio.sleep(self.cfg.delay)

        # 将 keep_index 对应段写回 result.chain，由框架发送并进入历史
        keep_text = segments[keep_index].text
        if self._should_forward(ctx, keep_text):
            node_comp = await self._build_forward_node(ctx, keep_text)
            if node_comp:
                # 第一条消息需要引用时，尽量在框架发送的这一条也加 Reply
                if first_sent and self.cfg.enable_reply and ctx.event.message_obj.message_id:
                    result.chain.append(Reply(id=ctx.event.message_obj.message_id))
                result.chain.append(node_comp)
            else:
                if first_sent and self.cfg.enable_reply and ctx.event.message_obj.message_id:
                    result.chain.append(Reply(id=ctx.event.message_obj.message_id))
                result.chain.append(Plain(keep_text))
        else:
            if first_sent and self.cfg.enable_reply and ctx.event.message_obj.message_id:
                result.chain.append(Reply(id=ctx.event.message_obj.message_id))
            result.chain.append(Plain(keep_text))

        # keep_index 之后的段：后台继续发，尽量保证发生在框架发送 keep 段之后
        if keep_index < len(segments) - 1:
            asyncio.create_task(
                self._send_after_framework(ctx, segments, keep_index + 1)
            )

        return StepResult(
            msg=f"[MultiLangSplit] 分段发送完成，共 {len(segments)} 段"
        )

    def _choose_history_segment_index(self, segments: List[Segment]) -> int:
        """选择要写入对话历史的分段（用于减少后续上下文 token）。

        规则：
        - history_keep_lang == "auto": 选择占比最大的语言（按字符数），忽略 emoji
        - 否则：选择最后一个匹配该语言的分段（避免过早截断上下文）
        - 都找不到：回退到最后一段
        """
        keep_lang = (self.cfg.history_keep_lang or "auto").strip().lower()

        if keep_lang == "auto":
            score: Dict[str, int] = {}
            for seg in segments:
                lang = (seg.lang or "other").lower()
                if lang == "emoji":
                    continue
                score[lang] = score.get(lang, 0) + len(seg.text)
            if score:
                target = max(score, key=score.get)
                for i in range(len(segments) - 1, -1, -1):
                    if (segments[i].lang or "").lower() == target:
                        return i
            return len(segments) - 1

        # 精确语言匹配：支持 zh / zh-cn 这种前缀匹配
        for i in range(len(segments) - 1, -1, -1):
            lang = (segments[i].lang or "").lower()
            if (
                lang == keep_lang
                or lang.startswith(keep_lang + "-")
                or keep_lang.startswith(lang + "-")
            ):
                return i

        return len(segments) - 1

    async def _send_after_framework(
        self, ctx: OutContext, segments: List[Segment], start_index: int
    ) -> None:
        """在框架发送 result.chain 之后继续发送剩余分段。

        这里用 create_task + sleep(0) 尽量让发送顺序变成：
        手动发送(keep 之前) -> 框架发送(keep) -> 手动发送(keep 之后)
        """
        try:
            await asyncio.sleep(0)
            for i in range(start_index, len(segments)):
                await self._send_segment(ctx, segments[i].text, False)
                await asyncio.sleep(self.cfg.delay)
        except Exception as e:
            logger.error(f"[MultiLangSplit] 发送后续分段失败: {e}")

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
