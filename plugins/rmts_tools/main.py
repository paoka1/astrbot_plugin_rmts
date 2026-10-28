"""
从 rmts（NoneBot2 迷迭香机器人）迁移的 function calling 工具
工具的使用规则写在各自的说明里（原 prompt.py 中「函数调用规则」的对应条目）
"""

import json
import random
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .memory import MemoryStore
from .search import search_text
from .weather import get_forecast_text

RESOURCES = Path(__file__).parent / "resources"
STICKER_DIR = RESOURCES / "stickers"
STICKERS = sorted(p.stem for p in STICKER_DIR.glob("*.png"))

# 被戳时交给模型的话，移植自 rmts/plugins/chat/__init__.py
POKE_MSGS = ["博士（TA的名字是：{}，TA的ID是{}）戳了戳你",
             "博士（TA的名字是：{}，TA的ID是{}）轻轻地戳了戳你",
             "博士（TA的名字是：{}，TA的ID是{}）拍了拍你",
             "博士（TA的名字是：{}，TA的ID是{}）向你打招呼",
             "博士（TA的名字是：{}，TA的ID是{}）摸了摸你",
             "你看见了博士（TA的名字是：{}，TA的ID是{}）"]

# 网页搜索复用这个 AstrBot 模型提供商的 DeepSeek API Key
SEARCH_PROVIDER_ID = "deepseek/deepseek-v4-pro"

# event extra 标记：本次请求的提示词已带上博士信息 / 本次回复由戳一戳触发
EXTRA_PREFIXED = "rmts_prefixed"
EXTRA_POKE = "rmts_poke"


def scope_of(event: AstrMessageEvent) -> str:
    """记忆作用域：群聊用群号，私聊用 private_<QQ号>"""
    group_id = event.get_group_id()
    return str(group_id) if group_id else f"private_{event.get_sender_id()}"


