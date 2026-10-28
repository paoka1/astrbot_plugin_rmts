# rmts_astrbot

部署在 `ubuntu@192.168.12.136` 上的 QQ 机器人（SnowLuma + AstrBot）的插件代码。

## 目录

- `plugins/rmts_tools/`：从 rmts（NoneBot2 迷迭香机器人）迁移的 AstrBot 工具插件
  - 干员生日、干员档案、天气（高德）、表情包、个人 / 全局记忆、戳一戳、网页搜索（DeepSeek Search API）
  - 被戳时触发对话；发给模型的消息前拼上博士的名字和 QQ 号
  - `resources/`：生日、干员数据和表情包图片，来自 rmts 的 `rmts/resources`

## 服务器上的位置

- 插件目录：`~/qqbot/astrbot-data/plugins/rmts_tools/`
- 插件配置（含高德 API Key，不在本仓库）：容器内 `/AstrBot/data/config/rmts_tools_config.json`
- 记忆数据：`~/qqbot/astrbot-data/plugin_data/rmts_tools/`

## 部署

```bash
tar -cf - -C plugins rmts_tools | ssh ubuntu@192.168.12.136 'sudo tar -xf - -C ~/qqbot/astrbot-data/plugins && sudo docker restart astrbot'
```
