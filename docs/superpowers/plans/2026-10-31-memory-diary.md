# 迷迭香的记忆与日记 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把插件的记忆从"每人一串字符串、满了丢最早的"改成"跟人走的博士画像 + 每作用域每天一篇日记"，白天自动注入、夜间自动整理。

**Architecture:** 三个不依赖 AstrBot 的纯 Python 模块：`memory.py`（画像 / 群信息 / 可见性 / 迁移 / 注入文本）、`diary.py`（当日记录、抽样、日记存储与关键词搜索）、`nightly.py`（夜间写日记与整理，模型调用以回调注入）。`main.py` 负责 AstrBot 钩子、工具和定时循环。纯逻辑全部用 pytest 覆盖，AstrBot 相关部分在真实 Bot 上手动验证。

**Tech Stack:** Python ≥3.12，AstrBot ≥4.29（插件 API），pytest（dev），ruff（已有）。

**Spec:** `docs/superpowers/specs/2026-10-31-memory-diary-design.md` —— 实现前先通读。本计划中"设计 x.y"指该文档的章节。

## 执行记录（2026-10-31）

- T1–T7 的实现、配置、人格及 README 更新已完成，版本为 `1.1.0`。
- 验证采用 21 个核心用例，覆盖可见性、来源与编号、迁移、记录截点与去重、抽样、检索、夜间失败隔离及取消；没有逐步执行预期失败的测试阶段。
- `uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .` 均通过。
- 按用户要求，所有 Commit 步骤跳过，改动保持未提交。
- AstrBot 接口已按 4.29.3 源码核对；当前仓库没有运行中的 Bot，Task 7 的真实群聊、对话历史和重载手动验证仍需在 Bot 上执行。
- 当日记录通常追加写入；为满足失败堆积的硬上限要求，文件超过 `max_entries * 2` 行时收紧到最近 `max_entries` 行。纯文本提取只读取 `Comp.Plain`，不读取图片或引用的占位说明。

## Global Constraints

- AstrBot ≥ 4.29，平台 aiocqhttp；不新增运行时依赖（无向量模型、分词库、数据库）；数据为 JSON / JSONL 文件。
- `memory.py`、`diary.py`、`nightly.py` **不得 import astrbot**；它们之间可用相对导入（`from .memory import ...`）。
- 数据目录沿用 `main.py` 的 `DATA_DIR = StarTools.get_data_dir(PLUGIN_NAME)`。
- JSON 写入沿用现有"写 `.tmp` 再 `replace`"的原子落盘方式。
- 日期一律 `YYYY-MM-DD` 字符串；未知日期为空字符串 `""`，展示为"很久以前"。
- 作用域字符串沿用 `main.scope_of`：群聊为群号，私聊为 `private_<QQ号>`。
- 来源字符串：`"private"` 或 `"group:<群号>"`；`sources` 含 `"private"` 即视为私聊事实。
- 代码风格沿用现有：中文注释、日志前缀 `[rmts]`、工具的使用规则写在工具 docstring 里。
- 配置默认值：`memory_max_length=500`、`diary_time="04:00"`、`memory_provider_id=""`、`diary_inject_count=1`、`diary_input_max_chars=20000`。
- 群消息监听**不能** `stop_event()`、不能 `yield` 结果（已核对：AstrBot 4.29.3 中监听器只置 `is_wake`，不置 `is_at_or_wake_command`，不会触发 LLM）。
- 测试运行：`uv run pytest -q`；收尾：`uv run ruff check .` 与 `uv run ruff format --check .` 通过。

## Review Focus

1. **夜间模型返回坏 JSON / 引用不存在的编号 / 返回不该改的 QQ 号**：不崩溃；坏 JSON 时该作用域不删当天记录；无效操作逐条忽略。→ Task 5 测试 `test_parse_ops_tolerates_garbage`、`test_process_scope_bad_json_keeps_log`、`test_apply_ignores_foreign_qq`。
2. **整理进行中又来了新消息**：截止时间之后的记录不能被删。→ Task 3 测试 `test_clear_keeps_entries_after_cutoff`。
3. **群作用域的夜间整理把私聊事实带出来**：群作用域的模型输入里不能出现私聊事实；对私聊事实的操作被忽略。→ Task 1 `test_group_scope_ops_cannot_touch_private`，Task 5 `test_group_prompt_excludes_private_facts`。
4. **首次运行 / 文件缺失 / 迁移重复执行**：空数据目录能正常启动；迁移只执行一次（旧文件改名 `.bak`）。→ Task 2 `test_migration_runs_once`、`test_empty_dir`。
5. **插件重载或禁用时夜间任务正在运行**：任务被取消、不留半写的文件。→ Task 7 手动验证步骤 + 原子写保证。

---

### Task 1: 画像与群信息存储、可见性规则

**Files:**
- Rewrite: `memory.py`（整体替换旧实现，旧 `MemoryStore.add/get` 删除）
- Create: `tests/conftest.py`, `tests/test_memory.py`
- Modify: `pyproject.toml`（dev 依赖加 pytest：`uv add --dev pytest`）

