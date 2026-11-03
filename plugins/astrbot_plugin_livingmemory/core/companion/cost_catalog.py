"""List configured paid paths before their first observed request."""

CATALOG = [
    (
        "chat",
        "普通聊天",
        "含接话、工具规划、写歌词、聊天内的资料修改与空间操作；复用当轮回答时不另算一笔。",
        "llm",
    ),
    (
        "proactive_chat",
        "主动聊天",
        "主动消息插件生成的聊天；分享空间见闻可复用此轮。",
        "llm",
    ),
    ("memory_extract", "记忆提取", "从真实对话提炼长期记忆。", "llm"),
    ("memory_consolidation", "记忆整理", "合并、精炼旧记忆时的模型调用。", "llm"),
    (
        "emotion_analysis",
        "情绪判断",
        "判断八种短时情绪；本地随时间回稳不调用模型。",
        "llm",
    ),
    (
        "context_compression",
        "上下文压缩",
        "由模型压缩近期聊天；本地裁剪不收费。",
        "llm",
    ),
    ("空间看图", "空间看图", "浏览动态或阅读分享时的图片识别。", "llm"),
    (
        "空间自主决策",
        "空间自主决策",
        "综合人格、见闻、情绪决定发布、评论或留下话题。",
        "llm",
    ),
    (
        "资料自主决策",
        "资料自主决策",
        "独立判断昵称、头像、签名或状态变化；若复用聊天则计入普通聊天。",
        "llm",
    ),
    (
        "meme_analysis",
        "表情识别与选图",
        "表情包管理器的识别、分类、去重判断、语义选图；每次实际模型请求只计一次。",
        "llm",
    ),
    (
        "表情收藏互动",
        "表情收藏互动",
        "成功收藏后的额外短回复；复用聊天时不增加调用。",
        "llm",
    ),
    (
        "voice_style",
        "语音语气分析",
        "克隆语音前用聊天模型分析语气，与实际声音合成分开计。",
        "llm",
    ),
    (
        "voice_synthesis",
        "声音复刻 · 合成语音",
        "按字符抵扣试用或资源包；另显示实际生成音频时长。",
        "speech",
    ),
    (
        "singing_audio",
        "唱歌 · 音频生成",
        "按云端原始音频时长计量；每次生成和重试分别计，不含本地补的静音。",
        "speech",
    ),
    (
        "preset_voice",
        "预设音色 · 语音合成",
        "以后改用预设音色时自动记录；不冒用复刻的试用余额。",
        "speech",
    ),
    (
        "audio_diagnostic",
        "语音／唱歌诊断",
        "仅有证据的历史诊断调用；不是正常聊天生成。",
        "speech",
    ),
    (
        "voice_slot",
        "音色槽位购买／续费",
        "平台订单费用，需按实际账单手工登记；不因一次语音而重复记槽位费。",
        "manual",
    ),
    (
        "speech_resource_purchase",
        "语音／唱歌资源包购买",
        "登记实际购买支出；包内消耗仅记用量，避免把购买费再算一次。",
        "manual",
    ),
    (
        "speech_other_fee",
        "语音其他平台费用",
        "平台额外收费、调整等按账单登记；充值属于账户转入，不是消费。",
        "manual",
    ),
    (
        "unattributed",
        "未归类模型调用",
        "新功能经已观测的 AstrBot 模型接口调用会先进入这里，不静默遗漏。",
        "llm",
    ),
    (
        "historical_unclassified",
        "安装前记录（未分类）",
        "旧记录保留原有用量，不凭猜测分摊。",
        "llm",
    ),
]


def complete_rows(rows):
    """Keep observed models, adding one empty row for each unused category."""
    result = [dict(r) for r in rows]
    seen = {r["category"] for r in rows}
    descriptions = {key: (name, note, kind) for key, name, note, kind in CATALOG}
    for key, name, note, kind in CATALOG:
        if key not in seen:
            result.append(
                {
                    "category": key,
                    "model": "—",
                    "calls": 0,
                    "input_other": 0,
                    "input_cached": 0,
                    "output": 0,
                    "amount": 0,
                    "currency": "CNY",
                    "unknown": 0,
                    "kind": kind,
                }
            )
    for row in result:
        name, note, kind = descriptions.get(
            row["category"], (row["category"], "已观测到的其他模型用途。", "llm")
        )
        row.update(label=name, note=note, kind=kind)
    order = {r[0]: i for i, r in enumerate(CATALOG)}
    return sorted(
        result, key=lambda r: (order.get(r["category"], len(order)), r.get("model", ""))
    )
