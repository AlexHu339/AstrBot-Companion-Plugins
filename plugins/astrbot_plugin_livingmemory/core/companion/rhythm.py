"""Bounded private-chat batching and source-backed conversation closure."""

from __future__ import annotations

import asyncio
import math
import re
import time
import uuid

from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import Plain

from ..memory_scope import is_event_memory_allowed
from ..utils import get_persona_id
from .policy import token_estimate
from .delivery import TextDelivery
from .delivery_history import reconcile_native_history

DEFAULTS = {
    "enabled": True,
    "merge_enabled": True,
    "quiet_seconds": 3.0,
    "max_wait_seconds": 10.0,
    "closure_enabled": True,
    "closure_cooldown_minutes": 20.0,
    "proactive_pause_enabled": True,
    "format_hint_enabled": True,
}
ACK = re.compile(
    r"^(?:嗯+|哦+|好[的呀哒吧]?|行[的呀]?|收到[啦了]?|知道[啦了]?|明白[啦了]?|谢谢[你啦呀]?|哈哈+|嘿嘿+|OK|ok|[👌👍]+)[！!。，,~～\s]*$"
)
FAREWELL = re.compile(
    r"^(?:晚安[啦呀哦]?|拜拜[啦]?|再见|不聊了|先忙了|我先去忙了|我去忙了|我先睡了|我去睡了|睡了|明天聊|下了)[！!。，,~～\s]*$"
)


