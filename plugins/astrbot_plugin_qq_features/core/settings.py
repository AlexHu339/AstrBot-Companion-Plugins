"""One catalogue drives the config form and the explanatory feature cards."""

from __future__ import annotations

FEATURES = {
    "poke_react": (
        "被戳后按人格回应",
        "你戳机器人后，它会结合当前人格、近期对话、记忆和心情，简短回应。不是每戳一下都机械回复；受回应概率和冷却时间限制。命中回应会产生一次正常聊天模型费用。",
    ),
    "poke_back": (
        "被戳后可以回戳",
        "机器人被你戳后，可以按语境回戳一次，也可以只说话。关闭后，被戳事件仍能被理解，但不会因该事件回戳。戳动作本身不消耗模型 token。",
    ),
    "poke_active": (
        "聊天和主动消息中主动戳你",
        "在普通聊天或现有主动消息中，机器人可以偶尔戳你一下。不会另外启动定时聊天任务。与回戳共用冷却，默认每轮最多一次；关闭后仍可单独允许被戳回戳。",
    ),
    "quote": (
        "引用你前面的话",
        "机器人可以引用当前私聊在“引用历史时长”内收到的真实 QQ 消息，默认最近10分钟。引用时长单独设置，不会扩大撤回范围；不会引用别人的私聊。沿用 AstrBot 分段发送，引用附在本轮第一条实际消息上。",
    ),
    "recall": (
        "撤回自己刚发的消息",
        "机器人可以把本轮回复标记为几秒后尝试撤回，也可以撤回本私聊仍在“允许撤回时限”内的真实机器人消息。默认只允许发送后120秒内的消息，自动撤回另外默认等5秒；引用时长不会扩大撤回范围。不会撤回用户消息，不猜消息编号；接口成功后才记录撤回成功。",
    ),
    "nickname": (
        "按你的要求改 QQ 昵称",
        "你在主人私聊中明确要求修改时，机器人可修改整个 QQ 账号的昵称。不会随切换人格自动修改；账号资料对所有好友共同可见。接口成功后才报告成功。",
    ),
    "avatar": (
        "按你的要求改 QQ 头像",
        "你明确要求换头像并发送或引用一张图片后，机器人可把它设为 QQ 头像。也可在本页选择本地图片。只使用本次图片，不允许模型任意读取电脑文件或指定外部下载地址。",
    ),
    "signature": (
        "按你的要求改个性签名",
        "你说“把签名改成……”时，机器人修改 QQ 个性签名。主动心情签名另有独立开关和次数限制。两个入口共用同一个修改器，避免互相抢写。",
    ),
    "status": (
        "按你的要求改在线状态",
        "你明确要求时，可切换在线、离开、隐身、忙碌、请勿打扰等 QQ 状态。只改变 QQ 展示状态，不停止 AstrBot，也不更改主动消息的免打扰设置。",
    ),
    "mood_signature": (
        "心情变化时偶尔换签名",
        "复用 LivingMemory 已判断的开心、甜蜜、难过等心情，或你本轮明确提到的生日、夸奖等线索，允许机器人在合适时换一句短签名。每次实际聊天才考虑，不在空闲时循环调用模型；默认每天最多两次，间隔至少六小时。不会把猜想写成用户事实。",
    ),
    "qzone_read": (
        "查看空间动态",
        "允许读取主人、机器人自己的动态；打开“浏览好友和空友爱看”后也能查看好友动态流。它能读正文、评论和图片线索，用于产生新话题。仅读取账号本来有权访问的内容；包含专用接口读取的“空友爱看”推荐流；访问失败会显示原因，并继续使用好友来源。",
    ),
    "qzone_draft": (
        "写说说和评论草稿",
        "你可以直接提供文字，也可以让机器人结合当前话题、已有记忆和人格写草稿。草稿进入本地待审核列表，可查看、修改、驳回。草稿不会自动出现在 QQ 空间，也不表示已经发布。",
    ),
    "qzone_publish": (
        "发布说说",
        "这是“能不能发”的开关。开启后，按下方“空间行动方式”执行：自主模式可在聊天中或随机时机自行发说说，默认每滚动5天最多2次；先审核模式存为草稿；仅听指令模式等你明确要求。文字可自然搭配emoji、颜文字与玩梗，不固定套话。",
    ),
    "qzone_comment": (
        "发表评论",
        "这是“能不能评论”的开关。可以针对实际读到的主人动态发表评论；若还打开“允许评论好友和空友爱看”，也能评论好友。执行方式统一由下方模式决定。自主评论默认每滚动5天最多2次，且不重复评论同一动态；只看不评论可单独关闭此项。",
    ),
    "qzone_browse_friends": (
        "浏览好友和空友爱看",
        "扩大读取范围到当前QQ账号能看到的好友动态与空友爱看推荐流，带回真实作者、时间、原文和可用来源。可与主人聊风景、趣事、恋爱照片等见闻；不会把别人的经历当成主人的经历。关闭后，只读主人和机器人自己的动态。",
    ),
    "qzone_comment_friends": (
        "允许评论好友和空友爱看",
        "在“发表评论”开启的基础上，允许把评论发到好友动态和空友爱看推荐内容下面。可以只开浏览、关掉本项，让小鱼只看不评论。所有自主评论共用每5天2次的额度；开关不会放开陌生人私聊或允许别人修改账号资料。",
    ),
    "social_browse": (
        "每天随机逛空间",
        "在每天随机选定的时刻浏览动态，默认一天最多两次，遵守现有免打扰时段。每次只处理少量新动态，读过的会去重；没有值得聊的内容可以不发言。不每隔几秒调用模型，定时检查本身不消耗token。",
    ),
    "social_delayed": (
        "聊天结束或没回消息时偶尔发动态",
        "在主动消息成功送达但你尚未回复，或一段聊天自然结束后，安排一次随机延迟的行动候选。到点会重新检查你是否已回复、是否免打扰、额度是否足够，再决定发不发；不机械催你，也不把玩笑里的猜测记成事实。",
    ),
    "social_share": (
        "把见闻带回聊天",
        "小鱼可以把读到的有趣动态作为可选话题、分享原链接，或在适合时于自己的说说中提到你。私聊分享共用现有主动消息管线、上下文和表情包管理器，不另开一套连续催聊器；分享过或过期的话题会退出队列。",
    ),
    "social_memory": (
        "保存有来源的空间见闻",
        "原始见闻记录保留作者、发布时间、读取时间和链接。每次浏览最多挑一条值得留下的内容进入LivingMemory，标记为外部观察；不会进入你的固定事实，也不冒充你承诺过的事情。临时可聊话头另有有效期，不会无限积累。",
    ),
}

