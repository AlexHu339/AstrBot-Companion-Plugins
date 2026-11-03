"""Read live companion state; preserve its existing persona/context policies."""

from __future__ import annotations

import time
import asyncio
import json
import sys
import sqlite3


class CompanionBridge:
    def __init__(self, context):
        self.context = context

    def runtime(self):
        meta = self.context.get_registered_star("astrbot_plugin_livingmemory")
        return getattr(getattr(meta, "star_cls", None), "companion", None) if meta and meta.activated else None

    async def identity(self, event):
        identity = event.get_extra("companion_identity")
        if identity:
            return identity[0], identity[1]
        scope = event.unified_msg_origin
        cid = await self.context.conversation_manager.get_curr_conversation_id(scope)
        if cid:
            conversation = await self.context.conversation_manager.get_conversation(scope, cid)
            if conversation and conversation.persona_id and conversation.persona_id != "[%None]":
                return scope, str(conversation.persona_id)
        return scope, "default"

    def snapshot(self, scope, persona):
        runtime = self.runtime()
        if not runtime:
            return {"connected": False, "emotion": None, "purposes": []}
        emotion = None
        try:
            emotion = runtime.emotion._current(scope, persona)
        except Exception:
            pass
        try:
            purposes = runtime.store.active_facets(scope, persona, "purposes")[:3]
            purposes = [{"text": r["text"], "status": r.get("status", "open")} for r in purposes]
        except Exception:
            purposes = []
        return {"connected": True, "emotion": emotion, "purposes": purposes}

    def can_self_signature(self, scope, persona, user_text):
        import re

        cue = bool(re.search(r"(?:今天|明天|我的|你|生日|夸|真棒|可爱|喜欢你|爱你|委屈|难过)", user_text))
        state = self.snapshot(scope, persona).get("emotion") or {}
        fresh = time.time() - float(state.get("last_assessed") or 0) < 6 * 3600
        strong = max((state.get("emotions") or {}).values(), default=0) >= 12
        return bool((fresh and strong) or (cue and re.search(r"生日|真棒|可爱|喜欢你|爱你|委屈|难过", user_text)))

    def available(self):
        return self.runtime() is not None

    async def background_context(self, event):
        scope, persona = await self.identity(event)
        cid = await self.context.conversation_manager.get_curr_conversation_id(scope)
        conv = await self.context.conversation_manager.get_conversation(scope, cid) if cid else None
        prompt = ""
        if conv and conv.persona_id and conv.persona_id != "[%None]":
            card = await self.context.persona_manager.get_persona(conv.persona_id)
            prompt = getattr(card, "system_prompt", "") if card else ""
        if not prompt:
            card = await self.context.persona_manager.get_default_persona_v3(umo=scope)
            prompt = (card or {}).get("prompt", "")
        if not prompt:
            raise ValueError("没有可用人格，暂停自主生成，避免使用另一个临时人设")
        history = getattr(conv, "history", []) if conv else []
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except (ValueError, TypeError):
                history = []
        recent = []
        for m in (history or [])[-8:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                text = m.get("content", "")
                if isinstance(text, str):
                    recent.append({"role": m["role"], "text": text[:300]})
        runtime = self.runtime()
        facets = {}
        if runtime:
            # The companion archive excludes synthetic proactive user prompts.
            # Prefer it over host history when preparing an independent social decision.
            try:
                with runtime.store.connect() as db:
                    rows = db.execute(
                        "SELECT role,text FROM archive WHERE scope=? AND persona=? AND hidden=0 AND role IN ('user','assistant') ORDER BY id DESC LIMIT 8",
                        (scope, persona),
                    ).fetchall()
                if rows:
                    recent = [{"role": r["role"], "text": r["text"][:300]} for r in reversed(rows)]
            except Exception:
                pass
            for kind in ("facts", "continuity", "purposes"):
                facets[kind] = [r["text"] for r in runtime.store.active_facets(scope, persona, kind)[:3]]
        return prompt, {
            "persona": persona,
            "recent_dialogue": recent,
            "memory_background": facets,
            **self.snapshot(scope, persona),
        }

    async def auxiliary(self, event, category, prompt, system_prompt, image_urls=None, provider_id=""):
        runtime = self.runtime()
        variable = None
        token = None
        if runtime:
            runtime.wrap_providers()
            variable = getattr(sys.modules.get(runtime.__class__.__module__), "CALL_CONTEXT", None)
            if variable:
                token = variable.set((event.unified_msg_origin, category))
        try:
            pid = provider_id or await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            result = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=pid,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    image_urls=image_urls or [],
                    max_tokens=1000,
                ),
                timeout=60,
            )
            return result.completion_text or ""
        finally:
            if variable and token is not None:
                variable.reset(token)

    async def remember_observation(self, event, post, summary, evidence):
        runtime = self.runtime()
        engine = getattr(getattr(getattr(runtime, "plugin", None), "initializer", None), "memory_engine", None)
        if not engine:
            return None
        scope, persona = await self.identity(event)

        def existing_id():
            root = getattr(runtime, "root", None)
            if root is None:
                return None
            path = root / "livingmemory.db"
            if not path.exists():
                return None
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute(
                    "SELECT doc_id FROM documents WHERE json_extract(metadata,'$.source_type')='qzone_observation' AND json_extract(metadata,'$.source_key')=? AND json_extract(metadata,'$.session_id')=? AND json_extract(metadata,'$.persona_id')=? LIMIT 1",
                    (post["ref"], scope, persona),
                ).fetchone()
            return row[0] if row else None

        existing = await asyncio.to_thread(existing_id)
        if existing:
            if post.get("discussion"):
                memory = await engine.get_memory(existing)
                metadata = (memory or {}).get("metadata", {})
                metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                quotes = list(metadata.get("external_discussion", []))
                if post["discussion"] not in quotes:
                    quotes = (quotes + [post["discussion"][:300]])[-3:]
                    await engine.update_memory(existing, {"metadata": {"external_discussion": quotes}})
            return existing
        # External memories never call ingest(), which maintains the owner's facts and promises.
        content = (
            f"【QQ空间观察｜外部来源，不是主人聊天事实】作者：{post.get('author_name', '')}（{post.get('author_id', '')}）；"
            f"原发布时间：{post.get('time', '未知')}。读到的原文：{post.get('text', '')[:500]}。"
            f"图片观察（模型描述，身份关系可能不确定）：{post.get('visual_observation', '')[:250]}。"
            f"读后印象（模型概括）：{summary[:180]}。来源：{(post.get('url') or post.get('source_uri') or post['ref'])}。别人的经历不能移植给主人。"
        )
        if post.get("discussion"):
            content += " 用户围绕此来源的当时原话：" + post["discussion"][:300] + "。这是原话，不推断承诺或已发生经历。"
        mid = await engine.add_memory(
            content=content,
            session_id=scope,
            persona_id=persona,
            importance=0.35,
            metadata={
                "source_type": "qzone_observation",
                "memory_type": "EXTERNAL_OBSERVATION",
                "source_url": (post.get("url") or post.get("source_uri") or post["ref"]),
                "source_author": post.get("author_id", ""),
                "source_key": post["ref"],
                "not_user_fact": True,
                "admission_reason": post.get("admission_reason", "impression"),
                "external_discussion": [post["discussion"][:300]] if post.get("discussion") else [],
            },
            source_messages=[
                {
                    "role": "external",
                    "content": post.get("text", ""),
                    "source_url": (post.get("url") or post.get("source_uri") or post["ref"]),
                    "author_id": post.get("author_id", ""),
                    "published_at": post.get("published_at", 0),
                    "evidence": evidence,
                    "visual_observation": post.get("visual_observation", ""),
                }
            ],
        )
        return mid
