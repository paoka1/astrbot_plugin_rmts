# CLAUDE.md

AstrBot 插件 `astrbot_plugin_rmts`（迷迭香工具箱）：为明日方舟「迷迭香」人格提供 function calling 工具、跟人走的博士画像记忆和每日日记。用户文档见 `README.md`，记忆与日记的设计见 `docs/superpowers/specs/2026-10-31-memory-diary-design.md`。

部署到服务器的步骤在 `CLAUDE.local.md`（不提交）。

## 目录

- `main.py`：AstrBot 接入层。钩子、LLM 工具、WebUI 接口、夜间定时循环都在这里
- `core/`：业务模块。`memory`（画像 / 群信息 / 可见性 / 迁移 / 注入文本）、`diary`（当日记录、抽样、日记存储与搜索）、`nightly`（写日记与整理）、`panel`（WebUI 数据汇总）、`persona`（人格同步判断）、`search`、`weather`
- `views/memory/index.html`：WebUI 插件页面
- `persona/rosmontis.md`：配套人格提示词，插件启动时同步为人格「迷迭香（rmts）」
- `resources/`：干员生日、档案、表情包
- `scripts/persona_chat.py`：本地直接调模型试聊人格，可同时对比多个版本（如 `HEAD:persona/rosmontis.md` 和工作区的文件），Key 读环境变量 `DEEPSEEK_API_KEY`；不经过 AstrBot，没有工具和记忆
- `tests/`：pytest，只测 `core/`

## 命令

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

改完代码三条都要过。`main.py` 依赖 AstrBot，本地无法导入，只能在服务器上验证。

## 约定

- `core/` 里的 memory、diary、nightly、panel、persona **不能 import astrbot**，以便本地测试；模块之间用相对导入。测试通过 `tests/conftest.py` 把仓库根注册为包 `rmts`，写成 `from rmts.core.memory import ...`
- 中文注释，日志前缀 `[rmts]`；工具的使用规则写在工具 docstring 里（`Args:` 段的类型写法如 `array[string]`、`number`）
- JSON 写入用「写 `.tmp` 再 `replace`」原子落盘；当日记录是 JSONL 追加写
- 日期一律 `YYYY-MM-DD`，未知日期为 `""`（展示为「很久以前」）
- 作用域：群聊为群号，私聊为 `private_<QQ号>`；事实来源为 `private` 或 `group:<群号>`

## 不能破坏的规则

- **可见性在代码里过滤**，模型看不到的就说不出来（`core/memory.py` 的 `visible_facts`）：本人私聊看全部；本人在群里看非私聊事实；别人提到或查询时只给 `sources` 含当前群的非私聊事实；私聊里查别人什么都不给
- **来源由代码计算，不交给模型**：新增为当前作用域；更新后换成当前作用域；合并时含私聊即为私聊，否则取交集（交集为空只有本人可用）
- 群作用域的夜间整理，模型输入里不能出现私聊事实；对私聊事实的操作一律忽略
- 群消息监听不能 `stop_event()` 或 `yield` 结果
- 夜间整理任一步失败都不删当天记录；同一天再次整理时续写当天日记，不覆盖
- 人格同步只管「迷迭香（rmts）」这一份：用户改过（内容既不是新版也不是插件上次写入的）就不覆盖；不改其他人格，不切换默认人格
- WebUI 页面渲染用户内容只用 `textContent`，不用 `innerHTML`；接口的作用域参数要校验（见 `panel.diary_payload`）

## AstrBot 接口（已对照 4.29.3 源码核对）

- 记忆块拼进 `req.system_prompt`，不会写进对话历史（保存历史时跳过首条 system 消息）
- 夜间调用模型用 `self.context.llm_generate(chat_provider_id=..., system_prompt=..., prompt=...)`，不触发插件钩子；人格用 `persona_manager.get_default_persona_v3(umo)["prompt"]`
- 人格管理器在插件加载前已初始化，插件在 `async def initialize()` 里同步人格：`get_all_personas()` 的元素有 `persona_id`、`system_prompt`；`create_persona(id, prompt)` 已存在时抛错；`update_persona(id, system_prompt=...)` 只改传入的字段
- `on_llm_response` 在每次 agent 运行结束时触发一次
- 内置插件 `astrbot` 已注册监听所有消息的处理器，每条消息本来就会被唤醒判定、计入限流，我们的群消息监听不改变这一点
- WebUI 页面：`views/<名>/index.html` 自动被发现，AstrBot 会注入 bridge 脚本；页面里 `AstrBotPluginView.apiGet("overview")` 对应 `context.register_web_api("/astrbot_plugin_rmts/overview", ...)`。处理函数无参数，查询参数用 `from astrbot.api.web import request` 的 `request.query.get(...)`，返回 `json_response({"status": "ok", "data": ...})`
- 页面的 CSP 允许内联脚本；主题通过 `html[data-theme]` 切换

## Git

- 只提交到 `main`，不推送，除非用户明确要求
- 提交前检查改动里没有服务器地址、真实 QQ 号 / 群号、密钥或本机路径
