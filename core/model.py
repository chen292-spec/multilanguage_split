"""数据模型模块。

定义管道中使用的核心数据结构：
- StepName: 步骤名称枚举
- StepResult: 步骤执行结果
- OutContext: 管道上下文（在各步骤间传递数据）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import BaseMessageComponent


class StepName(str, Enum):
    """步骤名称枚举。

    每个值对应 _conf_schema.json 中 pipeline.steps 的选项 key。
    继承 str 使得可以直接与字符串比较，如 StepName.CLEAN == "clean"。
    """
    CLEAN = "clean"      # 文本清洗：去噪声、去括号、去情绪标签等
    DETECT = "detect"    # 多语言检测：识别语言并拆分成多段
    SEND = "send"        # 智能发送：短段直发、长段合并转发


@dataclass(slots=True)
class StepResult:
    """步骤执行结果。

    每个 Step 的 handle() 方法都返回此对象，用于：
    - ok: 告诉管道这一步是否成功
    - abort: 是否中断后续步骤（如消息被拦截）
    - msg: 附加日志消息（会被管道自动记录）
    - data: 可携带任意数据传递给下游
    """
    ok: bool = True
    abort: bool = False
    msg: Optional[str] = None
    data: Any = None


@dataclass
class OutContext:
    """管道上下文对象。

    在 on_decorating_result 中创建，随后在管道的每个步骤间传递。
    各步骤可以读取和修改其中的字段。

    字段说明：
    - event: AstrBot 消息事件对象
    - chain: 消息链（消息组件列表），步骤可修改
    - is_llm: 是否为 LLM 生成的回复
    - plain: 纯文本内容（从消息链提取，clean 步骤会更新它）
    - segments: 多语言分段结果（由 detect 步骤填充，send 步骤消费）
    """
    event: AstrMessageEvent
    chain: List[BaseMessageComponent]
    is_llm: bool
    plain: str
    segments: List[str] = field(default_factory=list)