DEFAULTS = {
    "general": {"enabled": True, "owner_scope": "", "data_root": "", "workspace_version": 2},
    "features": {key: True for key in FEATURES},
    "limits": {
        "poke_cooldown_seconds": 30,
        "poke_response_probability": 0.7,
        "poke_response_cooldown_seconds": 45,
        "recall_delay_seconds": 5,
        "quote_history_minutes": 10,
        "recall_window_seconds": 120,
        "signature_daily_limit": 2,
        "signature_cooldown_hours": 6,
        "context_chars": 900,
    },
    "social": {
        "mode": "autonomous",
        "reads_per_day": 2,
        "posts_per_window": 2,
        "comments_per_window": 2,
        "window_days": 5,
        "read_batch_size": 3,
        "decisions_per_day": 6,
        "unanswered_min_minutes": 45,
        "unanswered_max_minutes": 240,
        "closure_min_minutes": 60,
        "closure_max_minutes": 360,
        "delayed_probability": 0.5,
        "share_probability": 0.65,
        "vision_enabled": True,
        "vision_provider_id": "",
        "vision_images_per_read": 1,
        "observation_retention_days": 30,
        "topic_ttl_hours": 48,
        "style_guidance": "沿用当前人格、关系称呼和口癖，允许傲娇、嘴硬心软、可爱吐槽和适量玩梗。结合当下语境，避免复读固定例句；可以用emoji或颜文字。认真、难过时温柔些；不把没回消息都理解为不爱或出轨，玩笑不记成事实。",
    },
    "profile_auto": {
        "enabled": True,
        "nickname": True,
        "avatar": True,
        "signature": True,
        "status": True,
        "probability": 0.35,
        "daily_limit": 2,
        "cooldown_hours": 6,
        "max_changes_per_opportunity": 2,
        "randomize_change_count": True,
    },
    "space": {"owner": True, "friends": True, "public": True, "shared_comment": True},
    "conversation": {"enabled": True, "probability": 0.6, "turn_gap": 2},
    "meme_reaction": {"enabled": True, "probability": 0.45, "cooldown_minutes": 10, "daily_limit": 5},
}


