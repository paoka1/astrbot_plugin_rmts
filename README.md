# astrbot_plugin_rmts · 迷迭香工具箱

为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供一组 function calling 工具，配合明日方舟「迷迭香」角色人格使用。
功能移植自作者的 NoneBot2 机器人 [rmts_bot](https://github.com/paoka1/rmts_bot)。

## 功能

| 工具 | 说明 |
|---|---|
| `get_birth_by_date` / `get_birth_by_name` | 按日期查过生日的干员 / 按名字查干员生日 |
| `get_operator_info` | 查询干员档案摘要（377 名干员） |
| `get_weather` | 高德天气，未来 3～4 天预报 |
| `web_search` | 「在终端浏览器上搜索」，调用 DeepSeek Search API |
| `add_doctor_info` / `get_doctor_all_info` | 记下 / 读取某位博士的个人信息（只能记录说话者本人） |
| `add_global_info` / `get_global_all_info` | 记下 / 读取当前群的全局信息 |
| `send_sticker` | 发送表情包（需自行放入表情图片，见下文） |
| `poke_doctor` | 戳一戳指定博士（私聊时只能戳正在聊天的人） |

另外：

- **被戳时触发对话**：有人戳机器人时，以「博士（TA的名字是：xx，TA的ID是xx）戳了戳你」等随机说法交给模型，接着当前对话回复；群聊中回复会 @ 戳的人
- **发送者信息**：在发给模型的每条消息前拼上「博士（TA的名字是：xx，TA的ID是xx）对你说：…」，群聊优先使用群名片

记忆按会话隔离：群聊按群号，私聊按 QQ 号，每次写入立即保存。

## 要求

- AstrBot ≥ 4.29
- 消息平台：aiocqhttp（OneBot v11，例如 NapCat、SnowLuma）。戳一戳和取昵称使用 OneBot 接口，其他平台不可用
- 网页搜索需要 DeepSeek API Key（复用 AstrBot 中已配置的 DeepSeek 模型提供商）

## 配置

在 AstrBot WebUI 的插件配置中设置：

| 配置项 | 说明 |
|---|---|
| `amap_weather_api_key` | 高德开放平台「Web服务」Key，留空则天气不可用 |
| `search_provider_id` | 网页搜索复用哪个 DeepSeek 模型提供商的 Key，例如 `deepseek/deepseek-v4-pro`；留空则用当前会话的模型提供商 |
| `sender_info_enabled` | 是否在消息前拼上发送者名字和 QQ 号，默认开启。关闭后请开启 AstrBot 的「用户识别」，否则戳一戳、个人记忆拿不到 QQ 号 |
| `memory_max_length` | 每份记忆的最大总字数，默认 300，超出时丢弃最早的 |

## 表情包

插件不附带表情图片。把 PNG 图片放到 AstrBot 的 `data/plugin_data/astrbot_plugin_rmts/stickers/` 目录，**文件名就是表情名**（例如 `摸摸博士.png`），然后在 WebUI 重载插件，模型即可按名字发送。目录为空时模型不会调用该工具。

## 使用提示

- 插件的工具说明里写好了使用规则（如查天气缺地点时先读记忆、干员用正式名称、查询结果不记入记忆），人格提示词里不需要再写函数调用规则
- DeepSeek API 中只有 `deepseek-flash` 支持图片输入。主模型用 `deepseek-v4-pro` 时，可在 AstrBot 中把 `deepseek-flash` 设为「默认图片转述模型」，并去掉主模型的图片模态

## 数据来源与致谢

- [rmts_bot](https://github.com/paoka1/rmts_bot)：本插件的原始实现
- [ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)：干员档案原始数据，经 LLM 总结为 `resources/operators_info.json`
- [PRTS wiki](https://prts.wiki/)：干员生日数据 `resources/birthday.json`
- [amap-weather](https://github.com/Simoon-F/amap-weather)：天气查询实现参考
- [高德开放平台](https://lbs.amap.com/)、[DeepSeek](https://api-docs.deepseek.com/zh-cn/)：天气与搜索服务

《明日方舟》相关的角色、文本等内容版权归上海鹰角网络科技有限公司所有。本项目为非官方的爱好者作品，仅供学习交流，请勿用于商业用途。

## 许可证

[AGPL-3.0](LICENSE)
