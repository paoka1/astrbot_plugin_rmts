"""群里插话：按新消息条数和时间间隔决定什么时候唤醒她看看群聊，以及唤醒时给她看的内容。"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .diary import DiaryStore, LogEntry
from .memory import Fact, MemoryStore, cap_facts, visible_facts

PASS = "不说"
CONTEXT_SIZE = 40  # 唤醒时给她看最近多少条群消息
MAX_PEOPLE = 5  # 附上最近说话的几位博士的记事

INTERJECT_TASK = f"""上面是群里最近的聊天，没有人在叫你。你在一旁看着，想说什么就可以说：接一句话、关心一下谁、说说自己的想法都行；大家聊到你、聊到你认识的人或在乎的事时，更可以大方开口。
先想想你此刻有多想开口，写成 0 到 1 之间的一个数：0 是完全不想，1 是非常想。
第一行只写这个数。第二行写你想说的话，以迷迭香的口吻，一到三句，接住某个人的某句话；可以在话前面加标记：[回复3] 引用第 3 条消息，[@QQ号] @ 某位博士，接较早的话时用标记说清在回哪句。实在没什么想说的，第二行写：{PASS}
第三行以“理由：”开头，用一句话说说为什么想说或不想说，这一行不会发到群里。
这时你只能说话，用不了终端上的其他功能。"""


@dataclass(frozen=True)
class Rule:
    """什么时候唤醒她、意愿达到多少才开口。"""

    min_messages: int  # 至少攒够多少条新群消息
    min_minutes: int  # 离上次唤醒、她上次说话至少隔多少分钟
    threshold: float  # 意愿阈值，大于 1 表示不插话


@dataclass(frozen=True)
class Slot:
    start: int  # 一天中的第几分钟，含
    end: int  # 不含；比 start 小时表示跨午夜
    rule: Rule

    def contains(self, minute: int) -> bool:
        if self.start < self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end


_SPAN = re.compile(r"\s*(\d{1,2}):(\d{2})\s*[-~～—]\s*(\d{1,2}):(\d{2})\s*")


def parse_span(span: str) -> tuple[int, int]:
    m = _SPAN.fullmatch(span)
    if not m or int(m[1]) > 24 or int(m[3]) > 24 or int(m[2]) > 59 or int(m[4]) > 59:
        raise ValueError(f"插话时段应写成 HH:MM-HH:MM，收到「{span}」")
    return int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])


def parse_slots(entries: list, default: Rule) -> list[Slot]:
    """解析 WebUI 里按时段的设置（template_list 的条目）；没填时段的条目跳过。"""
    slots = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not str(entry.get("time", "")).strip():
            continue
        start, end = parse_span(str(entry["time"]))
        rule = Rule(
            int(entry.get("min_messages", default.min_messages)),
            int(entry.get("min_minutes", default.min_minutes)),
            float(entry.get("threshold", default.threshold)),
        )
        slots.append(Slot(start, end, rule))
    return slots


def rule_at(now: datetime, default: Rule, slots: list[Slot]) -> Rule:
    """当前时刻生效的规则；落在多个时段里时取排在前面的，不在任何时段里用默认值。"""
    minute = now.hour * 60 + now.minute
    return next((slot.rule for slot in slots if slot.contains(minute)), default)


@dataclass
class _State:
    new: int = 0  # 上次判断或她上次说话之后的新消息条数
    last_check: datetime | None = None
    last_spoke: datetime | None = None
    day: str = ""
    spoken: int = 0  # 当天插话次数


class Interjector:
    """每个群各自计数；只在新消息够多、离上次判断和她上次说话都够久时唤醒。

    条数、间隔和阈值按当前时段的规则来。
    """

    def __init__(self, default: Rule, slots: list[Slot], daily_limit: int) -> None:
        self.default = default
        self.slots = slots
        self.daily_limit = daily_limit
        self._states: dict[str, _State] = {}

    def rule(self, now: datetime) -> Rule:
        return rule_at(now, self.default, self.slots)

    def _state(self, scope: str, now: datetime) -> _State:
        state = self._states.setdefault(scope, _State())
        if state.day != now.date().isoformat():
            state.day, state.spoken = now.date().isoformat(), 0
        return state

    def on_message(self, scope: str, now: datetime) -> bool:
        """记一条别人的群消息，返回这时要不要唤醒她判断。"""
        state = self._state(scope, now)
        state.new += 1
        rule = self.rule(now)
        if state.new < max(1, rule.min_messages):
            return False
        last = max((t for t in (state.last_check, state.last_spoke) if t), default=None)
        if last and now - last < timedelta(minutes=max(0, rule.min_minutes)):
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
    text: str  # 为空表示她不想说
    score: float | None = None  # 她自己估的开口意愿，0～1；没写时为 None
    reply_to: LogEntry | None = None  # 引用回复的那条消息
    at: LogEntry | None = None  # @ 这条消息的发送者
    reason: str = ""  # 她说的为什么想说或不想说，只记下来，不发出去

    def passes(self, threshold: float) -> bool:
        return bool(self.text) and self.score is not None and self.score >= threshold


# 开头的意愿分数，允许写成「意愿：0.6」
_SCORE = re.compile(r"\s*(?:意愿\s*[:：]?\s*)?(1(?:\.0+)?|0?\.\d+|0)(?![\d.])")
# 最后一行的理由
_REASON = re.compile(r"(?:^|\n)\s*理由\s*[:：]\s*(.*?)\s*$", re.DOTALL)
# [回复n]、[@QQ号]；她有时会写成 [@名字]，也认
_MARK = re.compile(r"\s*\[\s*(?:回复\s*#?\s*(\d+)|@\s*([^\]]+?))\s*\]")


def parse_reply(reply: str, entries: list[LogEntry]) -> Interjection:
    """拆出开头的意愿分数、最后的理由和 [回复n]、[@QQ号] 标记；选择不说时 text 为空。

    编号对应 build_prompt 里给她看的最近 CONTEXT_SIZE 条消息。只能引用别人带消息 ID 的话，
    只能 @ 这段群聊里说过话的人，不合规的标记直接丢掉。
    """
    recent = entries[-CONTEXT_SIZE:]
    result = Interjection("")
    text = reply.strip()
    if m := _SCORE.match(text):
        result.score = float(m[1])
        text = text[m.end() :].strip()
    if m := _REASON.search(text):
        result.reason = m[1]
        text = text[: m.start()].strip()
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
    if text.strip('。.！!…"“”「」') not in ("", PASS):
        result.text = text
    return result


@dataclass
class Decision:
    """一次插话判断，供 WebUI 对照着看：给她看的群聊、她的打分、想说的话和理由。"""

    ts: int
    threshold: float
    outcome: str  # 发出 / 没达到阈值 / 不想说 / 她已经说过话 / 出错：……
    score: float | None = None
    text: str = ""
    reason: str = ""
    reply_to: int = 0  # 引用了 context 里的第几条，0 表示没引用
    at: str = ""  # @ 的人，「名字(QQ)」
    context: list[dict] = field(default_factory=list)


def decision_of(
    said: Interjection, entries: list[LogEntry], threshold: float, outcome: str
) -> Decision:
    """把这次给她看的群聊（编号与提示词一致）和她的回答整理成一条记录。"""
    recent = entries[-CONTEXT_SIZE:]
    return Decision(
        ts=int(datetime.now().timestamp()),
        threshold=threshold,
        outcome=outcome,
        score=said.score,
        text=said.text,
        reason=said.reason,
        reply_to=recent.index(said.reply_to) + 1 if said.reply_to else 0,
        at=f"{said.at.name}({said.at.qq})" if said.at else "",
        context=[
            {
                "n": n,
                "time": datetime.fromtimestamp(e.ts).strftime("%H:%M"),
                "name": e.name,
                "qq": e.qq,
                "text": e.text,
                "is_self": e.is_self,
            }
            for n, e in enumerate(recent, 1)
        ],
    )


class DecisionLog:
    """每次插话判断的记录。按作用域、按月分文件（`<scope>/<YYYY-MM>.jsonl`），只追加。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def append(self, scope: str, decision: Decision) -> None:
        folder = self.root / scope
        folder.mkdir(exist_ok=True)
        month = datetime.fromtimestamp(decision.ts).strftime("%Y-%m")
        with (folder / f"{month}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")

    def scopes(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def query(
        self, scope: str, start: date | None, end: date | None, limit: int
    ) -> tuple[list[Decision], bool]:
        """按时间倒序返回，起止日期都包含在内；第二项表示是否被截断。"""
        first = start.strftime("%Y-%m") if start else ""
        last = end.strftime("%Y-%m") if end else "9999-99"
        rows: list[Decision] = []
        for path in (self.root / scope).glob("*.jsonl"):
            if not first <= path.stem <= last:
                continue
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        d = Decision(**json.loads(line))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    day = datetime.fromtimestamp(d.ts).date()
                    if (start and day < start) or (end and day > end):
                        continue
                    rows.append(d)
        rows.sort(key=lambda d: d.ts, reverse=True)
        return rows[:limit], len(rows) > limit