def migrate_legacy_limits(config):
    """Migrate this plugin's old shared window once; keep unrelated values."""
    limits = dict(config.get("limits", {}) or {})
    if "recent_message_minutes" not in limits:
        return False
    # AstrBot may have already added new schema defaults. The legacy value is
    # the user's previously saved quote preference until this key is removed.
    limits["quote_history_minutes"] = limits.pop("recent_message_minutes")
    limits["recall_window_seconds"] = max(
        int(limits.get("recall_window_seconds", 120)), int(limits.get("recall_delay_seconds", 5)) + 1
    )
    config["limits"] = limits
    return True


def migrate_workspace(config):
    general = dict(config.get("general", {}) or {})
    if general.get("workspace_version") == 2:
        return False
    auto = dict(config.get("profile_auto", {}) or {})
    limits = config.get("limits", {}) or {}
    auto.update(
        daily_limit=limits.get("signature_daily_limit", 2), cooldown_hours=limits.get("signature_cooldown_hours", 6)
    )
    auto["signature"] = (config.get("features", {}) or {}).get("mood_signature", True)
    config["profile_auto"] = auto
    features = dict(config.get("features", {}) or {})
    # These are internal lifecycle steps, not separate user permissions.
    for key in ("qzone_draft", "social_memory", "social_browse", "qzone_browse_friends", "qzone_comment_friends"):
        features[key] = True
    config["features"] = features
    social = dict(config.get("social", {}) or {})
    social["mode"] = "autonomous"
    config["social"] = social
    general["workspace_version"] = 2
    config["general"] = general
    return True


def merge_settings(config):
    import copy

    result = copy.deepcopy(DEFAULTS)
    for group, entries in result.items():
        supplied = config.get(group, {}) or {}
        for key in entries:
            if key in supplied:
                result[group][key] = supplied[key]
        if group == "limits" and "recent_message_minutes" in supplied and "quote_history_minutes" not in supplied:
            result[group]["quote_history_minutes"] = supplied["recent_message_minutes"]
    return result