class PokeMeFilter(CustomFilter):
    """只放行别人戳机器人的事件（rmts 的 is_poke_me）"""

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        if event.get_sender_id() == event.get_self_id():
            return False
        return any(isinstance(c, Comp.Poke) and c.target_id() == str(event.get_self_id())
                   for c in event.get_messages())


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.birthdays = json.loads((RESOURCES / "birthday.json").read_text(encoding="utf-8"))
        self.operators = json.loads((RESOURCES / "operators_info.json").read_text(encoding="utf-8")).get("operators", {})
        self.memory = MemoryStore(StarTools.get_data_dir("rmts_tools"), int(config.get("memory_max_length", 300)))
        logger.info(f"[rmts_tools] 生日 {len(self.birthdays)} 条，干员 {len(self.operators)} 名，表情 {len(STICKERS)} 个")

    # ---------- 干员生日 ----------

    @filter.llm_tool(name="get_birth_by_date")
    async def get_birth_by_date(self, event: AstrMessageEvent, date: str):
        '''通过日期获取过生日的干员。问"今天谁过生日"时，用系统提示里的当前日期换算成这个格式；查询结果不要记进记忆。

        Args:
            date(string): 日期，格式为 M月D日，例如 1月1日、3月15日（不补零）
        '''
        result = [item["name"] for item in self.birthdays if item["birthday"] == date]
        return f"{date}过生日的干员有: {', '.join(result)}" if result else f"{date}没有干员过生日"

    @filter.llm_tool(name="get_birth_by_name")
    async def get_birth_by_name(self, event: AstrMessageEvent, name: str):
        '''通过名字获取干员的生日。传入正式名称，例如"迷迭香"而不是"香香"；查询结果不要记进记忆。

        Args:
            name(string): 干员正式名称
        '''
        for item in self.birthdays:
            if item["name"] == name:
                return f"{name}的生日是: {item['birthday']}"
        return f"没有找到名为{name}的干员的生日信息"

    # ---------- 干员档案 ----------

    @filter.llm_tool(name="get_operator_info")
    async def get_operator_info(self, event: AstrMessageEvent, name: str):
        '''获取明日方舟干员的档案信息。干员信息缺失时先调用它；传入正式名称，例如"澄闪"而不是"闪闪"；查询结果不要记进记忆。

        Args:
            name(string): 干员正式名称，如：澄闪
        '''
        operator = self.operators.get(name)
        if not operator:
            return f"没有找到名为{name}的干员信息"
        return f"{name}的干员信息：{operator.get('summary', '该干员无信息')}"

    # ---------- 天气 ----------

    @filter.llm_tool(name="get_weather")
    async def get_weather(self, event: AstrMessageEvent, location: str):
        '''获取某地未来几天的天气预报。博士没说地点时，先读博士的个人记忆和全局记忆找所在地，找不到再问博士；地点写详细，例如"广州市"而不是"广东"；查询结果不要记进记忆。

        Args:
            location(string): 查询天气的地点，如：广州市、广宁县
        '''
        api_key = self.config.get("amap_weather_api_key", "")
        if not api_key:
            return "天气查询未配置高德 API Key"
        try:
            return await get_forecast_text(api_key, location)
        except Exception as e:
            logger.error(f"[rmts_tools] 获取天气信息时发生错误: {e}")
            return f"获取天气信息时发生错误: {e}"

    # ---------- 网页搜索 ----------

    @filter.llm_tool(name="web_search")
    async def web_search(self, event: AstrMessageEvent, query: str):
        '''在终端浏览器上搜索。需要最新消息、实时信息，或者遇到自己不知道、不确定的事情时调用；回答时依据搜索结果，不要编造。查询结果不要记进记忆。

        Args:
            query(string): 搜索词，不超过 128 字；可用 site:网站、"精确短语"、-排除词、after:2025 等语法
        '''
        provider = self.context.get_provider_by_id(SEARCH_PROVIDER_ID)
        keys = provider.get_keys() if provider else []
        if not keys:
            return f"网页搜索不可用：找不到模型提供商 {SEARCH_PROVIDER_ID} 的 API Key"
        try:
            return await search_text(keys[0], query, ori_question=event.message_str or None)
        except Exception as e:
            logger.error(f"[rmts_tools] 网页搜索时发生错误: {e}")
            return f"网页搜索时发生错误: {e}"

    # ---------- 表情包 ----------

    async def send_sticker(self, event: AstrMessageEvent, type: str):
        if type not in STICKERS:
            return f"表情 {type} 不存在"
        await event.send(MessageChain().file_image(str(STICKER_DIR / f"{type}.png")))
        return f"已向博士发送表情 {type}"

    # 可选表情要写进工具说明，所以说明在加载时按表情目录生成
    send_sticker.__doc__ = f'''向博士发送一个迷迭香表情包。可选表情：{"、".join(STICKERS)}

Args:
    type(string): 表情名称，必须是上面列出的其中一个
'''
    send_sticker = filter.llm_tool(name="send_sticker")(send_sticker)

    # ---------- 记忆 ----------

    @filter.llm_tool(name="add_doctor_info")
    async def add_doctor_info(self, event: AstrMessageEvent, info: list, doctor_id: int):
        '''在终端记下某位博士的个人信息。迷迭香记性不好，对话中出现值得长期记住的个人信息（如生日、所在地、喜好）就记下来。只能记录正在说话的博士本人的信息；如果博士A说的是博士B的事，改用 add_global_info 记在全局。

        Args:
            info(array[string]): 要记下的信息列表
            doctor_id(number): 博士的 QQ 号
        '''
        user_id = event.get_sender_id()
        if str(doctor_id) != str(user_id):
            return f"ID为{user_id}的博士想要修改ID为{doctor_id}博士的信息，这是不可以的"
        await self.memory.add(scope_of(event), str(doctor_id), info)
        return "已成功记下博士的信息"

    @filter.llm_tool(name="get_doctor_all_info")
    async def get_doctor_all_info(self, event: AstrMessageEvent, doctor_id: int):
        '''在终端读取某位博士的所有个人信息。发现缺少某位博士的信息时调用，查询时也要一并查全局信息。

        Args:
            doctor_id(number): 博士的 QQ 号
        '''
        memories = await self.memory.get(scope_of(event), str(doctor_id))
        if not memories:
            return "博士还没有任何记录的信息"
        return "博士的所有信息：\n" + "\n".join(memories)

    @filter.llm_tool(name="add_global_info")
    async def add_global_info(self, event: AstrMessageEvent, info: list):
        '''在终端记下当前群的全局信息（不属于某一位博士本人的信息，例如博士A说博士B的生日）。

        Args:
            info(array[string]): 要记下的信息列表
        '''
        scope = scope_of(event)
        await self.memory.add(scope, scope, info)
        return "已成功记下全局信息"

    @filter.llm_tool(name="get_global_all_info")
    async def get_global_all_info(self, event: AstrMessageEvent):
        '''在终端读取当前群的所有全局信息。查询信息时，个人信息和全局信息都要查。'''
        scope = scope_of(event)
        memories = await self.memory.get(scope, scope)
        if not memories:
            return "还没有任何记录的全局信息"
        return "群组的所有全局信息：\n" + "\n".join(memories)

    # ---------- 戳一戳 ----------

    @filter.llm_tool(name="poke_doctor")
    async def poke_doctor(self, event: AstrMessageEvent, doctor_id: int):
        '''戳一戳指定博士。私聊时只能戳正在聊天的博士。

        Args:
            doctor_id(number): 博士的 QQ 号
        '''
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

    # ---------- 被戳时触发对话 ----------

    async def nickname_of(self, event: AstrMessageEvent, user_id: str) -> str:
        """群里优先群名片，否则 QQ 昵称（rmts 的 get_nickname）"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return user_id
        try:
            group_id = event.get_group_id()
            if group_id:
                info = await bot.call_action("get_group_member_info", group_id=int(group_id), user_id=int(user_id))
                return str(info.get("card") or info.get("nickname") or user_id)
            info = await bot.call_action("get_stranger_info", user_id=int(user_id))
            return str(info.get("nickname") or user_id)
        except Exception as e:
            logger.warning(f"[rmts_tools] 获取 {user_id} 的昵称失败: {e}")
            return user_id

    @filter.custom_filter(PokeMeFilter, False)  # raise_error 只能按位置传
    async def on_poked(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        text = random.choice(POKE_MSGS).format(await self.nickname_of(event, user_id), user_id)

        # 接着当前对话聊，人设、历史和工具都和普通消息一样
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo) or await conv_mgr.new_conversation(umo, event.get_platform_id())
        conversation = await conv_mgr.get_conversation(umo, cid)

        event.set_extra(EXTRA_PREFIXED, True)
        event.set_extra(EXTRA_POKE, True)
        yield event.request_llm(prompt=text, conversation=conversation)

    @filter.on_decorating_result()
    async def mention_poker(self, event: AstrMessageEvent):
        """群里被戳时，回复前 @ 戳的人"""
        result = event.get_result()
        if event.get_extra(EXTRA_POKE) and event.get_group_id() and result and result.chain:
            result.chain.insert(0, Comp.At(qq=event.get_sender_id()))
            result.chain.insert(1, Comp.Plain(" "))

    # ---------- 博士信息 ----------

    @filter.on_llm_request()
    async def add_sender_info(self, event: AstrMessageEvent, req: ProviderRequest):
        """在消息前拼上博士的名字和 QQ 号（rmts 的「博士（TA的名字是…）对你说：…」）"""
        if event.get_extra(EXTRA_PREFIXED):
            return
        event.set_extra(EXTRA_PREFIXED, True)
        prefix = f"博士（TA的名字是：{event.get_sender_name()}，TA的ID是{event.get_sender_id()}）"
        if req.image_urls:
            prefix += f"，发送了{len(req.image_urls)}张图片"
        req.prompt = f"{prefix}，对你说：{req.prompt}" if req.prompt else prefix
