"""管道调度器模块。

Pipeline 是插件的中枢，负责：
1. 根据配置构建步骤实例（支持锁定顺序 / 自定义顺序）
2. 统一管理步骤的生命周期（initialize / terminate）
3. 按顺序执行各步骤，支持中断和 LLM 限制

学习自 outputpro 的 Pipeline 设计。
"""

from astrbot.api import logger

from .config import PluginConfig
from .model import OutContext
from .step import BaseStep, CleanStep, DetectStep, SendStep


class Pipeline:
    """管道调度器。

    STEP_REGISTRY 定义了内置默认顺序。
    当 lock_order=True 时按此顺序执行；
    当 lock_order=False 时按用户在 UI 拖拽的顺序执行。
    """

    # 内置默认顺序：步骤名 → 步骤类
    STEP_REGISTRY: list[tuple[str, type[BaseStep]]] = [
        ("clean", CleanStep),      # 文本清洗
        ("detect", DetectStep),    # 多语言检测
        ("send", SendStep),        # 智能发送
    ]

    def __init__(self, config: PluginConfig):
        self.config = config
        self.cfg = config.pipeline
        self._steps: list[BaseStep] = []
        self._build_steps()

    def _build_steps(self) -> None:
        """根据配置构建步骤实例。

        lock_order=True: 按 STEP_REGISTRY 内置顺序，只实例化用户勾选的步骤
        lock_order=False: 按用户 UI 列表的顺序实例化
        """
        if self.cfg.lock_order:
            # 锁定顺序：按内置顺序遍历，只创建用户启用的步骤
            for name, cls in self.STEP_REGISTRY:
                if name in self.cfg._steps:
                    self._steps.append(cls(self.config))
        else:
            # 自定义顺序：按用户拖拽的顺序创建
            step_map = dict(self.STEP_REGISTRY)
            for name in self.cfg._steps:
                cls = step_map.get(name)
                if not cls:
                    logger.warning(f"[MultiLangSplit] 未知步骤: {name}")
                    continue
                self._steps.append(cls(self.config))

        step_names = [s.name.value for s in self._steps]
        logger.info(f"[MultiLangSplit] 管道已构建，执行顺序: {step_names}")

    # ==================== 生命周期 ====================

    async def initialize(self) -> None:
        """初始化所有步骤。"""
        for step in self._steps:
            await step.initialize()

    async def terminate(self) -> None:
        """终止所有步骤。"""
        for step in self._steps:
            await step.terminate()

    # ==================== 执行 ====================

    def _llm_allow(self, step: BaseStep, is_llm: bool) -> bool:
        """检查步骤的 LLM 限制。

        如果某步骤被标记为"仅 LLM 生效"，则非 LLM 消息会跳过它。
        """
        if self.cfg.is_llm_only(step.name):
            return is_llm
        return True

    async def run(self, ctx: OutContext) -> bool:
        """执行管道中的所有步骤。

        返回 True 表示正常完成，False 表示被某步骤中断。
        """
        for step in self._steps:
            # 检查 LLM 限制
            if not self._llm_allow(step, ctx.is_llm):
                continue

            # 执行步骤
            result = await step.handle(ctx)

            # 记录日志
            if result.msg:
                if result.ok:
                    logger.debug(result.msg)
                else:
                    logger.warning(result.msg)

            # 中断检查
            if result.abort:
                return False

        return True
