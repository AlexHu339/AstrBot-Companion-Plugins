"""Observe actual OneBot receipts without replacing host segmentation or delays."""

from __future__ import annotations

import contextvars
import functools
import re

from astrbot.api import logger

SEND_CONTEXT = contextvars.ContextVar("qq_features_send", default=None)
TAG = re.compile(r"\[(?:poke|recall|reply:\s*([0-9]+))\]", re.I)


def parse_controls(text):
    clean, pokes, quote, recall, end = [], [], "", False, 0
    for match in TAG.finditer(text or ""):
        clean.append(text[end : match.start()])
        name = match.group(0).lower()
        if name == "[poke]":
            pokes.append(len("".join(clean)))
        elif name == "[recall]":
            recall = True
        else:
            quote = match.group(1) or ""
        end = match.end()
    clean.append((text or "")[end:])
    return "".join(clean), {"pokes": pokes[:1], "quote": quote, "recall": recall}


class ReceiptObserver:
    def __init__(self, plugin):
        self.plugin = plugin
        self.patches = []

    def attach(self, bot):
        api = getattr(bot, "api", None)
        if api is None or any(item[0] is api for item in self.patches):
            return
        original = api.call_action

        @functools.wraps(original)
        async def observed(action, **params):
            result = await original(action=action, **params)
            bound = SEND_CONTEXT.get()
            if bound and action in ("send_private_msg", "send_msg") and not params.get("group_id"):
                event, scope, persona = bound
                target = str(params.get("user_id", ""))
                if target == str(event.get_sender_id()) and scope == self.plugin.owner_scope():
                    mid = str((result or {}).get("message_id", "")) if isinstance(result, dict) else ""
                    if mid.isdigit():
                        text = "".join(
                            str(p.get("data", {}).get("text", ""))
                            for p in params.get("message", [])
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                        ids = event.get_extra("qqf_sent_ids", [])
                        ids.append(mid)
                        event.set_extra("qqf_sent_ids", ids)
                        try:
                            self.plugin.store.message(scope, mid, "assistant", text)
                        except Exception:
                            logger.warning("[qq功能拓展] 已送达，但消息回执存储失败；不重发消息")
            return result

        api.call_action = observed
        self.patches.append((api, original, observed))

    def detach(self):
        for api, original, observed in self.patches:
            if api.call_action is observed:
                api.call_action = original
        self.patches.clear()

    async def wrap_sender(self, event):
        if event.get_extra("qqf_sender_wrapped"):
            return
        bot = self.plugin.bot_for(event)
        if bot is None:
            return
        self.attach(bot)
        original = event.send
        scope, persona = await self.plugin.bridge.identity(event)
        event.set_extra("qqf_sender_wrapped", True)

        @functools.wraps(original)
        async def send(chain):
            from astrbot.api.message_components import Plain, Reply

            if not self.plugin.allowed(event):
                return await original(chain)
            controls = event.get_extra("qqf_controls", {})
            quote = controls.get("quote") or event.get_extra("qqf_quote", "")
            if quote and self.plugin.feature("quote") and not event.get_extra("qqf_quote_sent"):
                rows = self.plugin.store.messages(
                    scope, "user", self.plugin.settings["limits"]["quote_history_minutes"]
                )
                if any(r["id"] == quote for r in rows) and not any(isinstance(c, Reply) for c in chain.chain):
                    chain = chain.derive([Reply(id=quote), *chain.chain])
                    event.set_extra("qqf_quote_sent", True)
            token = SEND_CONTEXT.set((event, scope, persona))
            try:
                result = await original(chain)
            finally:
                SEND_CONTEXT.reset(token)
            if result is False:
                return result
            plain = "".join(c.text for c in chain.chain if isinstance(c, Plain))
            progress = event.get_extra("qqf_sent_chars", 0) + len(plain)
            event.set_extra("qqf_sent_chars", progress)
            if event.get_extra("qqf_quote_sent") and not event.get_extra("qqf_quote_logged"):
                event.set_extra("qqf_quote_logged", True)
                self.plugin.receipt(event, "quote", "success", "已引用本私聊中的真实消息")
            for position in controls.get("pokes", []):
                if progress >= position and not event.get_extra("qqf_poke_done"):
                    event.set_extra("qqf_poke_done", True)
                    try:
                        await self.plugin.poke(event)
                    except Exception as exc:
                        self.plugin.action_error(event, "poke", exc)
            return result

        event.send = send
