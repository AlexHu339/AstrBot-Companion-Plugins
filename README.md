# AstrBot Companion Plugins · 小鱼插件分享包

用于朋友学习、借鉴和分别安装的 **6 个非内置 AstrBot 插件源码快照**。基于 AstrBot **4.29.3** 与 NapCat 的本机适配版本，快照日期 **2026-11-03**。

这些是原项目与本地定制代码的合集，各项目作者和许可证分别保留，**不是全部从零原创，也不是 AstrBot 官方整合包**。

## 下载

到 [Releases](https://github.com/AlexHu339/AstrBot-Companion-Plugins/releases/latest) 下载：

- `AstrBot-Companion-Plugins-bundle.zip`：全部源码、说明和默认配置模板。
- 与插件目录同名的 6 个 ZIP：用于分别安装插件。
- `SHA256SUMS.txt`：下载文件的完整性校验值。

**不要把这个合集仓库 URL 直接当作一个插件安装。** 合集根目录没有统一的 `main.py`，6 个插件仍然各自运行。

## 包含哪些插件

| 插件 | 本地快照版本 | 主要功能 | 授权 |
| --- | --- | --- | --- |
| [LivingMemory 小鱼陪伴版](plugins/astrbot_plugin_livingmemory) | 2.8.2+companion.14 | 长期记忆、检索与整理、置顶保护、八种情绪、费用与储存看板、聊天节奏与新消息优先接话 | AGPL-3.0 |
| [qq功能拓展](plugins/astrbot_plugin_qq_features) | 2.3.2 | 戳一戳、引用与撤回、资料变化、空间浏览/发表/评论/见闻分享、收藏表情后的互动 | AGPL-3.0；保留 Qzone 第三方许可 |
| [主动消息](plugins/astrbot_plugin_proactive_chat) | v1.2.6+companion.1 | 既有主动聊天调度，结合记忆和情绪，保留免打扰与未回复控制 | AGPL-3.0 |
| [表情包管理器](plugins/meme_manager) | 5.2.9+companion.2 | 自动收集、描述和向量检索；辅助模型在后台选图，文字优先发送 | MIT |
| [克隆语音](plugins/astrbot_plugin_clonetts) | v3.0.1+companion.1 | 火山声音复刻语音；本地定制包含 PCM 封装 WAV 与用量适配 | AGPL-3.0 |
| [小鱼唱歌](plugins/astrbot_plugin_dafeiyu_sing) | v0.2.1+companion.4 | 豆包 Seed Audio 云端短歌、更多歌名与主题、与现有人格和话题配合；可选本地 SoulX 接口 | MIT |

原作者及来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。完整目录、版本与公开副本改动见 [PLUGIN_MANIFEST.json](PLUGIN_MANIFEST.json)。

## 怎么安装和配置

1. 先有正常运行的 AstrBot 与 QQ/NapCat 连接，并确认普通聊天可回复。
2. 从 Release 下载需要的**单插件 ZIP**，在 AstrBot 的插件安装入口选择本地 ZIP 安装；如果你的版本未提供本地 ZIP 入口，解压后把整个插件目录放到自己的 `data/plugins/` 下，再加载插件。
3. 按各插件的 `requirements.txt` 安装依赖；在 AstrBot 实际使用的 Python 环境操作，不能只装到另一个系统 Python。
4. 在插件配置里填**你自己的**模型 Provider ID、主人身份和云端凭据。参考 [安装与适配说明](docs/INSTALL.md)。
5. 先确认普通聊天、记忆、表情工作，再启用主动聊天、语音、唱歌和空间功能。

`examples/` 中是从插件配置 schema 生成的**默认模板**，不是仓库维护者的实际配置。不要用模板覆盖已有重要配置；没有 API Key、QQ 账号、音色或模型授权随包附送。

## 这套定制的几个关键点

- 私聊/人格与群聊记忆分开；原文、长期事实、连续状态、目的和外部见闻分别处理。
- 重要与置顶记忆受到保护，低价值旧记忆优先归档；合并整理保留来源与原文。
- 文字不等辅助模型选表情。新实质消息到来后停止旧轮未发送的碎句，新回答就绪后接上；已发消息保留，未发草稿不当作已经说过的事实。
- 默认模型配置需自行选择，定制适配保留人格和主动提示词；仓库不提供维护者私人设定。
- 主动消息继续由 Proactive Chat 调度，QQ 拓展不会另起一个不受控制的私聊发送器。
- 费用看板按本机观察到的调用和配置价格计算，不能替代平台账单。充值、赠金、资源包、账户余额与试用到期信息需要按各平台实际能力查询或校准。

QQ 资料修改、公开说说、评论等会对真实账号生效；请在自己的账号上检查主人范围、功能开关、频率和免打扰设置。语音与唱歌使用自己的云端服务，可能消耗额度或产生费用。

## 哪些东西没有打包

AstrBot 自带的内置插件、AstrBot/NapCat/Ollama 程序及模型、实际 API Key/Cookie/Token、账号登录状态、私人聊天与记忆数据库、个人配置和人格、账本与备份、收到的表情包、参考录音、生成音频、缓存和运行日志。

源码中的截图/图标/字体为原项目随源码附带的静态资源，不包含维护者本机收到的图片。需要表情资源时请在表情包管理器中自行安装。

## 目录

```text
plugins/          六个独立插件的源码与许可证
examples/         不含账号凭据的默认配置模板
docs/             安装、交互关系和本地定制说明
scripts/          源码检查与重新打包工具
PLUGIN_MANIFEST.json
SOURCE_MANIFEST.json
THIRD_PARTY_NOTICES.md
```

详细改动与兼容边界见 [LOCAL_CHANGES.md](docs/LOCAL_CHANGES.md)。重新分发或修改时，请遵守每个插件及其第三方代码的许可证。