def validate_patch(payload):
    if not isinstance(payload, dict) or set(payload) - {
        "features",
        "limits",
        "general",
        "social",
        "profile_auto",
        "space",
        "conversation",
        "meme_reaction",
    }:
        raise ValueError("未知的设置项")
    result = {}
    ranges = {
        "poke_cooldown_seconds": (5, 3600),
        "poke_response_probability": (0, 1),
        "poke_response_cooldown_seconds": (10, 3600),
        "recall_delay_seconds": (1, 120),
        "quote_history_minutes": (1, 30),
        "recall_window_seconds": (5, 300),
        "signature_daily_limit": (0, 10),
        "signature_cooldown_hours": (0.25, 24),
        "context_chars": (200, 2000),
    }
    social_ranges = {
        "reads_per_day": (0, 12),
        "posts_per_window": (0, 20),
        "comments_per_window": (0, 30),
        "window_days": (1, 30),
        "read_batch_size": (1, 5),
        "decisions_per_day": (0, 24),
        "unanswered_min_minutes": (5, 1440),
        "unanswered_max_minutes": (5, 2880),
        "closure_min_minutes": (5, 1440),
        "closure_max_minutes": (5, 2880),
        "delayed_probability": (0, 1),
        "share_probability": (0, 1),
        "vision_images_per_read": (0, 20),
        "observation_retention_days": (7, 90),
        "topic_ttl_hours": (1, 168),
    }
    import math

    grouped_ranges = {
        "profile_auto": {"probability": (0, 1), "daily_limit": (0, 20), "cooldown_hours": (0, 72), "max_changes_per_opportunity": (1, 4)},
        "conversation": {"probability": (0, 1), "turn_gap": (1, 20)},
        "meme_reaction": {"probability": (0, 1), "cooldown_minutes": (0, 1440), "daily_limit": (0, 30)},
    }

    for group, values in payload.items():
        if not isinstance(values, dict):
            raise ValueError("设置格式不正确")
        result[group] = {}
        for key, value in values.items():
            # A still-open pre-upgrade page can change the quote window only.
            if group == "limits" and key == "recent_message_minutes":
                if "quote_history_minutes" in values:
                    raise ValueError("请刷新页面后分别设置引用与撤回时限")
                key = "quote_history_minutes"
            if group in ("profile_auto", "space", "conversation", "meme_reaction"):
                if key not in DEFAULTS[group]:
                    raise ValueError("未知的功能参数")
                if key in grouped_ranges.get(group, {}):
                    low, high = grouped_ranges[group][key]
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or not low <= value <= high
                    ):
                        raise ValueError(f"{key} 必须介于 {low} 和 {high}")
                    if key in ("daily_limit", "turn_gap", "max_changes_per_opportunity") and int(value) != value:
                        raise ValueError("次数与轮数必须为整数")
                elif type(value) is not bool:
                    raise ValueError("功能开关必须是开或关")
                result[group][key] = value
                continue
            if group == "features" and key in FEATURES or group == "general" and key == "enabled":
                if type(value) is not bool:
                    raise ValueError("功能开关必须是开或关")
            elif group == "social" and key in ("mode", "style_guidance", "vision_provider_id", "vision_enabled"):
                if key == "mode" and value not in ("autonomous", "manual", "review"):
                    raise ValueError("请选择自主、仅听指令或先审核模式")
                if key in ("style_guidance", "vision_provider_id") and (
                    not isinstance(value, str) or len(value) > 1000
                ):
                    raise ValueError("偏好和模型名称需要有效文字，最多1000字")
                if key == "vision_enabled" and type(value) is not bool:
                    raise ValueError("看图开关必须是开或关")
            elif group == "limits" and key in ranges or group == "social" and key in social_ranges:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("频率参数必须是有效数字")
                low, high = (ranges if group == "limits" else social_ranges)[key]
                if not low <= value <= high:
                    raise ValueError(f"{key} 必须介于 {low} 和 {high}")
                if (
                    key
                    in {
                        "quote_history_minutes",
                        "recall_window_seconds",
                        "recall_delay_seconds",
                        "signature_daily_limit",
                        "context_chars",
                    }
                    and int(value) != value
                ):
                    raise ValueError("次数和字数必须为整数")
                if (
                    group == "social"
                    and key not in ("delayed_probability", "share_probability")
                    and int(value) != value
                ):
                    raise ValueError("次数与分钟需要整数")
            else:
                raise ValueError("此入口不能更改身份、存储路径或未知设置")
            result[group][key] = value
    return result


def explicit_public_request(text, kind):
    """A model cannot turn a quotation, negation or a draft request into publication."""
    import re

    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|\'[^\'\n]*\'', "", text or "")
    if re.search(r"(?:不要|别|不许|先不|暂不|禁止).{0,12}(?:发布|发送|发|评论)", text):
        return False
    if kind == "comment":
        return bool(re.search(r"(?:发送|发表|发布).{0,10}评论|评论(?:这条|一下|刚才|这段)", text))
    return bool(
        re.search(
            r"(?:发布|发送).{0,12}(?:说说|动态|空间)|发(?:条|个|一条|一个|这条|这段).{0,12}(?:说说|动态|到空间|到QQ空间)|(?:发|写)到(?:QQ)?空间",
            text,
        )
    )
