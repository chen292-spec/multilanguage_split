from __future__ import annotations
"""配置模块。

将 AstrBot 的配置字典转换为强类型的 Python 对象，方便各步骤读取。
学习自 outputpro 的 ConfigNode 模式，但做了简化：
- 不使用复杂的元类/描述符，直接用 __init__ 读取字典
- 每个配置类对应 _conf_schema.json 中的一个 object 节点
"""

from astrbot.api import logger


class PipelineConfig:
    """管道配置。

    对应 _conf_schema.json 中的 "pipeline" 节点。
    控制哪些步骤启用、执行顺序、哪些步骤仅对 LLM 生效。
    """

    # 内置的默认步骤顺序（当 lock_order=True 时使用）
    DEFAULT_ORDER = ["clean", "detect", "send"]

    def __init__(self, data: dict):
        # 是否锁定步骤顺序（锁定时按 DEFAULT_ORDER 执行）
        self.lock_order: bool = bool(data.get("lock_order", True))

        # 用户在 UI 上勾选的步骤列表，格式如 "clean(文本清洗)"
        raw_steps = data.get("steps", [])
        # 提取英文 key：把 "clean(文本清洗)" 变成 "clean"
        self._steps: list = [name.split("(", 1)[0].strip() for name in raw_steps]

        # 关键保底：如果没有配置任何步骤（首次加载/旧配置迁移），默认全部启用
        if not self._steps:
            self._steps = list(self.DEFAULT_ORDER)
            logger.info("[MultiLangSplit] 未检测到步骤配置，已默认启用全部步骤")

        # 仅对 LLM 回复生效的步骤
        raw_llm_steps = data.get("llm_steps", [])
        self._llm_steps: list = [name.split("(", 1)[0].strip() for name in raw_llm_steps]

        # 保底：如果 llm_steps 为空，默认所有步骤都仅对 LLM 生效
        if not self._llm_steps:
            self._llm_steps = list(self.DEFAULT_ORDER)

    def is_enabled(self, step_name: str) -> bool:
        """判断某个步骤是否被用户启用（勾选）。"""
        return step_name in self._steps

    def is_llm_only(self, step_name: str) -> bool:
        """判断某个步骤是否仅对 LLM 回复生效。"""
        return step_name in self._llm_steps


class CleanConfig:
    """文本清洗配置。

    对应 _conf_schema.json 中的 "clean" 节点。
    控制在语言检测前对文本的"美容处理"。
    """

    def __init__(self, data: dict):
        # 文本长度阈值：超过此长度不清洗（防止误伤长文本），0 表示不限制
        self.text_threshold: int = int(data.get("text_threshold", 150))
        # 是否摘除中括号内容 [...]（如 [思考中]、[翻译]）
        self.bracket: bool = bool(data.get("bracket", True))
        # 是否摘除小括号内容 (...)，支持全角括号
        self.parenthesis: bool = bool(data.get("parenthesis", False))
        # 是否摘除情绪标签 &&...&&（某些角色扮演模型会输出）
        self.emotion_tag: bool = bool(data.get("emotion_tag", True))
        # 是否清除 emoji（需要 emoji 库）
        self.emoji: bool = bool(data.get("emoji", False))
        # 要去除的句首字符列表
        self.lead: list = list(data.get("lead", []))
        # 要去除的句尾字符列表
        self.tail: list = list(data.get("tail", []))
        # 整体清洗的正则表达式（如 "[#%~]"）
        self.punctuation: str = str(data.get("punctuation", ""))


class DetectConfig:
    """多语言检测配置。

    对应 _conf_schema.json 中的 "detect" 节点。
    控制语言识别行为。
    """

    def __init__(self, data: dict):
        # 是否启用 langdetect 库（支持 55 种语言）
        self.enable_langdetect: bool = bool(data.get("enable_langdetect", True))
        # 是否拆分文本行末尾的 emoji
        self.split_inline_emoji: bool = bool(data.get("split_inline_emoji", True))


class SendConfig:
    """智能发送配置。

    对应 _conf_schema.json 中的 "send" 节点。
    合并了"分段发送"和"合并转发"功能：
    - 每段独立判断：短段直接发送，长段合并转发
    """

    def __init__(self, data: dict):
        # 每条消息之间的发送延迟（秒），模拟打字效果
        try:
            self.delay: float = float(data.get("delay", 1.0))
        except (ValueError, TypeError):
            self.delay = 1.0
        # 第一条消息是否引用用户的原消息
        self.enable_reply: bool = bool(data.get("enable_reply", True))

        # 仅写入历史时保留一种语言（用于减少后续上下文 token），不影响用户看到的多语言分段发送
        self.history_single_lang: bool = bool(data.get("history_single_lang", False))
        # 选择保留语言：
        # - "auto": 自动选择占比最大的语言（排除 emoji）
        # - 或者填写具体语言码/类型：en/de/fr/zh-cn/latin/chinese...
        self.history_keep_lang: str = str(data.get("history_keep_lang", "auto")).strip() or "auto"
        # 历史保留包含表情
        self.history_include_emoji: bool = bool(data.get("history_include_emoji", True))

        # 合并转发长度阈值：单段文本超过此长度就用合并转发发送
        # 设为 0 表示禁用合并转发（所有段都直接发送）
        self.forward_threshold: int = int(data.get("forward_threshold", 500))
        # 合并转发节点昵称（留空则自动获取 bot 昵称）
        self.forward_node_name: str = str(data.get("forward_node_name", ""))


class PluginConfig:
    """插件总配置。

    汇总所有子配置，作为唯一的配置入口传递给 Pipeline 和各 Step。
    """

    def __init__(self, raw_config: dict):
        """
        参数：
            raw_config: AstrBot 传入的插件配置字典
        """
        # 检测是否为 v1.x 旧配置格式（扁平结构，顶层有 delay/enable_reply 等）
        is_old_format = "delay" in raw_config and "pipeline" not in raw_config
        if is_old_format:
            raw_config = self._migrate_v1_config(raw_config)
            logger.info("[MultiLangSplit] 检测到 v1.x 旧配置，已自动迁移到 v2.0 格式")

        self.pipeline = PipelineConfig(raw_config.get("pipeline", {}))
        self.clean = CleanConfig(raw_config.get("clean", {}))
        self.detect = DetectConfig(raw_config.get("detect", {}))
        self.send = SendConfig(raw_config.get("send", {}))

        enabled = [s for s in self.pipeline._steps]
        logger.info(f"[MultiLangSplit] 已启用步骤: {enabled}")

    @staticmethod
    def _migrate_v1_config(old: dict) -> dict:
        """将 v1.x 的扁平配置迁移到 v2.0 的嵌套结构。

        v1.x 格式（顶层）：delay, enable_reply, split_inline_emoji, enable_langdetect, split_scope
        v2.0 格式（嵌套）：pipeline.steps, detect.enable_langdetect, send.delay 等
        """
        new: dict = {
            # 管道配置：默认全部启用
            "pipeline": {},
            # 检测配置：从旧配置迁移
            "detect": {
                "enable_langdetect": old.get("enable_langdetect", True),
                "split_inline_emoji": old.get("split_inline_emoji", True),
            },
            # 发送配置：从旧配置迁移
            "send": {
                "delay": old.get("delay", 1.0),
                "enable_reply": old.get("enable_reply", True),
            },
            # 清洗配置：v1.x 没有，使用默认值
            "clean": {},
        }
        return new
