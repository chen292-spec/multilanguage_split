# 多语言分段发送插件 (multilanguage_split)

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，自动识别机器人回复中的不同语言，并将每种语言分成独立的对话框发送。

## 功能特性

- **多语言自动识别**：支持中文、日语、韩语、英语、法语、德语、俄语、西班牙语、意大利语等语言
- **分对话框发送**：不同语言的内容会分成多条消息依次发送
- **Emoji/颜文字分离**：emoji 和颜文字（如 `(≧▽≦)` `╮(╯▽╰)╭`）会被单独分出来发送
- **行内 Emoji 拆分**：文本末尾的 emoji（如 `你好世界😊✨`）也会被自动拆分
- **全球 55 种语言**：安装 `langdetect` 后支持英/法/德/西/意/俄/阿拉伯/印地/泰/希腊/波斯等 55 种语言
- **可配置延迟**：消息之间可设置发送延迟，模拟打字效果
- **引用回复**：第一条消息可自动引用用户的原始消息

## 使用场景

当你让机器人输出多种语言的翻译时，例如：

```
你好，今天天气真好！
안녕하세요, 오늘 날씨가 정말 좋아요!
こんにちは、今日はいい天気ですね！
😊🌸✨
```

插件会自动将以上内容拆分为 **4条独立消息** 依次发送。

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `delay` | 每条消息之间的发送延迟（秒） | `1.0` |
| `enable_reply` | 第一条消息是否引用原消息 | `true` |
| `split_inline_emoji` | 是否拆分文本行末尾的emoji | `true` |
| `enable_langdetect` | 是否启用 langdetect（55种语言） | `true` |
| `split_scope` | 作用范围：`llm_only`(仅LLM回复) 或 `all`(所有消息) | `llm_only` |

## 可选依赖

安装后可识别全球 55 种语言（含英/法/德/西/意/葡/俄/阿拉伯/印地/泰/希腊/希伯来/波斯等）：

```bash
pip install langdetect
```

不安装也能正常工作，会回退到 Unicode 检测，可区分中/日/韩/拉丁/西里尔/阿拉伯等文字系统。

## 语言识别原理

插件使用两层检测策略：

### 主检测器：langdetect（需安装）

安装 `langdetect` 后作为主检测器，直接识别 55 种语言，包括英语、法语、德语、西班牙语、意大利语、葡萄牙语、俄语、阿拉伯语、印地语、泰语、希腊语、希伯来语、波斯语、越南语等。

### 兜底方案：Unicode 字符范围（无需依赖）

未安装 langdetect 或检测失败时自动回退，可区分：

- **中文**：CJK 统一表意文字（当该行没有日语假名时）
- **日语**：平假名/片假名（该行汉字也归为日语）
- **韩语**：韩文字母
- **俄语**：西里尔字母
- **阿拉伯语**：阿拉伯字母
- **拉丁语系**：英/法/德/意/西等统一归为一类（无法细分）
- **Emoji/颜文字**：emoji + 颜文字模式匹配

## 致谢

本插件的开发参考了以下项目，感谢作者们的开源贡献：

- [astrbot_plugin_custome_segment_reply](https://github.com/LinJohn8/astrbot_plugin_custome_segment_reply) - 自定义分段回复插件
- [astrbot_plugin_splitter](https://github.com/nuomicici/astrbot_plugin_splitter) - 消息分割插件

## 相关链接

- [AstrBot 项目](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