**Interfaces:**
- Produces（`memory.py`）：
  ```python
  @dataclass
  class Fact:
      id: int
      text: str
      date: str            # "" 表示未知
      sources: list[str]   # "private" / "group:<群号>"

  @dataclass
  class Profile:
      name: str = ""
      impression: str = ""
      facts: list[Fact] = field(default_factory=list)
      next_id: int = 1

  @dataclass
  class FactOps:
      add: list[str] = field(default_factory=list)
      update: dict[int, str] = field(default_factory=dict)
      delete: list[int] = field(default_factory=list)
      merge: list[tuple[list[int], str]] = field(default_factory=list)

  def source_of(scope: str) -> str
  def is_private_fact(fact: Fact) -> bool
  def total_length(facts: list[Fact]) -> int          # sum(len(f.text))
  def visible_facts(profile: Profile, *, scope: str, is_self: bool) -> list[Fact]

  class MemoryStore:
      def __init__(self, data_dir: Path, max_length: int = 500) -> None
      max_length: int
      def doctor(self, qq: str) -> Profile | None        # 返回深拷贝
      def group(self, group_id: str) -> list[Fact]       # 返回深拷贝，无则 []
      async def add_doctor_facts(self, qq: str, texts: list[str], *, scope: str, today: str, name: str = "") -> list[int]
      async def add_group_facts(self, group_id: str, texts: list[str], *, today: str) -> list[int]
      async def forget_doctor_facts(self, qq: str, ids: list[int]) -> list[int]   # 返回实际删掉的编号
      async def apply_doctor_ops(self, qq: str, ops: FactOps, *, scope: str, today: str, impression: str | None = None, name: str = "") -> None
      async def apply_group_ops(self, group_id: str, ops: FactOps, *, today: str) -> None
  ```
- 存储文件：`data_dir / "memory.json"`，结构 `{"doctors": {qq: {name, impression, next_id, facts: [...]}}, "groups": {gid: {next_id, facts: [...]}}}`。所有写操作在一个 `asyncio.Lock` 内并立即落盘。

- [x] **Step 1: 写 `tests/conftest.py`**，把仓库根目录注册为包 `rmts`，使测试可 `from rmts.memory import ...` 且模块间相对导入可用：

  ```python
  import sys, types
  from pathlib import Path

  ROOT = Path(__file__).resolve().parent.parent
  pkg = types.ModuleType("rmts")
  pkg.__path__ = [str(ROOT)]
  sys.modules.setdefault("rmts", pkg)
  ```
  异步方法在测试里用 `asyncio.run(...)` 调用，不引入 pytest-asyncio。

- [x] **Step 2: 写失败测试 `tests/test_memory.py`**

  ```python
  def test_source_of():
      assert source_of("123") == "group:123"
      assert source_of("private_42") == "private"

  def test_add_and_persist(tmp_path):
      s = MemoryStore(tmp_path)
      ids = asyncio.run(s.add_doctor_facts("42", ["生日5月1日"], scope="123", today="2026-10-31", name="小明"))
      assert ids == [1]
      s2 = MemoryStore(tmp_path)
      p = s2.doctor("42")
      assert p.name == "小明" and p.facts[0].sources == ["group:123"] and p.facts[0].date == "2026-10-31"

  def test_add_does_not_drop_when_over_limit(tmp_path):
      s = MemoryStore(tmp_path, max_length=5)
      asyncio.run(s.add_doctor_facts("42", ["一二三四五六", "七八九十"], scope="1", today="2026-10-31"))
      assert len(s.doctor("42").facts) == 2

  def test_visibility_rules():
      p = Profile(facts=[Fact(1, "私", "", ["private"]), Fact(2, "A群", "", ["group:A"]),
                         Fact(3, "B群", "", ["group:B"]), Fact(4, "混", "", ["group:A", "private"])])
      ids = lambda fs: [f.id for f in fs]
      assert ids(visible_facts(p, scope="private_42", is_self=True)) == [1, 2, 3, 4]
      assert ids(visible_facts(p, scope="private_42", is_self=False)) == []
      assert ids(visible_facts(p, scope="A", is_self=True)) == [2, 3]
      assert ids(visible_facts(p, scope="A", is_self=False)) == [2]
      assert ids(visible_facts(p, scope="C", is_self=False)) == []

  def test_forget_only_existing(tmp_path): ...   # 删除 [1, 99] 返回 [1]，事实列表少一条

  def test_apply_ops_sources_and_dates(tmp_path):
      # 先在 A 群加 #1 "住广州"、#2 "爱吃甜"，在 B 群加 #3 "爱喝奶茶"
      # 在 B 群 apply：update {1: "住上海"}，merge [([2, 3], "爱吃甜、爱喝奶茶")]，add ["下周考试"]，impression="很温柔"
      # 断言：#1 text=="住上海" 且 sources == ["group:A", "group:B"] 且 date==today
      #       合并得到的新事实 sources == ["group:A", "group:B"]，#2 #3 已不存在
      #       新增事实 sources == ["group:B"]；impression == "很温柔"；next_id 单调递增、编号不复用

  def test_group_scope_ops_cannot_touch_private(tmp_path):
      # 私聊里加 #1 "失恋了"（sources ["private"]）
      # 在群 A apply：update {1: "x"}，delete [1]，merge [([1], "y")]
      # 断言 #1 原样存在、没有新增 "x"/"y"

  def test_merge_with_private_becomes_private(tmp_path):
      # 私聊作用域里 merge 一条 private 事实与一条 group:A 事实 → 新事实 sources 含 "private"

  def test_group_facts(tmp_path): ...   # add_group_facts / apply_group_ops 的增删改合并，sources 恒为 []
  ```

