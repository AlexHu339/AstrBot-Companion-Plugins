"""Bounded expression context and durable receipts for the existing audio path."""

from __future__ import annotations

import json
from astrbot.api.message_components import Record
from astrbot.api import logger
from .tts_api.speech_meter import meter_for


async def expression_context(plugin, event):
    """Read the current persona and scoped context without another model call."""
    scope = event.unified_msg_origin
    identity = event.get_extra("companion_identity")
    memory_scope = str(identity[0]) if identity else scope
    persona_id = str(identity[1]) if identity else "default"
    manager = plugin.context.conversation_manager
    cid = await manager.get_curr_conversation_id(scope)
    conv = await manager.get_conversation(scope, cid) if cid else None
    prompt = ""
    if conv and conv.persona_id and conv.persona_id != "[%None]":
        persona_id = str(conv.persona_id)
        card = await plugin.context.persona_manager.get_persona(persona_id)
        prompt = getattr(card, "system_prompt", "") if card else ""
    if not prompt:
        card = await plugin.context.persona_manager.get_default_persona_v3(umo=scope)
        prompt = (card or {}).get("prompt", "")
    background = {
        "persona": persona_id,
        "recent_dialogue": [],
        "memory": {},
        "affect": None,
    }
    meta = plugin.context.get_registered_star("astrbot_plugin_livingmemory")
    runtime = (
        getattr(getattr(meta, "star_cls", None), "companion", None)
        if meta and meta.activated
        else None
    )
    if runtime:
        with runtime.store.connect() as db:
            rows = db.execute(
                "SELECT role,text FROM archive WHERE scope=? AND persona=? AND hidden=0 ORDER BY id DESC LIMIT 4",
                (memory_scope, persona_id),
            ).fetchall()
        background["recent_dialogue"] = [
            {"role": r["role"], "text": r["text"][:240]} for r in reversed(rows)
        ]
        for kind in ("facts", "continuity", "purposes"):
            background["memory"][kind] = [
                r["text"][:140]
                for r in runtime.store.active_facets(memory_scope, persona_id, kind)[:2]
            ]
        background["affect"] = runtime.emotion._current(memory_scope, persona_id).get(
            "emotions", {}
        )
    elif conv:
        history = (
            json.loads(conv.history)
            if isinstance(conv.history, str)
            else (conv.history or [])
        )
        background["recent_dialogue"] = [
            {"role": r.get("role"), "text": str(r.get("content", ""))[:240]}
            for r in history[-4:]
            if r.get("role") in ("user", "assistant")
        ]
    return prompt, background, persona_id


def save_receipt(event, persona, state, text):
    """Persist only bounded text and status; never audio bytes or credentials."""
    try:
        meter = meter_for(__file__)
        if meter:
            meter.media_receipt(
                event.unified_msg_origin,
                persona,
                "voice",
                str(event.message_obj.message_id),
                state,
                text=text,
            )
    except Exception:
        logger.warning("Voice receipt persistence failed")


def track_delivery(event, delivery, record):
    """Confirm this specific audio, rather than another component in the reply."""
    original = event.send

    async def send(chain):
        contains = any(
            isinstance(c, Record) and c.file == record.file
            for c in getattr(chain, "chain", [])
        )
        try:
            result = await original(chain)
        except Exception:
            if contains:
                save_receipt(
                    event,
                    delivery.get("persona", "default"),
                    "failed",
                    delivery["text"],
                )
            raise
        if contains:
            delivered = result is not False
            event.set_extra("clonetts_voice_delivered", delivered)
            save_receipt(
                event,
                delivery.get("persona", "default"),
                "sent" if delivered else "delivery_unknown",
                delivery["text"],
            )
            event.set_extra("clonetts_receipt_done", True)
        return result

    event.send = send
