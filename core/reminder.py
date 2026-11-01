"""终端上的定时提醒：重复规则、下次时间计算、存储和到期判断。"""

import calendar
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

TIME_FORMAT = "%Y-%m-%d %H:%M"
# 每位博士在同一个群或私聊里最多同时挂着几条提醒，防止刷屏
MAX_PER_DOCTOR = 10
# 停机期间错过的提醒，迟到超过这个时间就不再补发
LATE_LIMIT = timedelta(hours=1)
REPEATS = ("daily", "weekdays", "weekends", "weekly", "monthly")
_HOURS = re.compile(r"(\d+)h")

# 到点时让她按人格组织语言的任务说明
REMIND_TASK = """现在是 {now}。博士 {name} 之前请你在这个时候提醒或告诉 TA 一件事，要说的内容：{text}
{weather}
请以迷迭香的口吻直接写出这时要发给博士的话，一到三句，不提“定时任务”“系统”之类的词；附了天气就挑今天要紧的说。只输出这段话。"""
# 她自己记挂的事到点时的任务说明
FOLLOW_UP_TASK = """现在是 {now}。你之前记挂着博士 {name} 的一件事：{text}
{weather}
现在去关心一下 TA。请以迷迭香的口吻直接写出这时要发给博士的话，一到三句，不提“提醒”“定时任务”“系统”之类的词；附了天气就挑今天要紧的说。只输出这段话。"""


class ReminderError(ValueError):
    """提醒参数不合法，消息会原样交给模型。"""


@dataclass
class Reminder:
    id: int
    scope: str
    umo: str
    qq: str
    name: str
    at: str  # 下次到点时间 YYYY-MM-DD HH:MM，按 Bot 所在时区
    repeat: str  # 空为只提醒一次；daily、weekdays、weekends、weekly、monthly 或「N h」
    text: str
    weather: str = ""  # 到点时要查天气的地点
    compose: bool = False  # 到点时让她按人格重新说，而不是原样发送 text
    created: str = ""
    follow_up: bool = False  # 她自己记挂、到时候去关心博士的事，不是博士要求的提醒