- [ ] **Step 3: 运行确认失败** — `uv run pytest tests/test_memory.py -q`，预期 ImportError / 失败。（未单独执行，见执行记录）

- [x] **Step 4: 实现 `memory.py`。** 要点：
  - `apply_doctor_ops`：只认该博士已有的编号；`scope` 非私聊时，涉及 `is_private_fact` 的编号一律忽略（update / delete / merge 中只要有一个私聊编号，整个该条 merge 忽略）。
  - 来源计算全部在代码里：add → `[source_of(scope)]`；update → 原 sources ∪ `{source_of(scope)}`；merge → 被合并事实 sources 的并集 ∪ `{source_of(scope)}`。sources 去重并排序后存储。add / update / merge 的 `date` 均为 `today`。
  - `impression` 为非空字符串时覆盖；`name` 非空时更新。
  - 编号由 `next_id` 发放，删除后不复用。
  - 群信息事实的 `sources` 恒为 `[]`。

- [x] **Step 5: 运行通过** — `uv run pytest tests/test_memory.py -q`，全部 PASS。

- [ ] **Step 6: Commit** — `git add memory.py tests/ pyproject.toml uv.lock && git commit -m "Rewrite memory store with per-doctor profiles and visibility rules"`（按用户要求跳过，不提交）

---

### Task 2: 旧数据迁移与注入文本

