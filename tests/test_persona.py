from rmts.core.persona import PersonaState, digest, sync_action

OLD = "# 迷迭香\n旧版"
NEW = "# 迷迭香\n新版"


def test_create_when_missing():
    assert sync_action(NEW, None, "") == "create"


def test_keep_when_same_ignoring_line_endings():
    assert sync_action(NEW, "# 迷迭香\r\n新版\n", digest(OLD)) == "keep"
    # 没有同步记录时内容一致也算最新
    assert sync_action(NEW, NEW, "") == "keep"


def test_update_when_user_did_not_edit():
    assert sync_action(NEW, OLD, digest(OLD)) == "update"


def test_edited_when_user_changed_it():
    assert sync_action(NEW, OLD + "\n博士自己加的", digest(OLD)) == "edited"
    # 没有同步记录、内容又不同（例如插件数据被删）时不覆盖
    assert sync_action(NEW, OLD, "") == "edited"


def test_state_roundtrip(tmp_path):
    state = PersonaState(tmp_path / "persona.json")
    assert state.synced() == ""
    state.save(NEW)
    assert PersonaState(tmp_path / "persona.json").synced() == digest(NEW)
