"""访问控制：沿用 AstrBot「访问控制」里的白名单，不在白名单里的会话不触发插件的功能。"""

PRIVATE = "private_"


def allowed(settings: dict, admins: list, *, scope: str, umo: str) -> bool:
    """按作用域判断，规则和 AstrBot 的白名单检查一致。

    settings 是 AstrBot 配置里的 platform_settings。白名单没开或为空时不检查；
    会话 ID（umo）或群号在名单里即可；WebChat 豁免。管理员豁免只看私聊：
    群里的功能（记录、插话、日记）属于整个群，不因管理员在群里说话而开启。
    """
    ids = {str(i).strip() for i in settings.get("id_whitelist") or []} - {""}
    if not settings.get("enable_id_white_list") or not ids:
        return True
    if umo in ids or umo.startswith("webchat:"):
        return True
    if not scope.startswith(PRIVATE):
        return scope in ids
    return bool(settings.get("wl_ignore_admin_on_friend")) and scope[
        len(PRIVATE) :
    ] in {str(a) for a in admins}
