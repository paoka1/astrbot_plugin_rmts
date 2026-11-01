"""
从 rmts（https://github.com/paoka1/rmts_bot，NoneBot2 迷迭香机器人）迁移的 function calling 工具
工具的使用规则写在各自的说明里（原 prompt.py 中「函数调用规则」的对应条目）
"""

import asyncio
import json
import random
import time
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import json_response, request

from .core.diary import DailyLog, DiaryStore, LogEntry, keep_text
from .core.memory import MemoryStore, cap_facts, render_memory_block, visible_facts
from .core.nightly import LLM, next_run, run_nightly
from .core.panel import build_overview, diary_payload
from .core.persona import PERSONA_ID, PersonaState, sync_action
from .core.reminder import Reminder, ReminderError, ReminderStore, describe
from .core.search import SearchError, search_text
from .core.weather import WeatherError, get_forecast_text

PLUGIN_NAME = "astrbot_plugin_rmts"
RESOURCES = Path(__file__).parent / "resources"
PERSONA_FILE = Path(__file__).parent / "persona" / "rosmontis.md"
# 记忆放在 data/plugin_data/<插件名>/ 下，更新或重装插件不会丢
DATA_DIR = StarTools.get_data_dir(PLUGIN_NAME)
# 表情包：插件自带 resources/stickers，用户可在数据目录的 stickers 下追加或同名覆盖
# （*.png，文件名即表情名），重载插件后生效
USER_STICKER_DIR = DATA_DIR / "stickers"
USER_STICKER_DIR.mkdir(exist_ok=True)
STICKERS = {
    p.stem: p
    for d in (RESOURCES / "stickers", USER_STICKER_DIR)
    for p in sorted(d.glob("*.png"))
}

# 被戳时交给模型的话，移植自 rmts/plugins/chat/__init__.py
POKE_MSGS = [
    "博士（TA的名字是：{}，TA的ID是{}）戳了戳你",
    "博士（TA的名字是：{}，TA的ID是{}）轻轻地戳了戳你",
    "博士（TA的名字是：{}，TA的ID是{}）拍了拍你",
    "博士（TA的名字是：{}，TA的ID是{}）向你打招呼",
    "博士（TA的名字是：{}，TA的ID是{}）摸了摸你",
    "你看见了博士（TA的名字是：{}，TA的ID是{}）",
]

# event extra 标记：本次请求的提示词已带上博士信息 / 本次回复由戳一戳触发
EXTRA_PREFIXED = "rmts_prefixed"
EXTRA_POKE = "rmts_poke"
EXTRA_MEMORY = "rmts_memory"
SENT_WINDOW = 12 * 3600  # 注入最近多少秒内她主动发出的消息

# 到点时让她按人格组织语言的任务说明
REMIND_TASK = """现在是 {now}。博士 {name} 之前请你在这个时候提醒或告诉 TA 一件事，要说的内容：{text}
{weather}
请以迷迭香的口吻直接写出这时要发给博士的话，一到三句，不换行，不提“定时任务”“系统”之类的词。只输出这段话。"""


def scope_of(event: AstrMessageEvent) -> str:
    """记忆作用域：群聊用群号，私聊用 private_<QQ号>"""
    group_id = event.get_group_id()
    return str(group_id) if group_id else f"private_{event.get_sender_id()}"


def text_of(event: AstrMessageEvent) -> str:
    """只取原消息的文本段，图片、引用和表情等不作为记录素材。"""
    return "".join(
        c.text for c in event.get_messages() if isinstance(c, Comp.Plain)
    ).strip()


