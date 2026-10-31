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
| `add_doctor_info` / `get_doctor_all_info` | 记下本人信息 / 按可见性规则查询博士画像 |
| `forget_doctor_info` | 应博士要求，按编号忘掉本人的事实 |
| `add_global_info` | 记下本群信息，私聊时记入本人的画像 |
| `search_diary` / `read_diary` | 按关键词搜索 / 按日期翻阅当前群或私聊的日记 |
| `send_sticker` | 发送迷迭香表情包（自带 39 个，可自行追加） |
| `poke_doctor` | 戳一戳指定博士（私聊时只能戳正在聊天的人） |

另外：

- **被戳时触发对话**：有人戳机器人时，以「博士（TA的名字是：xx，TA的ID是xx）戳了戳你」等随机说法交给模型，接着当前对话回复；群聊中回复会 @ 戳的人
- **发送者信息**：在发给模型的每条消息前拼上「博士（TA的名字是：xx，TA的ID是xx）对你说：…」，群聊优先使用群名片

## 记忆与日记

每位博士有一份跟人走的画像，保存名字、印象和带日期的事实。每次聊天自动把当前博士、消息里 @ 或回复到的博士（最多 3 人）、本群信息及最近一篇日记放入本次请求，省去主动查询；这些记录独立存储，不受 AstrBot 对话历史的截断、压缩或 `/reset` 影响。

本人在群里说话时，可以使用在各群得知的非私聊事实；别人提到或查询这位博士时，只能读到在本群得知的非私聊事实。私聊得知的事实只在和这位博士私聊时使用，私聊里不能查别人的画像。群信息和日记只在当前群或私聊内可见。

插件只记录文本，不处理图片、表情、语音、文件等内容。每天默认在 Bot 所在时区的 **04:00** 写前一天的日记，再整理画像和群信息。普通群聊只作为日记素材，个人画像只依据直接对迷迭香说的话和她的回复整理。超出字数预算时优先保留与她的对话，其余群聊均匀抽样；与她的对话也超预算时保留最近的部分。画像超限时白天照常记下，留待夜间合并或清理，避免丢弃最早的硬事实。

数据保存在 `data/plugin_data/astrbot_plugin_rmts/`：`memory.json` 为画像和群信息，`logs/` 为待整理的记录，`diaries/` 为按作用域分开的日记。每个作用域最多读取最近 5000 条记录，文件超过 10000 行时收紧到最近 5000 行，避免连续失败时无限堆积。夜间整理失败会保留待整理记录和已完成的结果，下次继续处理；插件禁用或重载会取消任务。旧的 `rosmontis_memory_*.json` 首次加载时自动迁移，原文件改名为 `.json.bak` 保留。

### WebUI 页面

在 AstrBot WebUI 的插件详情中打开 `memory` 页面，可以查看：

- **博士画像**：每位博士的名字、印象和事实，带日期和来源（私聊 / 哪个群），超过字数上限会标出
- **群信息**、**日记**（按群或私聊、按关键词筛选）、**待整理**（每个群或私聊攒了多少条记录）
- **夜间整理状态**：下次运行时间、上次结果和失败原因

「立即整理」按钮会马上为有新记录的群和私聊写日记、整理记忆，不用等到 `diary_time`。同一天再次整理时会续写当天的日记，不会覆盖。页面只读，只有能登录 WebUI 的人可以看到，其中包含私聊得知的信息。

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
| `persona_sync_enabled` | 是否自动创建和更新人格「迷迭香（rmts）」，默认开启；改过的人格不会被覆盖 |
| `memory_max_length` | 每位博士画像 / 每个群信息的字数上限，默认 500，夜间整理时压到上限内 |
| `diary_time` | 每天写日记和整理记忆的时间，`HH:MM`，默认 `04:00`，按 Bot 所在时区运行 |
| `memory_provider_id` | 夜间整理的模型提供商 ID，留空用当前会话的聊天模型 |
| `diary_inject_count` | 每次对话注入的最近日记篇数，默认 1，0 表示不注入 |
| `diary_input_max_chars` | 夜间读取当天消息的字数预算，默认 20000 |

## 人格

[`persona/rosmontis.md`](persona/rosmontis.md) 是配套的迷迭香人格提示词。插件启动时会把它同步为人格「迷迭香（rmts）」：没有就创建；插件更新了人格、而你没改过时自动换成新版；你在 WebUI 里改过的话不会覆盖，只在日志里提示，需要时对照新版文件自己合并。插件不会切换默认人格，首次安装后请在 AstrBot 配置中把「迷迭香（rmts）」设为默认人格。不想让插件管理人格时，关闭 `persona_sync_enabled`。

插件的工具说明里写有具体使用规则（如查天气缺地点时看终端上的记事、干员用正式名称、查询结果不记入记忆）。人格中的「迷迭香的习惯」让她自然地记下博士的事、想不起往事时翻日记。

夜间写日记使用该会话配置的默认人格，请把迷迭香设为默认人格；不追溯单个对话中手动切换的人格。整理画像使用独立的中性提示词，避免角色口吻影响事实提取。

## 表情包

插件自带 39 个迷迭香表情（`resources/stickers/`），作者为 [CoconutCorn](https://www.pixiv.net/users/64509815)。

想追加或替换表情，把 PNG 图片放到 AstrBot 的 `data/plugin_data/astrbot_plugin_rmts/stickers/` 目录，**文件名就是表情名**（例如 `摸摸博士.png`，与自带表情同名时会覆盖），然后在 WebUI 重载插件，模型即可按名字发送。

## 使用提示

- DeepSeek API 中只有 `deepseek-flash` 支持图片输入。主模型用 `deepseek-v4-pro` 时，可在 AstrBot 中把 `deepseek-flash` 设为「默认图片转述模型」，并去掉主模型的图片模态

## 数据来源与致谢

- [rmts_bot](https://github.com/paoka1/rmts_bot)：本插件的原始实现
- [CoconutCorn](https://www.pixiv.net/users/64509815)：迷迭香表情包作者，表情图片版权归作者所有
- [ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)：干员档案原始数据，经 LLM 总结为 `resources/operators_info.json`
- [PRTS wiki](https://prts.wiki/)：干员生日数据 `resources/birthday.json`
- [amap-weather](https://github.com/Simoon-F/amap-weather)：天气查询实现参考
- [高德开放平台](https://lbs.amap.com/)、[DeepSeek](https://api-docs.deepseek.com/zh-cn/)：天气与搜索服务

《明日方舟》相关的角色、文本等内容版权归上海鹰角网络科技有限公司所有。本项目为非官方的爱好者作品，仅供学习交流，请勿用于商业用途。

## 许可证

代码与人格提示词采用 [AGPL-3.0](LICENSE)。表情图片的版权归 CoconutCorn 所有，人格提示词中引用的游戏台词版权归鹰角网络所有，均不在 AGPL-3.0 授权范围内。
