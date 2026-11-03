"""Small contextual conversation guidance, sharing the current model turn."""

from __future__ import annotations

import random
import re


class ConversationDirector:
    def __init__(self, plugin):
        self.plugin = plugin

    def note_user(self, event):
        scope = event.unified_msg_origin
        db = self.plugin.social.db
        db.set("conversation-turn:" + scope, db.get("conversation-turn:" + scope, 0) + 1)

    def guidance(self, event, persona):
        conf = self.plugin.settings["conversation"]
        if not conf["enabled"] or self.plugin.is_proactive(event):
            return ""
        if event.get_extra("qqf_continue_considered"):
            return ""
        event.set_extra("qqf_continue_considered", True)
        text = event.get_message_str() or ""
        if re.search(r"难过|糟心|崩溃|去世|生病|不舒服|伤心|认真|别闹|不想聊|晚安|睡了|拜拜|先忙|不回复", text):
            return ""
        db = self.plugin.social.db
        scope = event.unified_msg_origin
        turn = db.get("conversation-turn:" + scope, 1)
        last = db.get("conversation-used:" + scope + ":" + persona, -100)
        if turn - last < conf["turn_gap"] or random.random() >= conf["probability"]:
            return ""
        event.set_extra("qqf_continue_offer", (persona, turn))
        return (
            "【接话留钩子：沿用当前人格】这轮如适合轻松互动，不要仅用‘嗯/好的/这句爱听’终结话题。"
            "可以顺着当前梗轻微夸张、俏皮改口、提出一个小主张、分享一个具体感受，留给对方自然吐槽或接续的点。"
            "不必每轮追问，不重复固定台词，不硬凑反转，不为了拖住对方而纠缠；对方忙、累或认真时体贴收尾。"
            "称呼、关系和调皮程度以当前人格与上下文为准，不能另造性格。"
        )

    def after_sent(self, event):
        offer = event.get_extra("qqf_continue_offer")
        if offer and getattr(event, "_has_send_oper", False):
            self.plugin.social.db.set("conversation-used:" + event.unified_msg_origin + ":" + offer[0], offer[1])