**Files:**
- Modify: `memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: Task 1 全部。
- Produces：
  ```python
  # MemoryStore.__init__ 内部在加载后调用 self._migrate_legacy()（不对外）
  def render_memory_block(
      *,
      speaker_qq: str,
      speaker_name: str,
      speaker: Profile | None,
      speaker_facts: list[Fact],
      mentioned: list[tuple[str, Profile, list[Fact]]],   # (qq, profile, 已过滤的事实)
      group_facts: list[Fact],
      diaries: list[tuple[str, str]],                     # (日期, 正文)，按时间顺序
  ) -> str
  ```

- [x] **Step 1: 写失败测试**

  ```python
  def test_migration(tmp_path):
      (tmp_path / "rosmontis_memory_123.json").write_text(json.dumps({
          "42": {"max_length": 300, "memories": ["住广州"]},
          "123": {"max_length": 300, "memories": ["小红和小明是室友"]}}), encoding="utf-8")
      (tmp_path / "rosmontis_memory_private_42.json").write_text(json.dumps({
          "42": {"max_length": 300, "memories": ["失恋了"]},
          "private_42": {"max_length": 300, "memories": ["怕黑"]}}), encoding="utf-8")
      s = MemoryStore(tmp_path)
      texts = {f.text: f.sources for f in s.doctor("42").facts}
      assert texts == {"住广州": ["group:123"], "失恋了": ["private"], "怕黑": ["private"]}
      assert [f.text for f in s.group("123")] == ["小红和小明是室友"]
      assert all(f.date == "" for f in s.doctor("42").facts)
      assert (tmp_path / "rosmontis_memory_123.json.bak").exists()
      assert not (tmp_path / "rosmontis_memory_123.json").exists()

  def test_migration_runs_once(tmp_path):
      # 同上数据，构造两次 MemoryStore，事实数量不翻倍

  def test_empty_dir(tmp_path):
      s = MemoryStore(tmp_path)
      assert s.doctor("1") is None and s.group("1") == []

  def test_render_block():
      # speaker 有印象 + 两条事实（一条 date=""），一个 mentioned，一条群信息，一篇日记
      # 断言输出：包含 "迷迭香终端里的记录"、"#1"、"很久以前"、印象文本、被提到的博士 QQ、群信息文本、日记日期与正文

  def test_render_empty():
      assert render_memory_block(speaker_qq="1", speaker_name="a", speaker=None, speaker_facts=[],
                                 mentioned=[], group_facts=[], diaries=[]) == ""
  ```

- [ ] **Step 2: 运行确认失败。**（未单独执行，见执行记录）

- [x] **Step 3: 实现迁移**（设计 4.6）：遍历 `data_dir.glob("rosmontis_memory_*.json")`；作用域 = 文件名去掉前缀；
  - 私聊作用域 `private_<qq>`：所有条目（无论 owner 是 `<qq>` 还是 `private_<qq>`）→ 该 qq 画像，sources `["private"]`；
  - 群作用域：owner == 作用域 → 群信息；其他 owner → 该 owner 画像，sources `["group:<群号>"]`；
  - date `""`；全部写完落盘后，旧文件 `rename` 为 `<原名>.bak`。

- [x] **Step 4: 实现 `render_memory_block`。** 格式（措辞可润色，结构保持）：

  ```
  【迷迭香终端里的记录】
  正在说话的博士 小明(42)：
  印象：总是很温柔
  #1 生日5月1日（2026-10-01）
  #2 住在广州（很久以前）
  话里提到的博士 小红(43)：
  ……
  本群记录：
  #1 小红和小明是室友（很久以前）
  最近的日记：
  2026-10-30：……
  ```
  speaker 无画像且其余皆空时返回 `""`；speaker 无画像但其他部分有内容时写"（还没有记录）"。

- [x] **Step 5: 运行通过。**

- [ ] **Step 6: Commit** — `git commit -am "Migrate legacy memory files and render memory block"`（含新增测试）（按用户要求跳过，不提交）

---

### Task 3: 当日记录与抽样

**Files:**
- Create: `diary.py`
- Create: `tests/test_diary.py`

**Interfaces:**
- Produces（`diary.py`）：
  ```python
  MIN_TEXT_LEN = 2
  def keep_text(text: str, to_her: bool) -> bool     # strip 后为空 → False；to_her 为真时其余都 True；否则 len >= MIN_TEXT_LEN

  @dataclass
  class LogEntry:
      ts: int          # Unix 秒
      qq: str
      name: str
      text: str
      to_her: bool = False
      is_self: bool = False   # 迷迭香自己的回复
      msg_id: str = ""

  class DailyLog:
      def __init__(self, log_dir: Path, max_entries: int = 5000) -> None
      def append(self, scope: str, umo: str, entry: LogEntry) -> None
      def scopes(self) -> list[str]
      def load(self, scope: str, until_ts: int) -> tuple[str, list[LogEntry]]   # (umo, 条目)
      def clear(self, scope: str, until_ts: int) -> None

  def sample_for_diary(entries: list[LogEntry], budget: int) -> list[LogEntry]
  ```
- 存储：`log_dir / f"{scope}.jsonl"`，每行一个 `{"umo": ..., **asdict(entry)}`，`append` 用追加模式写一行（不整文件重写）。

- [x] **Step 1: 写失败测试**

  ```python
  def test_keep_text():
      assert keep_text("  ", True) is False
      assert keep_text("1", False) is False and keep_text("1", True) is True
      assert keep_text("哈哈", False) is True

  def test_load_merges_to_her_by_msg_id(tmp_path):
      log = DailyLog(tmp_path)
      log.append("123", "umo:g", LogEntry(10, "42", "小明", "迷迭香在吗", msg_id="m1"))
      log.append("123", "umo:g", LogEntry(11, "42", "小明", "迷迭香在吗", to_her=True, msg_id="m1"))
      log.append("123", "umo:g", LogEntry(12, "bot", "迷迭香", "在的……", is_self=True))
      umo, es = log.load("123", until_ts=100)
      assert umo == "umo:g" and len(es) == 2 and es[0].to_her and es[1].is_self

  def test_load_respects_cutoff_and_cap(tmp_path):
      # max_entries=3，写 5 条 ts=1..5，load(until_ts=4) → ts [2,3,4]

  def test_clear_keeps_entries_after_cutoff(tmp_path):
      # 写 ts=1,2,3；clear(until_ts=2)；load(until_ts=100) 只剩 ts=3
      # 全部清空时文件被删除，scopes() 不再包含该作用域

  def test_sample_keeps_her_conversation_and_spreads_others():
      her = [LogEntry(i * 100, "42", "a", "对她说" * 5, to_her=True) for i in range(3)]
      others = [LogEntry(i, "43", "b", "群聊消息内容") for i in range(1000)]
      out = sample_for_diary(her + others, budget=2000)
      assert all(e in out for e in her)
      assert sum(len(e.text) for e in out) <= 2000
      kept = [e.ts for e in out if not e.to_her]
      assert kept[0] < 100 and kept[-1] > 900          # 覆盖全天，而非只取末尾
      assert [e.ts for e in out] == sorted(e.ts for e in out)

  def test_sample_under_budget_returns_all(): ...
  def test_sample_her_alone_over_budget():
      # 仅 to_her/is_self 条目就超预算 → 保留时间上最新、总字数 ≤ budget 的那部分
  ```

- [ ] **Step 2: 运行确认失败。**（未单独执行，见执行记录）

- [x] **Step 3: 实现。**
  - `load`：只取 `ts <= until_ts`；有 `msg_id` 的按 `msg_id` 合并（`to_her` 取或，保留首次出现的时间位置）；按 `ts` 排序；超过 `max_entries` 取最后 `max_entries` 条；`umo` 取最后一行的值；文件不存在返回 `("", [])`。坏行（JSON 解析失败）跳过。
  - `clear`：重写文件只保留 `ts > until_ts` 的行（原子写），为空则删除文件。
  - `sample_for_diary`：必留 = `to_her or is_self`；其余在剩余预算内**等间距**抽样，样本数从 `剩余预算 // 平均长度` 开始，若超预算则逐步减少到不超；结果按 `ts` 排序。

- [x] **Step 4: 运行通过。**

- [ ] **Step 5: Commit** — `git add diary.py tests/test_diary.py && git commit -m "Add daily message log with diary sampling"`（按用户要求跳过，不提交）

---