def validate_settings(payload: dict, current: dict | None = None) -> dict:
    """Validate a complete settings candidate without changing stored values.

    Args:
        payload: A partial update containing known settings only.
        current: Existing effective settings, when applying a partial update.

    Returns:
        A normalized complete configuration.

    Raises:
        ValueError: A setting has an unknown name, invalid type or invalid range.
    """
    if not isinstance(payload, dict) or set(payload) - set(DEFAULTS):
        raise ValueError("包含未知的聊天节奏设置")
    result = {**DEFAULTS, **(current or {}), **payload}
    for key in DEFAULTS:
        if isinstance(DEFAULTS[key], bool):
            if not isinstance(result[key], bool):
                raise ValueError("聊天节奏开关必须是布尔值")
        else:
            value = result[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("等待时间必须是数字")
            result[key] = float(value)
            if not math.isfinite(result[key]):
                raise ValueError("等待时间必须是有限数字")
    if not 0.5 <= result["quiet_seconds"] <= 10:
        raise ValueError("静默等待须为 0.5 至 10 秒")
    if not result["quiet_seconds"] <= result["max_wait_seconds"] <= 30:
        raise ValueError("最长等待须不小于静默等待，且不超过 30 秒")
    if not 0 <= result["closure_cooldown_minutes"] <= 120:
        raise ValueError("收尾冷却须为 0 至 120 分钟")
    return result


class ChatRhythm:
    """Coordinate existing message hooks without running another model loop."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.plugin = runtime.plugin
        self.pending = {}
        self.active = {}
        self.closed = False
        self.sequence = 0
        self._history_tasks = set()
        self.delivery = TextDelivery()
        self.delivery.attach()
        self.counters = dict(
            merged_batches=0,
            merged_messages=0,
            natural_closures=0,
            proactive_deferred=0,
        )

    @property
    def settings(self) -> dict:
        """Return validated effective settings from the existing plugin config."""
        try:
            return validate_settings(
                self.plugin.config_manager.get("chat_rhythm", {}) or {}
            )
        except ValueError:
            # Invalid manual configuration must not cause messages to disappear.
            return {**DEFAULTS, "enabled": False}

    def _finish_batch(self, key, batch):
        """Release every waiter, choosing one surviving event to generate.

        Args:
            key: Private scope and persona tuple.
            batch: The exact pending batch, protected against a newer replacement.
        """
        if self.pending.get(key) is batch:
            self.pending.pop(key)
        batch["items"].sort(key=lambda item: item["order"])
        alive = [item for item in batch["items"] if not item["future"].done()]
        winner = alive[-1] if alive else None
        sources = [
            {k: item[k] for k in ("event_id", "text", "at")} for item in batch["items"]
        ]
        for item in alive:
            item["future"].set_result(sources if item is winner else None)
        timer = batch.get("timer")
        if timer and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()

    async def _wait_batch(self, key, batch, settings):
        """Wait locally for quiet, with a fixed upper bound under continuous input."""
        try:
            while self.pending.get(key) is batch:
                now = asyncio.get_running_loop().time()
                remaining = (
                    min(
                        batch["last"] + settings["quiet_seconds"],
                        batch["start"] + settings["max_wait_seconds"],
                    )
                    - now
                )
                if remaining <= 0:
                    break
                batch["changed"].clear()
                try:
                    await asyncio.wait_for(batch["changed"].wait(), timeout=remaining)
                except TimeoutError:
                    pass
        finally:
            if self.pending.get(key) is batch:
                self._finish_batch(key, batch)

    async def before_waiting(self, event):
        """Batch allowed QQ private plain text before the host session lock.

        Args:
            event: An actual incoming platform event destined for native LLM chat.
        """
        settings = self.settings
        if (
            self.closed
            or not settings["enabled"]
            or event.get_extra("chat_rhythm_waited")
        ):
            return
        if (
            ":FriendMessage:" not in event.unified_msg_origin
            or event.get_platform_name() != "aiocqhttp"
        ):
            return
        if not is_event_memory_allowed(self.plugin.config_manager, event):
            return
        self.sequence += 1
        order = self.sequence
        event_id = str(getattr(event.message_obj, "message_id", "") or uuid.uuid4().hex)
        if event_id.startswith("proactive:"):
            return
        persona = str(await get_persona_id(self.plugin.context, event) or "default")
        key = (event.unified_msg_origin, persona)
        event.set_extra("chat_rhythm_key", key)
        event.set_extra("chat_rhythm_waited", True)
        text = event.get_message_str() or ""
        now = time.time()
        await asyncio.to_thread(
            self.runtime.store.archive, *key, event_id, "user", text, now
        )
        state_key = "rhythm:" + "|".join(key)
        state = self.runtime.store.get_setting(state_key, {})
        state["last_user_at"] = max(now, float(state.get("last_user_at", 0)))
        if not (ACK.fullmatch(text.strip()) or FAREWELL.fullmatch(text.strip())):
            state["closed_until"] = 0
        self.runtime.store.set_setting(state_key, state)
        parts = event.get_messages() or []
        eligible = bool(
            text.strip()
            and parts
            and all(isinstance(p, Plain) for p in parts)
            and not text.lstrip().startswith(("/", "！", "!"))
            and len(text) <= 6000
            and event.get_extra("provider_request") is None
        )
        if not eligible:
            if batch := self.pending.get(key):
                self._finish_batch(key, batch)
                await asyncio.sleep(0)
            # Preserve attachment/reference semantics and command/custom requests.
            state["closed_until"] = 0
            self.runtime.store.set_setting(state_key, state)
            return
        if not ACK.fullmatch(text.strip()):
            self.delivery.interrupt(event.unified_msg_origin)
        sources = [{"event_id": event_id, "text": text, "at": now}]
        if settings["merge_enabled"]:
            batch = self.pending.get(key)
            if batch is None:
                clock = asyncio.get_running_loop().time()
                batch = {
                    "items": [],
                    "start": clock,
                    "last": clock,
                    "changed": asyncio.Event(),
                }
                self.pending[key] = batch
                batch["timer"] = asyncio.create_task(
                    self._wait_batch(key, batch, settings)
                )
            if any(item["event_id"] == event_id for item in batch["items"]):
                event.stop_event()
                return
            future = asyncio.get_running_loop().create_future()
            batch["items"].append({**sources[0], "future": future, "order": order})
            batch["last"] = asyncio.get_running_loop().time()
            batch["changed"].set()
            if (
                len(batch["items"]) >= 8
                or sum(len(item["text"]) for item in batch["items"]) >= 6000
            ):
                self._finish_batch(key, batch)
            try:
                sources = await future
            except asyncio.CancelledError:
                event.stop_event()
                raise
            if sources is None:
                event.stop_event()
                return
        event.set_extra("chat_rhythm_sources", sources)
        merged = "\n".join(item["text"] for item in sources)
        state_key = "rhythm:" + "|".join(key)
        state = self.runtime.store.get_setting(state_key, {})
        input_at = max(item["at"] for item in sources)
        event.set_extra("chat_rhythm_input_at", input_at)
        settings = self.settings
        active = self.active.get(key)
        busy = bool(active and active[1] > time.time())
        recent_reply = 0 <= input_at - float(state.get("last_sent", 0)) <= 180
        can_close = bool(
            settings["enabled"]
            and settings["closure_enabled"]
            and not busy
            and recent_reply
            and not state.get("expects_reply")
            and (
                ACK.fullmatch(merged.strip())
                or (
                    state.get("closed_until", 0) > time.time()
                    and FAREWELL.fullmatch(merged.strip())
                )
            )
        )
        if can_close:
            state["closed_until"] = (
                time.time() + settings["closure_cooldown_minutes"] * 60
            )
            self.runtime.store.set_setting(state_key, state)
            self.counters["natural_closures"] += 1
            await asyncio.to_thread(
                self.runtime.store.trace,
                key[0],
                {
                    "chat_rhythm": {
                        "action": "natural_close",
                        "message_count": len(sources),
                        "extra_model_calls": 0,
                    }
                },
            )
            event.stop_event()
            return
        state["closed_until"] = 0
        self.runtime.store.set_setting(state_key, state)
        if len(sources) > 1:
            event.message_str = merged
            event.message_obj.message_str = merged
            event.message_obj.message = [Plain(merged)]
            self.counters["merged_batches"] += 1
            self.counters["merged_messages"] += len(sources)
        event.set_extra(
            "chat_rhythm_farewell", bool(FAREWELL.fullmatch(merged.strip()))
        )

    async def before_request(self, event, request):
        """Apply proactive cooldown and a temporary formatting hint.

        Args:
            event: A native or existing Proactive Chat event.
            request: The same provider request used by AstrBot.
        """
        settings = self.settings
        if (
            self.closed
            or not settings["enabled"]
            or ":FriendMessage:" not in event.unified_msg_origin
        ):
            return
        if not is_event_memory_allowed(self.plugin.config_manager, event):
            return
        persona = str(await get_persona_id(self.plugin.context, event) or "default")
        key = (event.unified_msg_origin, persona)
        event.set_extra("chat_rhythm_key", key)
        proactive = str(getattr(event.message_obj, "message_id", "")).startswith(
            "proactive:"
        )
        if event.get_platform_name() == "aiocqhttp" and not proactive:
            event.set_extra("companion_fast_text", True)
            event.set_extra("companion_delivery_cid", getattr(getattr(request, "conversation", None), "cid", None))
            progress = self.delivery.progress(event.unified_msg_origin)
            if progress is not None:
                sent = "\n".join(progress)[-240:] or "尚未发出文字"
                request.extra_user_content_parts = list(request.extra_user_content_parts or [])
                request.extra_user_content_parts.append(TextPart(text=(
                    "[DELIVERY_STATUS]上一轮仍在分条发送；角色历史中尚未发出的尾句只是草稿，"
                    "不能当作用户已听见。直接接当前用户的新话，别继续索要对方已经说过的内容。"
                    "上轮实际已发：" + sent + "[/DELIVERY_STATUS]"
                )).mark_as_temp())
        state = self.runtime.store.get_setting("rhythm:" + "|".join(key), {})
        now = time.time()
        self.active = {k: value for k, value in self.active.items() if value[1] > now}
        if (
            proactive
            and settings["proactive_pause_enabled"]
            and (
                key in self.pending
                or key in self.active
                or (settings["closure_enabled"] and state.get("closed_until", 0) > now)
            )
        ):
            self.counters["proactive_deferred"] += 1
            await asyncio.to_thread(
                self.runtime.store.trace,
                key[0],
                {
                    "chat_rhythm": {
                        "action": "proactive_deferred",
                        "extra_model_calls": 0,
                    }
                },
            )
            event.stop_event()
            return
        if not proactive:
            self.active[key] = (
                str(getattr(event.message_obj, "message_id", "")),
                now + 120,
            )
        if settings["format_hint_enabled"] or (
            settings["closure_enabled"] and event.get_extra("chat_rhythm_farewell")
        ):
            text = "[CHAT_RHYTHM]沿用原人格与当前任务。普通闲聊用完整短句，按意思自然换行；不凑条数，不拆开完整短语。明确提问和求助正常回答，不为了活泼而敷衍。"
            if settings["closure_enabled"] and event.get_extra("chat_rhythm_farewell"):
                text += "对方明确在道别，温柔简短地收尾，不另抛问题或催对方回来。"
            text += "[/CHAT_RHYTHM]"
            request.extra_user_content_parts = [
                p
                for p in (request.extra_user_content_parts or [])
                if "[CHAT_RHYTHM]" not in str(getattr(p, "text", ""))
            ]
            request.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())
            event.set_extra("chat_rhythm_hint_tokens", token_estimate(text))

    async def finalize_delivery(self, event):
        """Expose only delivered text to memory, emotion and post-send actions."""
        if not event.get_extra("companion_delivery_managed"):
            return
        if event.get_extra("companion_delivery_finalized"):
            return
        event.set_extra("companion_delivery_finalized", True)
        actual = "\n".join(event.get_extra("companion_delivered_parts", []))
        response = event.get_extra("companion_pending_reflection")
        full = getattr(response, "completion_text", "") or event.get_extra("chat_rhythm_response_text", "")
        event.set_extra("chat_rhythm_response_text", actual)
        event.set_extra("companion_emotion_reply", actual)
        if not event.get_extra("companion_delivery_interrupted"):
            return
        event.set_extra("qqf_social_topics_used", [])
        event.set_extra("companion_purposes", [])
        await asyncio.to_thread(self.runtime.store.trace, event.unified_msg_origin, {
            "delivery": {"status": "interrupted_by_new_reply", "sent_text": actual,
                         "generated_draft_not_fully_sent": full[:8000],
                         "not_fact_memory": True, "extra_model_calls": 0}
        })
        async def repair():
            try:
                ok = await reconcile_native_history(self.plugin.context, event, full, actual)
                event.set_extra("companion_history_reconciled", ok)
            except Exception:
                event.set_extra("companion_history_reconciled", False)
        task = asyncio.create_task(repair())
        self._history_tasks.add(task)
        task.add_done_callback(self._history_tasks.discard)

    async def message_sent(self, event):
        """Record compact closure state after the existing send pipeline finishes."""
        key = event.get_extra("chat_rhythm_key")
        text = event.get_extra("chat_rhythm_response_text")
        if not key or not text or not getattr(event, "_has_send_oper", False):
            return
        current = self.active.get(key)
        if current and current[0] == str(getattr(event.message_obj, "message_id", "")):
            self.active.pop(key, None)
        settings = self.settings
        state = self.runtime.store.get_setting("rhythm:" + "|".join(key), {})
        # A short answer to a bot question can be consent, not a conversation end.
        expects_reply = bool(
            re.search(
                r"[?？]|(?:要不要|能不能|可不可以|行不行|告诉我|回复我|跟我说|发给我|给我看看|准备好|完成后|弄好后|选一个|等你)|[吗嘛么]\s*[。！!~～]?\s*$",
                text,
            )
        )
        state.update(last_sent=time.time(), expects_reply=expects_reply)
        if (
            settings["closure_enabled"]
            and event.get_extra("chat_rhythm_farewell")
            and event.get_extra("chat_rhythm_input_at", 0)
            >= state.get("last_user_at", 0)
        ):
            state["closed_until"] = (
                time.time() + settings["closure_cooldown_minutes"] * 60
            )
        self.runtime.store.set_setting("rhythm:" + "|".join(key), state)

    async def shutdown(self):
        """Release pending human messages on plugin reload without losing a batch."""
        self.closed = True
        await self.delivery.close()
        if self._history_tasks:
            await asyncio.gather(*list(self._history_tasks), return_exceptions=True)
        timers = [batch["timer"] for batch in self.pending.values()]
        for key, batch in list(self.pending.items()):
            self._finish_batch(key, batch)
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)

    def dashboard(self) -> dict:
        """Return effective controls and counters for this plugin lifetime."""
        return {
            "settings": self.settings,
            "pending_batches": len(self.pending),
            "delivery": self.delivery.status(),
            "counters": dict(self.counters),
            "scope": "白名单 QQ 私聊；仅合并纯文字，图片、语音、引用和命令保留原处理流程",
        }
