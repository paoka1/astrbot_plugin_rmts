from rmts.core.access import allowed

ON = {
    "enable_id_white_list": True,
    "id_whitelist": ["100", "qq:FriendMessage:7", " "],
    "wl_ignore_admin_on_friend": True,
}


def test_whitelist_off_or_empty_allows_everything():
    for settings in (
        {},
        {**ON, "enable_id_white_list": False},
        {**ON, "id_whitelist": [""]},
    ):
        assert allowed(settings, [], scope="200", umo="qq:GroupMessage:200")


def test_groups_need_group_id_or_umo():
    assert allowed(ON, [], scope="100", umo="qq:GroupMessage:100")
    assert not allowed(ON, ["9"], scope="200", umo="qq:GroupMessage:200")
    assert allowed(
        {**ON, "id_whitelist": ["qq:GroupMessage:200"]},
        [],
        scope="200",
        umo="qq:GroupMessage:200",
    )
    assert allowed(ON, [], scope="200", umo="webchat:GroupMessage:200")


def test_private_chats_need_umo_or_admin_exemption():
    assert allowed(ON, [], scope="private_7", umo="qq:FriendMessage:7")
    # 私聊的会话 ID 不是群号，只写 QQ 号不算
    assert not allowed(
        {**ON, "id_whitelist": ["8"]}, [], scope="private_8", umo="qq:FriendMessage:8"
    )
    assert allowed(ON, ["9"], scope="private_9", umo="qq:FriendMessage:9")
    off = {**ON, "wl_ignore_admin_on_friend": False}
    assert not allowed(off, ["9"], scope="private_9", umo="qq:FriendMessage:9")
