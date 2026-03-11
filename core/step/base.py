"""步骤基类模块。

所有管道步骤（Step）都必须继承 BaseStep，并实现 handle() 方法。
这是管道架构的核心抽象，学习自 outputpro 的设计模式。
"""

from abc import ABC, abstractmethod

from ..config import PluginConfig
from ..model import OutContext, StepName, StepResult


class BaseStep(ABC):
    """所有步骤的抽象基类。

    子类必须：
    1. 设置类属性 name = StepName.XXX（用于管道识别）
    2. 实现 handle(ctx) 方法（核心处理逻辑）

    可选覆盖：
    - initialize(): 插件启动时调用
    - terminate(): 插件卸载时调用
    """

    # 步骤名称，子类必须覆盖
    name: StepName

    def __init__(self, config: PluginConfig):
        """
        参数：
            config: 插件总配置对象，各子步骤从中取自己需要的配置
        """
        self.plugin_config = config

    @abstractmethod
    async def handle(self, ctx: OutContext) -> StepResult:
        """处理单次步骤的核心逻辑。

        参数：
            ctx: 管道上下文对象，包含消息链、事件、分段结果等

        返回：
            StepResult 对象，告诉管道：
            - ok=True/False: 是否成功
            - abort=True: 中断后续步骤
            - msg: 日志消息
        """
        ...

    async def initialize(self) -> None:
        """插件启动时的初始化（可选覆盖）。"""
        pass

    async def terminate(self) -> None:
        """插件卸载时的清理（可选覆盖）。"""
        pass
