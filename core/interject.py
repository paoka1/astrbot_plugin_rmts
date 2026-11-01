"""群里插话：按新消息条数和时间间隔决定什么时候唤醒她看看群聊，以及唤醒时给她看的内容。"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .diary import DiaryStore, LogEntry
from .memory import Fact, MemoryStore, cap_facts, visible_facts

PASS = "不说"
CONTEXT_SIZE = 40  # 唤醒时给她看最近多少条群消息
MAX_PEOPLE = 5  # 附上最近说话的几位博士的记事

INTERJECT_TASK = f"""上面是群里最近的聊天，没有人在叫你。你可以像群里的一员那样插一句话，但大多数时候应该不说。
只有这几种情况才开口：
- 最近几分钟里有人提到你（迷迭香、香香、猫猫）或在找你；
- 最近几分钟里有人说自己累了、病了、难过，需要关心；
- 有人问了一个没人回答、而你确实知道答案的问题；
- 正在聊的正好是你熟悉的事，比如布丁、罗德岛、你认识的人。
开口时接住某个人的某句话，叫出对方的名字。
要说的话前面可以加标记，告诉终端怎么发：[回复3] 引用第 3 条消息回复，[@QQ号] @ 某位博士；不加就直接发在群里。
接较早的话、或者怕对方没注意到时，用标记说清楚是在回谁的哪句话；加不加、加哪个由你决定。
听不懂的词、梗、黑话、游戏术语不要去问；拿不准就不说。
带性暗示、擦边或拿你开黄腔的话，一律当作没看见，不说。
不要重复你最近说过的话，不要假装有人在问你，不要提“系统”“唤醒”之类的词。
要说就以迷迭香的口吻直接写出这句话，一到三句，不换行；不说就只输出：{PASS}"""


@dataclass
class _State:
    new: int = 0  # 上次判断或她上次说话之后的新消息条数
    last_check: datetime | None = None
    last_spoke: datetime | None = None
    day: str = ""
    spoken: int = 0  # 当天插话次数


class Interjector:
    """每个群各自计数；只在新消息够多、离上次判断和她上次说话都够久时唤醒。"""

    def __init__(self, min_messages: int, min_minutes: int, daily_limit: int) -> None:
        self.min_messages = max(1, min_messages)
        self.interval = timedelta(minutes=max(0, min_minutes))
        self.daily_limit = daily_limit
        self._states: dict[str, _State] = {}

    def _state(self, scope: str, now: datetime) -> _State:
        state = self._states.setdefault(scope, _State())
        if state.day != now.date().isoformat():
            state.day, state.spoken = now.date().isoformat(), 0
        return state

    def on_message(self, scope: str, now: datetime) -> bool:
        """记一条别人的群消息，返回这时要不要唤醒她判断。"""
        state = self._state(scope, now)
        state.new += 1
        if state.new < self.min_messages:
            return False
        last = max((t for t in (state.last_check, state.last_spoke) if t), default=None)
        if last and now - last < self.interval:
            return False
        if self.daily_limit > 0 and state.spoken >= self.daily_limit:
            return False
        state.new, state.last_check = 0, now
        return True

    def spoke(self, scope: str, now: datetime) -> None:
        """她在这个群说了话（回复、提醒或插话），重新计数。"""
        state = self._state(scope, now)
        state.new, state.last_spoke = 0, now

    def spoke_since(self, scope: str, since: datetime) -> bool:
        state = self._states.get(scope)
        return bool(state and state.last_spoke and state.last_spoke >= since)

    def interjected(self, scope: str, now: datetime) -> None:
        self._state(scope, now).spoken += 1


def interject_prompt(
    *,
    now: datetime,
    entries: list[LogEntry],
    group_facts: list[Fact],
    people: list[tuple[str, str, list[Fact]]],
    diary: tuple[str, str] | None,
) -> str:
    lines = [f"现在是 {now:%Y-%m-%d %H:%M}。", "【迷迭香终端上的记事】"]
    if group_facts:
        lines.extend(["本群记录：", *(f"#{f.id} {f.text}" for f in group_facts)])
    for qq, name, facts in people:
        lines.extend(
            [f"群里的博士 {name}({qq})：", *(f"#{f.id} {f.text}" for f in facts)]
        )
    if diary:
        lines.append(f"最近的日记：{diary[0]}：{diary[1]}")
    lines.append("【最近的群聊】（前面的数字是消息编号）")
    for n, e in enumerate(entries, 1):
        when = datetime.fromtimestamp(e.ts).strftime("%H:%M")
        who = "你" if e.is_self else f"{e.name}({e.qq})"
        lines.append(f"[{n}] {when} {who}：{e.text}")
    lines.append(INTERJECT_TASK)
    return "\n".join(lines)


def build_prompt(
    memory: MemoryStore,
    diaries: DiaryStore,
    scope: str,
    entries: list[LogEntry],
    now: datetime,
) -> str:
    """和平时聊天一样带上记事：本群记录、最近说话的博士在本群可见的事实、最近的日记。"""
    recent = entries[-CONTEXT_SIZE:]
    cap = memory.max_length * 2
    people, seen = [], set()
    for e in reversed(recent):
        if e.is_self or not e.qq or e.qq in seen:
            continue
        seen.add(e.qq)
        profile = memory.doctor(e.qq)
        if profile and (
            facts := cap_facts(visible_facts(profile, scope=scope, is_self=False), cap)
        ):
            people.append((e.qq, e.name, facts))
        if len(seen) >= MAX_PEOPLE:
            break
    latest = diaries.latest(scope, 1)
    return interject_prompt(
        now=now,
        entries=recent,
        group_facts=cap_facts(memory.group(scope), cap),
        people=people,
        diary=(latest[0].date, latest[0].text) if latest else None,
    )


@dataclass
class Interjection:
    text: str
    reply_to: LogEntry | None = None  # 引用回复的那条消息
    at: LogEntry | None = None  # @ 这条消息的发送者


# [回复n]、[@QQ号]；她有时会写成 [@名字]，也认
_MARK = re.compile(r"\s*\[\s*(?:回复\s*#?\s*(\d+)|@\s*([^\]]+?))\s*\]")


def parse_reply(reply: str, entries: list[LogEntry]) -> Interjection | None:
    """拆出开头的 [回复n]、[@QQ号] 标记；选择不说时返回 None。

    编号对应 build_prompt 里给她看的最近 CONTEXT_SIZE 条消息。只能引用别人带消息 ID 的话，
    只能 @ 这段群聊里说过话的人，不合规的标记直接丢掉。
    """
    recent = entries[-CONTEXT_SIZE:]
    result = Interjection("")
    text = reply.strip()
    while m := _MARK.match(text):
        text = text[m.end() :]
        if m.group(1):
            n = int(m.group(1))
            if (
                1 <= n <= len(recent)
                and recent[n - 1].msg_id
                and not recent[n - 1].is_self
            ):
                result.reply_to = recent[n - 1]
        else:
            who = m.group(2)
            result.at = next(
                (
                    e
                    for e in reversed(recent)
                    if not e.is_self and who in (e.qq, e.name)
                ),
                None,
            )
    text = text.strip()
    if not text.strip('。.！!…"“”「」') or text.strip('。.！!…"“”「」') == PASS:
        return None
    result.text = text
    return result
