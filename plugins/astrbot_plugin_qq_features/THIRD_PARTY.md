# Sources and licensing

The combined plugin is licensed under AGPL-3.0. Its complete corresponding source is included in the installation ZIP.

QQ Space transport, parsing and data models under `core/vendor` are adapted from Zhalslar/astrbot_plugin_qzone (GPL-3.0). Original license is retained in third_party/QZONE_LICENSE. Changes: a narrow configuration protocol replaces the original plugin config; HTTP timeout is bounded explicitly; login logging omits account identifiers. The local plugin adds a separately implemented, user-authorized autonomous scheduler and source-linked memory bridge, with a unified autonomous/manual/review policy. No model tool can approve an old reviewed draft.

Retrieved source files:
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/model.py (Git blob bcbfc231251ce919b5d4abd725cec0fa1afed7a7)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/api.py (Git blob 656c1236ed98ea71d1df7c87e55bc6e082d529eb)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/client.py (Git blob 536d04e086c05380675ec739a0f98f15eab79403)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/session.py (Git blob 30c72c492e19533cbb23200d28e43bbebfb1d461)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/parser.py (Git blob 05b3d12ba8d319cd5ea61f18330aab61a0916bab)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/model.py (Git blob e957cedc697a96406509f55a14c3f0636c79cc32)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/constants.py (Git blob b885eb03b4c4aa328ee2461238d0c31113272b8a)
- https://github.com/Zhalslar/astrbot_plugin_qzone/blob/main/core/qzone/utils.py (Git blob a3a90ad7edcaabf140fc5fb82a25b6e4e0324204)

Interaction designs and OneBot API usage were reviewed against kazamisama/astrbot_plugin_litepoke, L1ke40oz/astrbot_plugin_active_function, Zhalslar/astrbot_plugin_qqprofile and the local dafeiyu signature plugin. Their separate plugin runtimes were not bundled. A single scoped implementation replaces overlapping collection/segmentation/authorization behavior.

Autonomous-social design references checked 2026-10-30: https://github.com/Zhalslar/astrbot_plugin_qzone (random Cron offsets, AI publishing/commenting); https://github.com/Gu-Heping/onebot-qzone and its doc/api-probe-results.md (friend-feed transport and endpoint limitations); https://github.com/menglimi/astrbot_plugin_private_companion (candidate lifecycle, timing checks and source-aware continuity); https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat (existing private-message pipeline). These companion/bridge runtimes are not installed or copied as a second framework.

Public-recommendation protocol references checked 2026-10-30: https://github.com/SnowLuma/SnowLuma/blob/main/packages/sdk/README.md (public-space SDK documentation); https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/data/qzone-public.ts and packages/napcat-core/apis/qzone-public.ts (published protocol/action definitions). core/public_qzone.py is an independently written Python compatibility adapter. No NapCat source, binaries or runtime are copied into this distribution. Public-feed identities and transfer context are not substituted with legacy Qzone tid values; opaque context and credentials remain RAM-only.

V2 profile-state protocol reference: https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/extends/SetOnlineStatus.ts (Git blob ae143508abd522c86c816b443c14a504f70c626b). Only published status/extended-status numeric protocol facts are independently mapped; no NapCat implementation is bundled. Meme pack paths/categories are read from the installed meme_manager runtime resolver. Animated images are decoded in memory with the existing Pillow dependency; original packs are not modified.
