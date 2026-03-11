# Changelog

## v2.0.1

- 新增管理员指令 `mls_install_langdetect`：在 AstrBot 运行环境中一键安装 `langdetect`，并带有超时与终止处理，避免云端/容器环境卡住。
- 语言检测与分段逻辑增强：`DetectStep` 输出带语言标签的分段结果，提升对英文/德文等拉丁语系文本的分段效果。
- 上下文（历史）省 token 优化：发送给用户仍会展示所有语言分段，但写入 LLM 上下文时可配置只保留一种语言分段。
  - 新增配置：`history_single_lang`（是否开启仅历史单语保留）
  - 新增配置：`history_keep_lang`（保留语言：`auto` 或指定语言）
- 兼容性修复：适配 Python 3.8/3.9 的类型标注与数据结构实现。