### Task 4: 日记存储与关键词搜索

**Files:**
- Modify: `diary.py`
- Test: `tests/test_diary.py`

**Interfaces:**
- Produces：
  ```python
  @dataclass
  class DiaryEntry:
      date: str
      text: str
      summary: str
      tags: list[str]

  @dataclass
  class SearchHit:
      entry: DiaryEntry
      snippet: str

  class DiaryStore:
      def __init__(self, diary_dir: Path) -> None
      def put(self, scope: str, entry: DiaryEntry) -> None            # 同日期覆盖；按日期升序存
      def latest(self, scope: str, n: int) -> list[DiaryEntry]        # 最近 n 篇，按日期升序；n<=0 返回 []
      def get(self, scope: str, date: str) -> DiaryEntry | None
      def search(self, scope: str, keywords: list[str], start: str = "", end: str = "", limit: int = 5) -> list[SearchHit]
  ```
- 存储：`diary_dir / f"{scope}.json"`，一个按日期升序的列表。

- [x] **Step 1: 写失败测试**

  ```python
  def test_put_get_latest(tmp_path):
      # put 三篇（乱序），latest(n=2) 为最近两篇且升序；同日期再 put 覆盖；作用域互不可见

  def test_search_ranking(tmp_path):
      d = DiaryStore(tmp_path)
      d.put("1", DiaryEntry("2026-10-01", "今天小明抽卡出了新干员", "小明抽卡", ["小明", "抽卡"]))
      d.put("1", DiaryEntry("2026-10-05", "下雨了，大家聊天气", "下雨", ["天气"]))
      d.put("1", DiaryEntry("2026-10-09", "小明说考试很累", "小明考试", ["小明", "考试"]))
      hits = d.search("1", ["小明", "抽卡"])
      assert [h.entry.date for h in hits] == ["2026-10-01", "2026-10-09"]   # 命中关键词数优先，其次新近
      assert "抽卡" in hits[0].snippet
      assert d.search("1", ["小明"], start="2026-10-05")[0].entry.date == "2026-10-09"
      assert d.search("1", ["不存在"]) == []
      assert d.search("2", ["小明"]) == []

  def test_search_case_insensitive_and_limit(tmp_path): ...   # 英文关键词大小写不敏感；limit 生效
  ```

- [ ] **Step 2: 运行确认失败。**（未单独执行，见执行记录）

- [x] **Step 3: 实现。** 打分：命中的**不同关键词数**（在正文、摘要、标签任一处子串命中即算）为主键；标签命中的关键词数为次键；日期新近为第三键；零命中不返回。片段：取正文中第一个命中位置前后各约 30 字，正文未命中则用摘要。空关键词（strip 后）忽略。

- [x] **Step 4: 运行通过。**

- [ ] **Step 5: Commit** — `git commit -am "Add diary store with keyword search"`（按用户要求跳过，不提交）

---

### Task 5: 夜间整理

**Files:**
- Create: `nightly.py`
- Create: `tests/test_nightly.py`