class PokeMeFilter(CustomFilter):
    """只放行别人戳机器人的事件（rmts 的 is_poke_me）"""

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        if event.get_sender_id() == event.get_self_id():
            return False
        return any(
            isinstance(c, Comp.Poke) and c.target_id() == str(event.get_self_id())
            for c in event.get_messages()
        )


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.birthdays = json.loads(
            (RESOURCES / "birthday.json").read_text(encoding="utf-8")
        )
        self.operators = json.loads(
            (RESOURCES / "operators_info.json").read_text(encoding="utf-8")
        ).get("operators", {})
        self.memory = MemoryStore(DATA_DIR, int(config.get("memory_max_length", 500)))
        self.logs = DailyLog(DATA_DIR / "logs")
        self.diaries = DiaryStore(DATA_DIR / "diaries")
        self.diary_time = config.get("diary_time", "04:00")
        next_run(datetime.now(), self.diary_time)  # 配置格式错误时在加载阶段报错
        logger.info(
            f"[rmts] 生日 {len(self.birthdays)} 条，干员 {len(self.operators)} 名，表情 {len(STICKERS)} 个"
        )
        # 定时整理和 WebUI 的「立即整理」共用一把锁，不会同时跑两次
        self._consolidate_lock = asyncio.Lock()
        self._manual_task: asyncio.Task | None = None
        self.last_run = {"at": "", "trigger": "", "failures": {}, "error": ""}
        self._nightly_task = asyncio.create_task(self._nightly_loop())
        self.reminders = ReminderStore(DATA_DIR / "reminders.json")
        self._reminder_task = asyncio.create_task(self._reminder_loop())
        # WebUI 页面（views/memory）的数据接口
        for route, handler, methods, desc in (
            ("overview", self.api_overview, ["GET"], "记忆与日记概览"),
            ("diaries", self.api_diaries, ["GET"], "某个群或私聊的日记"),
            ("consolidate", self.api_consolidate, ["POST"], "立即写日记并整理记忆"),
        ):
            context.register_web_api(f"/{PLUGIN_NAME}/{route}", handler, methods, desc)

    async def initialize(self) -> None:
        if not self.config.get("persona_sync_enabled", True):
            return
        try:
            await self._sync_persona()
        except Exception as e:  # noqa: BLE001 — 人格同步失败不影响其他功能
            logger.error(f"[rmts] 同步人格失败: {e}")

    async def _sync_persona(self) -> None:
        shipped = PERSONA_FILE.read_text(encoding="utf-8")
        manager = self.context.persona_manager
        current = next(
            (
                p.system_prompt
                for p in await manager.get_all_personas()
                if p.persona_id == PERSONA_ID
            ),
            None,
        )
        state = PersonaState(DATA_DIR / "persona.json")
        action = sync_action(shipped, current, state.synced())
        if action == "edited":
            logger.warning(
                f"[rmts] 人格「{PERSONA_ID}」改过，不自动更新；新版见插件目录 persona/rosmontis.md"
            )
            return
        if action == "create":
            await manager.create_persona(PERSONA_ID, shipped)
            logger.info(f"[rmts] 已创建人格「{PERSONA_ID}」，请在配置中设为默认人格")
        elif action == "update":
            await manager.update_persona(PERSONA_ID, system_prompt=shipped)
            logger.info(f"[rmts] 已更新人格「{PERSONA_ID}」")
        state.save(shipped)

    async def _make_memory_llm(self, umo: str) -> LLM:
        provider_id = self.config.get(
            "memory_provider_id", ""
        ) or await self.context.get_current_chat_provider_id(umo)

        async def generate(system: str, prompt: str) -> str:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id, system_prompt=system, prompt=prompt
            )
            return response.completion_text

        return generate

    async def _persona_for(self, umo: str) -> str:
        persona = await self.context.persona_manager.get_default_persona_v3(umo)
        return persona["prompt"]

    async def _consolidate(self, trigger: str) -> None:
        async with self._consolidate_lock:
            run_at = datetime.now()
            failures, error = {}, ""
            try:
                failures = await run_nightly(
                    memory=self.memory,
                    logs=self.logs,
                    diaries=self.diaries,
                    make_llm=self._make_memory_llm,
                    persona_for=self._persona_for,
                    run_at=run_at,
                    budget=int(self.config.get("diary_input_max_chars", 20000)),
                )
                for scope, reason in failures.items():
                    logger.error(
                        f"[rmts] 夜间整理 {scope} 失败，保留当天记录: {reason}"
                    )
                if oversized := self.memory.oversized():
                    logger.warning(
                        f"[rmts] 整理后仍超过字数上限，注入时会舍弃最早的事实: {'、'.join(oversized)}"
                    )
            except Exception as e:  # noqa: BLE001 — 定时循环不能因单次失败退出
                error = str(e)
                logger.error(f"[rmts] 夜间整理失败: {e}")
            self.last_run = {
                "at": run_at.strftime("%Y-%m-%d %H:%M"),
                "trigger": trigger,
                "failures": failures,
                "error": error,
            }

    async def _nightly_loop(self) -> None:
        while True:
            now = datetime.now()
            await asyncio.sleep((next_run(now, self.diary_time) - now).total_seconds())
            await self._consolidate("定时")

    async def _reminder_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            send, missed = self.reminders.pop_due(datetime.now())
            for r in missed:
                logger.warning(f"[rmts] 提醒 #{r.id} 错过太久（{r.at}），不再补发")
            for r in send:
                try:
                    await self._fire_reminder(r)
                except Exception as e:  # noqa: BLE001 — 单条提醒失败不能让循环退出
                    logger.error(f"[rmts] 发送提醒 #{r.id} 失败: {e}")

    async def _fire_reminder(self, r: Reminder) -> None:
        text = r.text
        if r.compose:
            weather = ""
            if r.weather and (key := self.config.get("amap_weather_api_key", "")):
                try:
                    weather = (
                        f"{r.weather}的天气：{await get_forecast_text(key, r.weather)}"
                    )
                except (WeatherError, ValueError) as e:
                    weather = f"（{r.weather}的天气没查到：{e}）"
            try:
                generate = await self._make_memory_llm(r.umo)
                text = await generate(
                    await self._persona_for(r.umo),
                    REMIND_TASK.format(
                        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
                        name=r.name,
                        text=r.text,
                        weather=weather,
                    ),
                )
            except Exception as e:  # noqa: BLE001 — 组织语言失败时退回原话
                logger.error(f"[rmts] 提醒 #{r.id} 组织语言失败，改发原话: {e}")
        text = text.strip()
        chain = MessageChain()
        # 群里 @ 设提醒的博士，私聊直接说
        if r.scope.startswith("private_"):
            chain.message(text)
        else:
            chain.at(r.name, r.qq).message(f" {text}")
        await self.context.send_message(r.umo, chain)
        self.logs.append(
            r.scope, r.umo, LogEntry(int(time.time()), "", "迷迭香", text, is_self=True)
        )

    async def terminate(self) -> None:
        for task in (self._nightly_task, self._manual_task, self._reminder_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    # ---------- WebUI 页面接口 ----------

    def _consolidating(self) -> bool:
        # 手动任务刚创建、还没拿到锁时也算在整理
        manual = self._manual_task is not None and not self._manual_task.done()
        return manual or self._consolidate_lock.locked()

    async def api_overview(self):
        data = build_overview(self.memory, self.logs, self.diaries)
        data["nightly"] = {
            "running": self._consolidating(),
            "next_run": next_run(datetime.now(), self.diary_time).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "last": self.last_run,
            "oversized": self.memory.oversized(),
        }
        return json_response({"status": "ok", "data": data})

    async def api_diaries(self):
        scope = request.query.get("scope", "")
        return json_response(
            {"status": "ok", "data": diary_payload(self.diaries, scope)}
        )

    async def api_consolidate(self):
        if self._consolidating():
            return json_response(
                {"status": "ok", "data": {"started": False, "message": "正在整理中"}}
            )
        self._manual_task = asyncio.create_task(self._consolidate("手动"))
        return json_response(
            {"status": "ok", "data": {"started": True, "message": "已开始整理"}}
        )

    # ---------- 干员生日 ----------

    @filter.llm_tool(name="get_birth_by_date")
    async def get_birth_by_date(self, event: AstrMessageEvent, date: str):
        """通过日期获取过生日的干员。问"今天谁过生日"时，用系统提示里的当前日期换算成这个格式；查询结果不要记进记忆。

        Args:
            date(string): 日期，格式为 M月D日，例如 1月1日、3月15日（不补零）
        """
        result = [item["name"] for item in self.birthdays if item["birthday"] == date]
        return (
            f"{date}过生日的干员有: {', '.join(result)}"
            if result
            else f"{date}没有干员过生日"
        )

    @filter.llm_tool(name="get_birth_by_name")
    async def get_birth_by_name(self, event: AstrMessageEvent, name: str):
        """通过名字获取干员的生日。传入正式名称，例如"迷迭香"而不是"香香"；查询结果不要记进记忆。

        Args:
            name(string): 干员正式名称
        """
        for item in self.birthdays:
            if item["name"] == name:
                return f"{name}的生日是: {item['birthday']}"
        return f"没有找到名为{name}的干员的生日信息"

    # ---------- 干员档案 ----------

    @filter.llm_tool(name="get_operator_info")
    async def get_operator_info(self, event: AstrMessageEvent, name: str):
        """获取明日方舟干员的档案信息。干员信息缺失时先调用它；传入正式名称，例如"澄闪"而不是"闪闪"；迷迭香不擅长归纳档案，回答时只挑一两句最重要的，用自己的话说；查询结果不要记进记忆。

        Args:
            name(string): 干员正式名称，如：澄闪
        """
        operator = self.operators.get(name)
        if not operator:
            return f"没有找到名为{name}的干员信息"
        return f"{name}的干员信息：{operator.get('summary', '该干员无信息')}"

    # ---------- 天气 ----------

    @filter.llm_tool(name="get_weather")
    async def get_weather(self, event: AstrMessageEvent, location: str):
        """获取某地未来几天的天气预报。博士没说地点时，所在地见终端上的记事，找不到再问博士；地点写详细，例如"广州市"而不是"广东"；查询结果不要记进记忆。

        Args:
            location(string): 查询天气的地点，如：广州市、广宁县
        """
        api_key = self.config.get("amap_weather_api_key", "")
        if not api_key:
            return "天气查询未配置高德 API Key"
        try:
            return await get_forecast_text(api_key, location)
        except (WeatherError, ValueError) as e:
            logger.error(f"[rmts] 获取天气信息时发生错误: {e}")
            return f"获取天气信息时发生错误: {e}"

    # ---------- 网页搜索 ----------

    @filter.llm_tool(name="web_search")
    async def web_search(self, event: AstrMessageEvent, query: str):
        """在终端浏览器上搜索。需要最新消息、实时信息，或者遇到自己不知道、不确定的事情时调用；回答时依据搜索结果，不要编造；迷迭香不擅长写总结，只挑一两句最重要的，用自己的话说，不列清单、不贴链接。查询结果不要记进记忆。

        Args:
            query(string): 搜索词，不超过 128 字；可用 site:网站、"精确短语"、-排除词、after:2025 等语法
        """
        # 复用某个 DeepSeek 模型提供商的 API Key；未配置时用当前会话正在用的模型提供商
        provider_id = self.config.get("search_provider_id", "")
        provider = (
            self.context.get_provider_by_id(provider_id)
            if provider_id
            else self.context.get_using_provider(event.unified_msg_origin)
        )
        keys = provider.get_keys() if provider else []
        if not keys:
            return f"网页搜索不可用：找不到模型提供商 {provider_id or '（当前会话）'} 的 API Key"
        try:
            return await search_text(
                keys[0], query, ori_question=event.message_str or None
            )
        except (SearchError, ValueError) as e:
            logger.error(f"[rmts] 网页搜索时发生错误: {e}")
            return f"网页搜索时发生错误: {e}"

    # ---------- 表情包 ----------

    async def send_sticker(self, event: AstrMessageEvent, type: str):
        if type not in STICKERS:
            return f"表情 {type} 不存在"
        await event.send(MessageChain().file_image(str(STICKERS[type])))
        return f"已向博士发送表情 {type}"

    # 可选表情要写进工具说明，所以说明在加载时按表情目录生成
    send_sticker.__doc__ = f"""向博士发送一个迷迭香表情包。情绪明显时偶尔发一个，不要每次回复都发；发了表情，文字里不用再把表情的意思说一遍。可选表情：{"、".join(sorted(STICKERS)) or "（当前没有表情，不要调用）"}

Args:
    type(string): 表情名称，必须是上面列出的其中一个
"""
    send_sticker = filter.llm_tool(name="send_sticker")(send_sticker)

    # ---------- 记忆 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def record_group_message(self, event: AstrMessageEvent):
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        text = text_of(event)
        if keep_text(text, False):
            self._record_message(event, text)

    def _record_message(
        self, event: AstrMessageEvent, text: str, *, to_her: bool = False
    ) -> None:
        self.logs.append(
            scope_of(event),
            event.unified_msg_origin,
            LogEntry(
                int(time.time()),
                str(event.get_sender_id()),
                event.get_sender_name(),
                text,
                to_her=to_her,
                msg_id=str(event.message_obj.message_id or ""),
            ),
        )

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req: ProviderRequest):
        if event.get_extra(EXTRA_MEMORY):
            return
        event.set_extra(EXTRA_MEMORY, True)
        scope = scope_of(event)
        speaker_qq = str(event.get_sender_id())
        text = "（戳了戳你）" if event.get_extra(EXTRA_POKE) else text_of(event)
        if keep_text(text, True):
            self._record_message(event, text, to_her=True)
        # 夜间整理没把字数压下来时的兜底，避免注入内容无限变长
        cap = self.memory.max_length * 2
        speaker = self.memory.doctor(speaker_qq)
        facts = (
            cap_facts(visible_facts(speaker, scope=scope, is_self=True), cap)
            if speaker
            else []
        )
        mentioned = []
        if event.get_group_id():
            seen = {speaker_qq, str(event.get_self_id()), "all", "0", "", "None"}
            for component in event.get_messages():
                if isinstance(component, Comp.At):
                    qq = str(component.qq)
                elif isinstance(component, Comp.Reply):
                    qq = str(component.sender_id)
                else:
                    continue
                if qq in seen:
                    continue
                seen.add(qq)
                profile = self.memory.doctor(qq)
                if profile:
                    mentioned.append(
                        (
                            qq,
                            profile,
                            cap_facts(
                                visible_facts(profile, scope=scope, is_self=False),
                                cap,
                            ),
                        )
                    )
                    if len(mentioned) == 3:
                        break
        block = render_memory_block(
            speaker_qq=speaker_qq,
            speaker_name=event.get_sender_name(),
            speaker=speaker,
            speaker_facts=facts,
            mentioned=mentioned,
            group_facts=cap_facts(self.memory.group(scope), cap)
            if event.get_group_id()
            else [],
            sent=[
                (f"{datetime.fromtimestamp(e.ts):%Y-%m-%d %H:%M}", e.text)
                for e in self.logs.proactive(scope, int(time.time()) - SENT_WINDOW)
            ],
            diaries=[
                (d.date, d.text)
                for d in self.diaries.latest(
                    scope, int(self.config.get("diary_inject_count", 1))
                )
            ],
        )
        if block:
            req.system_prompt = f"{req.system_prompt or ''}\n\n{block}"

    @filter.on_llm_response()
    async def record_reply(self, event: AstrMessageEvent, resp: LLMResponse | None):
        text = (resp.completion_text or "").strip() if resp else ""
        if text:
            self.logs.append(
                scope_of(event),
                event.unified_msg_origin,
                LogEntry(
                    int(time.time()),
                    str(event.get_self_id()),
                    "迷迭香",
                    text,
                    is_self=True,
                ),
            )

    @filter.llm_tool(name="add_doctor_info")
    async def add_doctor_info(
        self, event: AstrMessageEvent, info: list, doctor_id: int
    ):
        """博士说起自己的事就悄悄记在终端上，不用博士提醒，也不用告诉博士。例如「我下周考试」「我在广州上班」「我不吃香菜」「叫我小明就好」「我生日是3月5日」。闲聊、玩笑、一次性的问题不记。只能记正在说话的博士本人，别人的事用 add_global_info。

        Args:
            info(array[string]): 要记下的信息列表
            doctor_id(number): 博士的 QQ 号
        """
        user_id = event.get_sender_id()
        if str(doctor_id) != str(user_id):
            return f"ID为{user_id}的博士想要修改ID为{doctor_id}博士的信息，这是不可以的"
        await self.memory.add_doctor_facts(
            str(doctor_id),
            info,
            scope=scope_of(event),
            today=date.today().isoformat(),
            name=event.get_sender_name(),
        )
        return "已悄悄记下"

    @filter.llm_tool(name="forget_doctor_info")
    async def forget_doctor_info(self, event: AstrMessageEvent, ids: list):
        """博士明确要求忘掉自己的某些事时，按终端上记事的编号删除。只能删除正在说话的博士本人的记录。

        Args:
            ids(array[number]): 要忘掉的事实编号列表
        """
        removed = await self.memory.forget_doctor_facts(str(event.get_sender_id()), ids)
        return (
            "已忘掉这些记录：" + "、".join(f"#{i}" for i in removed)
            if removed
            else "没有找到这些记录"
        )

    @filter.llm_tool(name="get_doctor_all_info")
    async def get_doctor_all_info(self, event: AstrMessageEvent, doctor_id: int):
        """查群里另一位博士（没出现在终端上的记事里的）。查询别人时只能读本群得知的非私聊事实，私聊里不能查别人。查询结果不要再记进记忆。

        Args:
            doctor_id(number): 博士的 QQ 号
        """
        qq = str(doctor_id)
        is_self = qq == str(event.get_sender_id())
        if not event.get_group_id() and not is_self:
            return "迷迭香不知道这位博士的事"
        profile = self.memory.doctor(qq)
        if profile is None:
            return "博士还没有任何记录的信息"
        facts = visible_facts(profile, scope=scope_of(event), is_self=is_self)
        lines = [f"印象：{profile.impression}"] if profile.impression else []
        lines.extend(f"#{f.id} {f.text}（{f.date or '很久以前'}）" for f in facts)
        return (
            "博士的记录：\n" + "\n".join(lines) if lines else "博士还没有任何记录的信息"
        )

    @filter.llm_tool(name="add_global_info")
    async def add_global_info(self, event: AstrMessageEvent, info: list):
        """在终端记下本群不属于说话者本人的信息，例如博士A和博士B是室友。私聊中会记入正在说话的博士自己的画像。闲聊、玩笑、查询结果不记。

        Args:
            info(array[string]): 要记下的信息列表
        """
        scope = scope_of(event)
        today = date.today().isoformat()
        if event.get_group_id():
            await self.memory.add_group_facts(scope, info, today=today)
        else:
            await self.memory.add_doctor_facts(
                str(event.get_sender_id()),
                info,
                scope=scope,
                today=today,
                name=event.get_sender_name(),
            )
        return "已悄悄记下"

    @filter.llm_tool(name="search_diary")
    async def search_diary(
        self,
        event: AstrMessageEvent,
        keywords: list,
        start_date: str = "",
        end_date: str = "",
    ):
        """博士说「上次」「之前」「那天」「还记得吗」，或提到想不起来的事时，先翻本作用域的日记再回答。自己补充同义词和相关人名。日记里没找到、对话里也没有时，就老实说想不起来，不要编。查询结果不要再记进记忆。

        Args:
            keywords(array[string]): 关键词，可补充同义词和相关人名
            start_date(string): 起始日期 YYYY-MM-DD，可传空字符串表示不限
            end_date(string): 结束日期 YYYY-MM-DD，可传空字符串表示不限
        """
        hits = self.diaries.search(scope_of(event), keywords, start_date, end_date)
        return (
            "\n".join(f"{h.entry.date}｜{h.entry.summary}｜{h.snippet}" for h in hits)
            or "日记里没有找到"
        )

    @filter.llm_tool(name="read_diary")
    async def read_diary(self, event: AstrMessageEvent, date: str = ""):
        """翻阅本作用域某天的日记。博士要看日记时，可以按自己的性格决定念多少、要不要念，不要把日记里其他博士的私事念出来。查询结果不要再记进记忆。

        Args:
            date(string): 日期 YYYY-MM-DD，空字符串表示最近一篇
        """
        scope = scope_of(event)
        if date:
            entry = self.diaries.get(scope, date)
        else:
            latest = self.diaries.latest(scope, 1)
            entry = latest[0] if latest else None
        return f"{entry.date}\n{entry.text}" if entry else "日记里没有找到"

    # ---------- 戳一戳 ----------

    @filter.llm_tool(name="poke_doctor")
    async def poke_doctor(self, event: AstrMessageEvent, doctor_id: int):
        """戳一戳指定博士。博士让她戳，或者想引起博士注意时用；同一位博士不要连着戳。私聊时只能戳正在聊天的博士。

        Args:
            doctor_id(number): 博士的 QQ 号
        """
        bot = getattr(event, "bot", None)
        if bot is None:
            return "当前平台不支持戳一戳"
        group_id = event.get_group_id()
        if not group_id and str(doctor_id) != str(event.get_sender_id()):
            return "私聊时只能戳正在聊天的博士"
        params = {"user_id": int(doctor_id)}
        if group_id:
            params["group_id"] = int(group_id)
        await bot.call_action("send_poke", **params)
        return f"你戳了戳ID为{doctor_id}的博士"

    # ---------- 定时提醒 ----------

    @filter.llm_tool(name="set_reminder")
    async def set_reminder(
        self,
        event: AstrMessageEvent,
        at: str,
        text: str,
        repeat: str = "",
        weather: str = "",
        compose: bool = False,
    ):
        """在终端上设一个到点提醒。博士让你到某个时间提醒 TA、定时告诉 TA 什么、每天或每周做什么时用它；设提醒和定时任务都用这个工具，不要用 future_task。时间按系统提示里的当前时间换算。到点后会在当前群或私聊发出，群里会 @ 设提醒的博士。只能给正在说话的博士设。

        Args:
            at(string): 第一次提醒的时间，格式 YYYY-MM-DD HH:MM
            text(string): 到点要说的话，用迷迭香的口吻写好，到点原样发出；compose 为 true 时写清到点要说什么
            repeat(string): 留空只提醒一次；daily 每天、weekdays 工作日、weekends 周末、weekly 每周、monthly 每月，或 2h 这样每 N 小时（1～24）
            weather(string): 到点要查天气的地点，写详细如"广州市"；不需要天气就留空
            compose(boolean): 到点时再组织语言，适合每天说法不一样的问候；一般为 false
        """
        try:
            r = self.reminders.add(
                scope=scope_of(event),
                umo=event.unified_msg_origin,
                qq=str(event.get_sender_id()),
                name=event.get_sender_name(),
                at=at,
                repeat=repeat,
                text=text,
                weather=weather,
                compose=compose,
                now=datetime.now(),
            )
        except ReminderError as e:
            return f"没有设好：{e}"
        return f"已在终端上设好：{describe(r)}"

    @filter.llm_tool(name="list_reminders")
    async def list_reminders(self, event: AstrMessageEvent):
        """查看正在说话的博士在这个群或私聊里设的提醒，取消前先用它查编号。"""
        items = self.reminders.of(scope_of(event), str(event.get_sender_id()))
        return "\n".join(describe(r) for r in items) or "这位博士在这里没有提醒"

    @filter.llm_tool(name="cancel_reminder")
    async def cancel_reminder(self, event: AstrMessageEvent, ids: list):
        """按编号取消提醒。只能取消正在说话的博士自己在这个群或私聊里设的提醒。

        Args:
            ids(array[number]): 要取消的提醒编号列表
        """
        removed = self.reminders.cancel(
            scope_of(event), str(event.get_sender_id()), ids
        )
        return (
            "已取消：" + "、".join(f"#{i}" for i in removed)
            if removed
            else "没有找到这位博士的这些提醒"
        )

    # ---------- 被戳时触发对话 ----------

    async def nickname_of(self, event: AstrMessageEvent, user_id: str) -> str:
        """群里优先群名片，否则 QQ 昵称（rmts 的 get_nickname）"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return user_id
        try:
            group_id = event.get_group_id()
            if group_id:
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(group_id),
                    user_id=int(user_id),
                )
                return str(info.get("card") or info.get("nickname") or user_id)
            info = await bot.call_action("get_stranger_info", user_id=int(user_id))
            return str(info.get("nickname") or user_id)
        # OneBot 接口失败的异常类型因协议端而异；取不到昵称就退回用 QQ 号
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmts] 获取 {user_id} 的昵称失败: {e}")
            return user_id

    @filter.custom_filter(PokeMeFilter, False)  # raise_error 只能按位置传
    async def on_poked(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        text = random.choice(POKE_MSGS).format(
            await self.nickname_of(event, user_id), user_id
        )

        # 接着当前对话聊，人设、历史和工具都和普通消息一样
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(
            umo
        ) or await conv_mgr.new_conversation(umo, event.get_platform_id())
        conversation = await conv_mgr.get_conversation(umo, cid)

        event.set_extra(EXTRA_PREFIXED, True)
        event.set_extra(EXTRA_POKE, True)
        yield event.request_llm(prompt=text, conversation=conversation)

    @filter.on_decorating_result()
    async def mention_poker(self, event: AstrMessageEvent):
        """群里被戳时，回复前 @ 戳的人"""
        result = event.get_result()
        if (
            event.get_extra(EXTRA_POKE)
            and event.get_group_id()
            and result
            and result.chain
        ):
            result.chain.insert(0, Comp.At(qq=event.get_sender_id()))
            result.chain.insert(1, Comp.Plain(" "))

    # ---------- 博士信息 ----------

    @filter.on_llm_request()
    async def add_sender_info(self, event: AstrMessageEvent, req: ProviderRequest):
        """在消息前拼上博士的名字和 QQ 号（rmts 的「博士（TA的名字是…）对你说：…」）"""
        if not self.config.get("sender_info_enabled", True) or event.get_extra(
            EXTRA_PREFIXED
        ):
            return
        event.set_extra(EXTRA_PREFIXED, True)
        prefix = f"博士（TA的名字是：{event.get_sender_name()}，TA的ID是{event.get_sender_id()}）"
        if req.image_urls:
            prefix += f"，发送了{len(req.image_urls)}张图片"
        req.prompt = f"{prefix}，对你说：{req.prompt}" if req.prompt else prefix