def parse_time(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError:
        raise ReminderError(f"时间格式应为 YYYY-MM-DD HH:MM，收到「{value}」") from None


def normalize_repeat(value: str) -> str:
    repeat = value.strip().lower().replace(" ", "")
    if repeat in ("", "none", "once"):
        return ""
    if repeat in REPEATS:
        return repeat
    if (m := _HOURS.fullmatch(repeat)) and 1 <= int(m.group(1)) <= 24:
        return f"{int(m.group(1))}h"
    raise ReminderError(
        f"不支持的重复规则「{value}」，可用：留空、{'、'.join(REPEATS)}，或 1h～24h"
    )


def next_time(at: datetime, repeat: str) -> datetime | None:
    """按重复规则算出 at 之后的下一次时间，不重复时返回 None。"""
    if not repeat:
        return None
    if m := _HOURS.fullmatch(repeat):
        return at + timedelta(hours=int(m.group(1)))
    if repeat == "weekly":
        return at + timedelta(days=7)
    if repeat == "monthly":
        year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
        # 31 号之类在下个月不存在时，落到当月最后一天
        day = min(at.day, calendar.monthrange(year, month)[1])
        return at.replace(year=year, month=month, day=day)
    nxt = at + timedelta(days=1)
    if repeat == "weekdays":
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    elif repeat == "weekends":
        while nxt.weekday() < 5:
            nxt += timedelta(days=1)
    return nxt


def first_time(at: datetime, repeat: str) -> datetime:
    """工作日、周末提醒的首次时间落在不符合的日子时，顺延到符合的那天。"""
    if (repeat == "weekdays" and at.weekday() >= 5) or (
        repeat == "weekends" and at.weekday() < 5
    ):
        return next_time(at - timedelta(days=1), repeat)
    return at


class ReminderStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: list[Reminder] = []
        self._next_id = 1
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._items = [Reminder(**r) for r in data.get("reminders", [])]
            self._next_id = data.get("next_id", 1)

    def _save(self) -> None:
        data = {"next_id": self._next_id, "reminders": [asdict(r) for r in self._items]}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def all(self) -> list[Reminder]:
        return sorted(self._items, key=lambda r: r.at)

    def of(self, scope: str, qq: str) -> list[Reminder]:
        return [r for r in self.all() if r.scope == scope and r.qq == qq]

    def add(
        self,
        *,
        scope: str,
        umo: str,
        qq: str,
        name: str,
        at: str,
        repeat: str,
        text: str,
        weather: str,
        compose: bool,
        now: datetime,
        follow_up: bool = False,
    ) -> Reminder:
        text = text.strip()
        if not text:
            raise ReminderError("提醒内容不能为空")
        repeat = normalize_repeat(repeat)
        when = first_time(parse_time(at), repeat)
        if when <= now:
            raise ReminderError(
                f"提醒时间 {when.strftime(TIME_FORMAT)} 已经过去了，现在是 {now.strftime(TIME_FORMAT)}"
            )
        if len(self.of(scope, qq)) >= MAX_PER_DOCTOR:
            raise ReminderError(
                f"这位博士在这里已经有 {MAX_PER_DOCTOR} 条提醒，先取消一些再设"
            )
        reminder = Reminder(
            id=self._next_id,
            scope=scope,
            umo=umo,
            qq=qq,
            name=name,
            at=when.strftime(TIME_FORMAT),
            repeat=repeat,
            text=text,
            weather=weather.strip(),
            # 要查天气或是她自己记挂的事时，必须到点再组织语言
            compose=compose or bool(weather.strip()) or follow_up,
            created=now.strftime(TIME_FORMAT),
            follow_up=follow_up,
        )
        self._next_id += 1
        self._items.append(reminder)
        self._save()
        return reminder

    def cancel(self, scope: str, qq: str, ids: list[int]) -> list[int]:
        wanted = {int(i) for i in ids}
        removed = [r.id for r in self.of(scope, qq) if r.id in wanted]
        if removed:
            self._items = [r for r in self._items if r.id not in removed]
            self._save()
        return removed

    def pop_due(self, now: datetime) -> tuple[list[Reminder], list[Reminder]]:
        """取出到点的提醒：返回（要发送的，错过太久不补发的）。

        重复提醒顺延到 now 之后的下一次，一次性提醒从存储中移除。
        """
        send, missed = [], []
        changed = False
        for r in list(self._items):
            at = parse_time(r.at)
            if at > now:
                continue
            changed = True
            (send if now - at <= LATE_LIMIT else missed).append(Reminder(**asdict(r)))
            nxt = next_time(at, r.repeat)
            while nxt is not None and nxt <= now:
                nxt = next_time(nxt, r.repeat)
            if nxt is None:
                self._items.remove(r)
            else:
                r.at = nxt.strftime(TIME_FORMAT)
        if changed:
            self._save()
        return send, missed


def remind_task(r: Reminder, now: datetime, weather: str) -> str:
    """到点组织语言时交给模型的任务；weather 是查好的天气或没查到的说明，可为空。"""
    return (FOLLOW_UP_TASK if r.follow_up else REMIND_TASK).format(
        now=now.strftime(TIME_FORMAT), name=r.name, text=r.text, weather=weather
    )


def repeat_label(repeat: str) -> str:
    return {
        "": "只提醒一次",
        "daily": "每天",
        "weekdays": "工作日",
        "weekends": "周末",
        "weekly": "每周",
        "monthly": "每月",
    }.get(repeat, f"每 {repeat[:-1]} 小时")


def describe(r: Reminder) -> str:
    """给模型看的单条提醒说明。"""
    extra = f"，到点查{r.weather}的天气" if r.weather else ""
    if r.follow_up:
        extra += "，你自己记挂的"
    return f"#{r.id} 下次 {r.at}（{repeat_label(r.repeat)}{extra}）：{r.text}"