**Interfaces:**
- Consumes: `MemoryStore`, `FactOps`, `Fact`, `source_of`, `visible_facts`, `total_length`（Task 1）；`DailyLog`, `LogEntry`, `sample_for_diary`, `DiaryStore`, `DiaryEntry`（Task 3、4）。
- Produces（`nightly.py`）：
  ```python
  LLM = Callable[[str, str], Awaitable[str]]     # (system_prompt, prompt) -> 回复文本

  def next_run(now: datetime, hhmm: str) -> datetime        # 下一个 hh:mm（严格晚于 now）
  def diary_date_for(run_at: datetime) -> str              # (run_at - 12 小时).date().isoformat()
  def parse_json_reply(text: str) -> dict                  # 去掉 ```json 围栏后 json.loads；非 dict 或失败抛 ValueError
  def parse_ops(raw: object) -> FactOps                    # 宽容解析，坏条目跳过

  async def process_scope(
      scope: str, *, memory: MemoryStore, logs: DailyLog, diaries: DiaryStore,
      llm: LLM, persona_prompt: str, run_at: datetime, budget: int,
  ) -> None                                                # 失败抛异常；成功后清掉 run_at 之前的记录

  async def run_nightly(
      *, memory: MemoryStore, logs: DailyLog, diaries: DiaryStore,
      make_llm: Callable[[str], Awaitable[LLM]],           # 参数为 umo
      persona_for: Callable[[str], Awaitable[str]],        # 参数为 umo
      run_at: datetime, budget: int,
  ) -> dict[str, str]                                      # 失败的 {作用域: 错误信息}，单个作用域失败不影响其他
  ```
- 模型输出 JSON 约定：
  - 写日记：`{"text": str, "summary": str, "tags": [str, ...]}`
  - 整理：`{"doctors": {"<qq>": {"add": [str], "update": [{"id": int, "text": str}], "delete": [int], "merge": [{"ids": [int], "text": str}], "impression": str}}, "group": {"add": ..., "update": ..., "delete": ..., "merge": ...}}`

- [x] **Step 1: 写失败测试**（用假的 `llm`：按 system prompt 是否含人格文本区分两次调用，返回预设 JSON，并记录收到的 prompt）

  ```python
  def test_next_run():
      assert next_run(datetime(2026, 10, 31, 3, 0), "04:00") == datetime(2026, 10, 31, 4, 0)
      assert next_run(datetime(2026, 10, 31, 4, 0), "04:00") == datetime(2026, 11, 1, 4, 0)

  def test_diary_date_for():
      assert diary_date_for(datetime(2026, 11, 1, 4, 0)) == "2026-10-31"
      assert diary_date_for(datetime(2026, 10, 31, 23, 30)) == "2026-10-31"

  def test_parse_json_reply_fenced():
      assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
      with pytest.raises(ValueError): parse_json_reply("不是 JSON")

  def test_parse_ops_tolerates_garbage():
      ops = parse_ops({"add": ["x", 3, ""], "update": [{"id": "2", "text": "y"}, {"id": "a"}],
                       "delete": [1, "z"], "merge": [{"ids": [1, 2], "text": "m"}, {"ids": "bad"}]})
      assert ops.add == ["x"] and ops.update == {2: "y"} and ops.delete == [1] and ops.merge == [([1, 2], "m")]

  def test_process_scope_happy_path(tmp_path):
      # 群 "123"：一条小明对她说的 "我搬到上海了"(to_her)、她的回复、一条群聊
      # 小明已有 A 群事实 #1 "住广州"
      # 假 llm：日记返回 {"text": "...", "summary": "...", "tags": ["小明"]}
      #         整理返回 {"doctors": {"42": {"update": [{"id": 1, "text": "住上海"}], "impression": "很温柔"}}, "group": {"add": ["今天大家在聊卡池"]}}
      # 断言：diaries.get("123", diary_date_for(run_at)) 存在；小明 #1 == "住上海" 且 sources 含 "group:123"
      #       群信息新增一条；logs.load("123", 10**12) 为空

  def test_group_prompt_excludes_private_facts(tmp_path):
      # 小明有私聊事实 "失恋了"；群作用域整理时，送给 llm 的 prompt 中不含 "失恋了"

  def test_memory_step_uses_only_her_conversation(tmp_path):
      # 群聊中别人说 "小明住在北京"（非 to_her）；整理 prompt 中不含该句，日记 prompt 中含该句

  def test_apply_ignores_foreign_qq(tmp_path):
      # 当天只有 42 对她说话；llm 返回 doctors 里有 "99" 的 add → 99 没有画像

  def test_process_scope_bad_json_keeps_log(tmp_path):
      # 日记 llm 返回非 JSON → process_scope 抛异常；当天记录仍在；画像未变

  def test_skip_memory_step_without_to_her(tmp_path):
      # 只有群聊、没人对她说话 → 只调用一次 llm（写日记）

  def test_run_nightly_isolates_failures(tmp_path):
      # 两个作用域，一个 llm 抛异常 → 返回 {该作用域: 错误}，另一个照常完成
  ```

- [ ] **Step 2: 运行确认失败。**（未单独执行，见执行记录）

- [x] **Step 3: 实现 `process_scope`。** 流程（设计 4.4）：
  1. `cutoff = int(run_at.timestamp())`；`umo, entries = logs.load(scope, cutoff)`；为空直接返回。
  2. **写日记**：`system = persona_prompt + "\n\n" + DIARY_TASK`；prompt 含前一篇日记（`diaries.latest(scope, 1)`）和 `sample_for_diary(entries, budget)` 的逐行文本（`HH:MM 名字(QQ)[对你说]: 内容`，她自己的回复标为"你"）。解析结果 → `DiaryEntry(date=diary_date_for(run_at), ...)`，`text` 为空视为失败，`tags` 截到 5 个；`diaries.put`。
  3. **整理**（仅当存在 `to_her` 条目）：对话素材 = `to_her or is_self` 的条目，超过 `budget` 字时保留最新的部分。涉及的博士 = 私聊作用域时为 `scope` 中的 QQ；群作用域时为 `to_her` 条目的发送者。每位博士的已有事实取 `visible_facts(profile, scope=scope, is_self=True)`（群里自然排除私聊事实），连同编号、印象一并给出；群作用域附上群信息。system 为不含人格的 `MEMORY_TASK`。解析后只对"涉及的博士"调用 `memory.apply_doctor_ops(..., scope=scope, today=diary_date_for(run_at), impression=..., name=该博士当天最后用的名字)`；群作用域再调用 `apply_group_ops`。
  4. `logs.clear(scope, cutoff)`。
  任一步异常直接向上抛（不清记录）。

  `DIARY_TASK` 必须要求：以迷迭香第一人称写当天日记，简短；群里不是对她说的话只用来描绘氛围和她看到的事；不写其他博士的隐私；摘要一句话；3～5 个标签，标签用具体的人名 / 事件 / 话题词；只输出上述 JSON。
  `MEMORY_TASK` 必须要求：只依据对话提取关于博士本人的长期事实；用编号引用已有事实做 update / delete / merge，不要重写没变化的；把"明天""下周"等换算成具体日期（给出今天日期）；删除已过期且无长期意义的；每位博士事实总字数超过 `memory.max_length` 时合并或删去不重要的（在 prompt 中给出当前字数和上限）；印象只写感受和态度，不写具体私事；闲聊、玩笑、一次性提问不记；只输出上述 JSON。

- [x] **Step 4: 实现 `run_nightly`**：对 `logs.scopes()` 逐个 `process_scope`，`umo` 取自 `logs.load`（为空则跳过）；`make_llm(umo)`、`persona_for(umo)` 每个作用域调用一次；捕获每个作用域的异常存入返回值。

- [x] **Step 5: 运行通过** — `uv run pytest -q` 全部 PASS。

- [ ] **Step 6: Commit** — `git add nightly.py tests/test_nightly.py && git commit -m "Add nightly diary writing and memory consolidation"`（按用户要求跳过，不提交）

---

### Task 6: 接入 AstrBot：记录、注入、工具

**Files:**
- Modify: `main.py`（「记忆」一节整体替换；新增钩子；`get_weather` docstring）

**Interfaces:**
- Consumes: Task 1～4 的全部公开接口。
- AstrBot API（已在 4.29.3 源码核对）：
  - `@filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)`；`event.message_str`（纯文本）；`event.message_obj.message_id`；`event.unified_msg_origin`
  - `@filter.on_llm_request()` → `(event, req: ProviderRequest)`；`req.system_prompt`（**不会**存进对话历史：`_save_to_history` 跳过首条 system 消息）
  - `@filter.on_llm_response()` → `(event, resp: LLMResponse)`，`resp.completion_text`；每次 agent 运行结束触发一次
  - `Comp.At.qq`（`"all"` 表示全体）、`Comp.Reply.sender_id`
  - `from astrbot.api.provider import LLMResponse`

- [x] **Step 1: `__init__` 中创建存储**：
  `self.memory = MemoryStore(DATA_DIR, int(config.get("memory_max_length", 500)))`、`self.logs = DailyLog(DATA_DIR / "logs")`、`self.diaries = DiaryStore(DATA_DIR / "diaries")`（目录 `mkdir(parents=True, exist_ok=True)` 放在各自构造函数里）。

- [x] **Step 2: 群消息记录** `async def record_group_message(self, event)`：发送者是机器人自己则跳过；`text = event.message_str.strip()`；`keep_text(text, False)` 为真时 `self.logs.append(scope, umo, LogEntry(int(time.time()), sender_id, sender_name, text, msg_id=message_id))`。不 `yield`、不 `stop_event()`。

- [x] **Step 3: 新增 `on_llm_request` 钩子 `inject_memory(self, event, req)`**（与现有 `add_sender_info` 并存；用 event extra `rmts_memory` 防同一事件重复处理）：
  1. 记录对她说的话：戳一戳（`EXTRA_POKE`）时 `text = "（戳了戳你）"`，否则 `event.message_str.strip()`；`keep_text(text, True)` 为真则追加 `to_her=True` 的 `LogEntry`（`msg_id` 同上）。
  2. 组注入块：`scope = scope_of(event)`；说话者事实 `visible_facts(profile, scope=scope, is_self=True)`；群聊时从 `event.get_messages()` 收集 `Comp.At.qq`（排除 `"all"` 和机器人自己）与 `Comp.Reply.sender_id`（排除 0 / 空 / 机器人自己），去重、排除说话者、最多 3 人，有画像的取 `visible_facts(..., is_self=False)`；群聊时 `self.memory.group(scope)`；`self.diaries.latest(scope, int(config.get("diary_inject_count", 1)))`。
  3. `block = render_memory_block(...)`，非空时 `req.system_prompt = f"{req.system_prompt or ''}\n\n{block}"`。

- [x] **Step 4: 新增 `on_llm_response` 钩子 `record_reply(self, event, resp)`**：`resp.completion_text` 非空时追加 `LogEntry(ts, event.get_self_id(), "迷迭香", text, is_self=True)`。

- [x] **Step 5: 替换工具**（docstring 按设计 4.3 写具体例子和触发条件；参数类型写法沿用现有 docstring 格式）：
  - `add_doctor_info(info: list, doctor_id: int)`：保留"只能记本人"校验；`add_doctor_facts(str(doctor_id), info, scope=scope, today=date.today().isoformat(), name=event.get_sender_name())`；返回"已悄悄记下"。docstring 要点：博士说起自己的事就悄悄记下，不用提醒也不用告诉博士，例如"我下周考试""我在广州上班""我不吃香菜""叫我小明就好"；闲聊、玩笑、一次性的问题不记；只能记正在说话的博士本人，别人的事用 `add_global_info`。
  - `add_global_info(info: list)`：群聊 → `add_group_facts`；私聊 → `add_doctor_facts(sender, ..., scope=scope)`。
  - `forget_doctor_info(ids: list)`（`array[number]`）：只删说话者本人的 `forget_doctor_facts`；返回删掉了哪些编号或"没有找到这些记录"。
  - `get_doctor_all_info(doctor_id: int)`：私聊中查别人 → "迷迭香不知道这位博士的事"；否则 `visible_facts(profile, scope=scope, is_self=(doctor_id==sender))` + 印象。docstring 改为"查群里另一位博士（没出现在终端记录里的）"。
  - **删除** `get_global_all_info`。
  - `search_diary(keywords: list, start_date: str, end_date: str)`：日期可传空字符串；返回每条"日期｜摘要｜片段"，无结果返回"日记里没有找到"。docstring：博士说"上次""之前""那天""还记得吗"，或提到想不起来的事时，先翻日记再回答；自己补充同义词和相关人名。
  - `read_diary(date: str)`：空字符串 → 最近一篇；返回日期 + 正文。docstring：博士要看日记时，可以按自己的性格决定念多少、要不要念；不要把日记里其他博士的私事念出来。
  - `get_weather` docstring："先读博士的个人记忆和全局记忆找所在地" → "所在地见终端记录，找不到再问博士"。

- [x] **Step 6: 静态检查** — `uv run ruff check . && uv run ruff format --check .` 通过；`uv run pytest -q` 仍全部 PASS。

- [ ] **Step 7: Commit** — `git commit -am "Wire memory injection, logging and diary tools into the plugin"`（按用户要求跳过，不提交）

---

### Task 7: 定时任务、配置、人格与文档

**Files:**
- Modify: `main.py`、`_conf_schema.json`、`persona/rosmontis.md`、`README.md`、`metadata.yaml`

**Interfaces:**
- Consumes: `run_nightly`, `next_run`（Task 5）。
- AstrBot API（已核对）：`await self.context.llm_generate(chat_provider_id=..., system_prompt=..., prompt=...)` 返回 `LLMResponse`（`.completion_text`），不触发插件钩子；`await self.context.get_current_chat_provider_id(umo)`；`(await self.context.persona_manager.get_default_persona_v3(umo))["prompt"]`；`Star.terminate()` 在禁用 / 重载时调用。

- [x] **Step 1: 定时循环**：`__init__` 末尾 `self._nightly_task = asyncio.create_task(self._nightly_loop())`；`_nightly_loop` 循环：`await asyncio.sleep((next_run(datetime.now(), diary_time) - datetime.now()).total_seconds())` → `run_nightly(...)`，对返回的失败逐条 `logger.error(f"[rmts] ...")`，整体再包一层 `except Exception` 记日志后继续循环（`CancelledError` 不吞）。`async def terminate(self)` 取消任务。
  - `make_llm(umo)`：`provider_id = config.get("memory_provider_id") or await self.context.get_current_chat_provider_id(umo)`，返回调用 `llm_generate` 取 `completion_text` 的闭包。
  - `persona_for(umo)`：`get_default_persona_v3(umo)["prompt"]`。
  - 人格：用该会话配置的**默认人格**（README 要求把迷迭香设为默认人格）；不追溯单个对话里手动切换的人格。

- [x] **Step 2: `_conf_schema.json`**：`memory_max_length` 描述改为"每位博士画像 / 每个群信息的字数上限，夜间整理时压到上限内"，默认 500；新增 `diary_time`（string，`04:00`，hint 说明格式 HH:MM、整理前一天的记录）、`memory_provider_id`（string，空=当前会话的聊天模型）、`diary_inject_count`（int，1，0 表示不注入）、`diary_input_max_chars`（int，20000）。

- [x] **Step 3: 人格**：在 `persona/rosmontis.md`「迷迭香的标签」之后加一节角色习惯（设计 4.8 的大意，按文件风格润色）。

- [x] **Step 4: README / metadata**：功能表更新（去掉 `get_global_all_info`，加 `forget_doctor_info`、`search_diary`、`read_diary`）；新增"记忆与日记"小节（画像跟人走、私聊信息只在私聊用、别人提到时只给本群得知的、每晚几点写日记、只处理文本、数据位置、旧数据自动迁移为 `.bak`）；配置表加新项；「人格」一节改为说明习惯描写的作用，并提醒已在 WebUI 建过人格的用户手动同步。`metadata.yaml` 的 `version` 升到 `1.1.0`，描述里"个人与群全局记忆"改为"记忆与日记"。

- [x] **Step 5: 自动检查** — `uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .` 全部通过。

- [ ] **Step 6: 真实 Bot 手动验证**（把 `diary_time` 临时设为两分钟后）：
  1. 有旧记忆的环境启动后，`memory.json` 生成、旧文件变 `.bak`，重载插件不重复迁移。
  2. 群里不 @ 机器人发几条消息 → 机器人不回复；`logs/<群号>.jsonl` 有记录；图片消息不入记录。
  3. @ 机器人说"我搬到上海了" → 回复正常；AstrBot 日志 / 对话历史中**看不到**"迷迭香终端里的记录"（确认注入未进历史）。
  4. 到点后 `diaries/<群号>.json` 出现日记，画像更新，`logs` 中该时刻前的记录被清掉。
  5. 私聊对她说一件事，第二天在群里让别人问"他最近怎么样"，她不说出来。
  6. 让她念日记、搜"上次"的事，工具被调用且只读本作用域。
  7. 夜间任务运行中重载插件 → 无异常堆栈外泄，数据文件完好。

- [ ] **Step 7: Commit** — `git commit -am "Schedule nightly diary, add config, persona habit and docs"`（按用户要求跳过，不提交）
