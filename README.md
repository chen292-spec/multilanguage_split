# 多语言分段发送插件 (multilanguage_split)

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，自动识别机器人回复中的不同语言，将每种语言分成独立的对话框发送。采用管道架构，每个步骤可独立开关、自由调节顺序。

## 管道架构

```text
文本清洗 → 多语言检测 → 智能发送
                         ├─ 短段 → 直接发送
                         └─ 长段 → 合并转发
```

| 步骤 | 功能 | 说明 |
| --- | --- | --- |
| **clean** | 文本清洗 | 去括号、去情绪标签、去特殊符号等，在语言检测前清理噪声 |
| **detect** | 多语言检测 | 识别 55 种语言并拆分为多段，emoji/颜文字单独分离 |
| **send** | 智能发送 | 每段独立判断：短段直接发送，长段合并转发（仅 QQ 平台） |

- 想改顺序？把 `pipeline.lock_order` 设为 `false`，然后在 UI 拖拽
- 想关掉某步骤？在 `pipeline.steps` 中取消勾选
- 想让某步骤只对 LLM 生效？在 `pipeline.llm_steps` 中勾选

## 功能特性

- **管道架构**：步骤可独立开关、自由调节顺序
- **文本清洗**：去 `[...]`、`(...)`、`&&...&&`、emoji、句首句尾字符、正则清洗
- **多语言自动识别**：支持中/日/韩/英/法/德/俄/西/意等 55 种语言
- **智能发送**：短段直发、长段自动合并转发，每段独立判断
- **Emoji/颜文字分离**：emoji 和颜文字（如 `(≧▽≦)` `╮(╯▽╰)╭`）单独发送
- **行内 Emoji 拆分**：文本末尾的 emoji（如 `你好世界😊✨`）自动拆分
- **可配置延迟**：消息之间可设置发送延迟，模拟打字效果
- **引用回复**：第一条消息可自动引用用户的原始消息

## 使用场景

当你让机器人输出多种语言的翻译时，例如：

```text
你好，今天天气真好！
The AI world has been quite lively recently. Jensen Huang said...（很长的英文段落）
Die KI-Welt war in letzter Zeit recht lebhaft...（很长的德文段落）
😊🌸✨
```

插件会：

1. 清洗文本噪声（如果开启了清洗步骤）
2. 识别出中文、英文、德文、emoji 四种语言
3. 中文短 → 直接发送；英文/德文长 → 合并转发；emoji → 直接发送

## 依赖

插件已通过 `requirements.txt` 内置 `langdetect` 依赖，AstrBot 安装插件时会自动安装。

如果自动安装失败，可手动执行：

```bash
pip install langdetect
```

未安装 `langdetect` 时仍可工作，但回退到 Unicode 检测，**无法区分同为拉丁字母的语言（如英语和德语）**。

## 语言识别原理

插件使用两层检测策略：

### 主检测器：langdetect（内置）

直接识别 55 种语言，包括英/法/德/西/意/葡/俄/阿拉伯/印地/泰/希腊/希伯来/波斯/越南语等。

### 兜底方案：Unicode 字符范围

未安装 langdetect 或检测失败时自动回退，可区分：

- **中文**：CJK 统一表意文字（当该行没有日语假名时）
- **日语**：平假名/片假名（该行汉字也归为日语）
- **韩语**：韩文字母
- **俄语**：西里尔字母
- **阿拉伯语**：阿拉伯字母
- **拉丁语系**：英/法/德/意/西等统一归为一类（无法细分）
- **Emoji/颜文字**：emoji + 颜文字模式匹配

## 致谢

本插件的开发参考了以下项目，感谢：

- [astrbot_plugin_outputpro](https://github.com/Zhalslar/astrbot_plugin_outputpro) - 输出增强插件（管道架构、文本清洗、合并转发）
- [astrbot_plugin_custome_segment_reply](https://github.com/LinJohn8/astrbot_plugin_custome_segment_reply) - 本地规则智能分段插件
- [astrbot_plugin_splitter](https://github.com/nuomicici/astrbot_plugin_splitter) - 对话分段Pro

## 相关链接

- [AstrBot 项目](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
