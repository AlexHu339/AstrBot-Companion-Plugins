"""Durable, source-scoped admission of meaningful external observations."""

from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from astrbot.api import logger

REASONS = {
    "owner_shared": "主人分享",
    "shared": "实际分享给主人",
    "discussion": "继续讨论",
    "explicit": "明确要求记住",
    "impression": "印象深刻",
}
FOLLOWUP = re.compile(
    r"这条|那条|这张|那张|这个地方|刚才|那个地方|我也想去|好想去|这个|那张图|这张图|记住.{0,8}(动态|见闻|说说)"
)


class ObservationMemory:
    def __init__(self, plugin):
        self.plugin = plugin
        self.worker = None
        self.closed = False
        with plugin.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observation_memory_jobs(
                scope TEXT,persona TEXT,ref TEXT,payload TEXT,reason TEXT,state TEXT,
                attempts INTEGER,next_try REAL,updated REAL,memory_id INTEGER,error TEXT,
                PRIMARY KEY(scope,persona,ref))""")

    async def start(self):
        with self.plugin.store.connect() as db:
            db.execute("UPDATE observation_memory_jobs SET state='queued' WHERE state='writing'")
        self.worker = asyncio.create_task(self.loop())

    async def stop(self):
        self.closed = True
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    def observation(self, scope, persona, ref):
        with self.plugin.store.connect() as db:
            row = db.execute(
                "SELECT * FROM social_observations WHERE ref=? AND scope=? AND persona=?", (ref, scope, persona)
            ).fetchone()
        if not row:
            return None
        row = dict(row)
        with self.plugin.store.connect() as db:
            detail = db.execute(
                "SELECT payload FROM social_observation_details WHERE scope=? AND persona=? AND ref=?",
                (scope, persona, ref),
            ).fetchone()
        return {
            "ref": ref,
            "text": row["text"],
            "author_id": row["author"],
            "author_name": "",
            "published_at": row["published"],
            "url": row["url"],
            "tid": row["tid"],
            **(json.loads(detail[0]) if detail else {}),
        }

    def enqueue(self, scope, persona, post, reason, summary="", evidence="", discussion=""):
        if reason not in REASONS or not post or not post.get("ref"):
            return False
        source = str(post.get("text", ""))
        if len(source.strip()) < 4 and not post.get("visual_observation"):
            return False
        if evidence and evidence not in source + "\n" + str(post.get("visual_observation", "")):
            return False
        post = {
            k: v
            for k, v in post.items()
            if k
            in (
                "ref",
                "text",
                "author_id",
                "author_name",
                "tid",
                "published_at",
                "time",
                "url",
                "visual_observation",
                "source_uri",
            )
        }
        payload = {
            "post": post,
            "summary": summary[:180] or "原文摘录：" + source[:160],
            "evidence": evidence or source[:80],
            "discussion": discussion[:300],
        }
        with self.plugin.store.connect() as db:
            old = db.execute(
                "SELECT state,payload FROM observation_memory_jobs WHERE scope=? AND persona=? AND ref=?",
                (scope, persona, post["ref"]),
            ).fetchone()
            if old and old[0] == "saved" and not discussion:
                return False
            if old:
                previous = json.loads(old[1])
                if not summary:
                    payload["summary"] = previous["summary"]
            db.execute(
                """INSERT INTO observation_memory_jobs VALUES(?,?,?,?,?,'queued',0,?,?,NULL,'')
                ON CONFLICT(scope,persona,ref) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,
                state='queued',attempts=0,next_try=excluded.next_try,updated=excluded.updated,error=''
                WHERE observation_memory_jobs.state!='writing'""",
                (
                    scope,
                    persona,
                    post["ref"],
                    json.dumps(payload, ensure_ascii=False),
                    reason,
                    time.time(),
                    time.time(),
                ),
            )
        return True

    async def on_request(self, event):
        if self.plugin.is_proactive(event):
            return
        scope, persona = await self.plugin.bridge.identity(event)
        shared = event.get_extra("qqf_shared_posts", [])
        key = "observation-active:" + scope + "|" + persona
        if shared:
            post = shared[0]
            self.plugin.social.db.set(key, {"ref": post["ref"], "at": time.time()})
            self.enqueue(scope, persona, post, "owner_shared")
            return
        active = self.plugin.social.db.get(key, {}) or {}
        text = event.get_message_str() or ""
        if active and time.time() - active.get("at", 0) < 1200 and FOLLOWUP.search(text):
            post = self.observation(scope, persona, active["ref"])
            reason = "explicit" if re.search(r"记住|记下来|别忘", text) else "discussion"
            self.enqueue(scope, persona, post, reason, discussion=text)
            self.plugin.social.db.set(key, {**active, "at": time.time()})
        elif active:
            self.plugin.social.db.set(key, {})

    async def after_sent(self, event, used_topics):
        if not getattr(event, "_has_send_oper", False):
            return
        scope, persona = await self.plugin.bridge.identity(event)
        for topic in used_topics:
            post = self.observation(scope, persona, topic["ref"])
            impression = topic.get("text", "")
            self.enqueue(scope, persona, post, "shared", summary=impression)
            self.plugin.social.db.set(
                "observation-active:" + scope + "|" + persona, {"ref": topic["ref"], "at": time.time()}
            )

    async def process_one(self):
        if not self.plugin.settings["general"]["enabled"] or not self.plugin.bridge.available():
            return False
        runtime = self.plugin.bridge.runtime()
        if not getattr(getattr(getattr(runtime, "plugin", None), "initializer", None), "memory_engine", None):
            return False
        with self.plugin.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM observation_memory_jobs WHERE state IN ('queued','retry') AND next_try<=? ORDER BY updated LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not row:
                return False
            job = dict(row)
            db.execute(
                "UPDATE observation_memory_jobs SET state='writing',attempts=attempts+1 WHERE scope=? AND persona=? AND ref=?",
                (job["scope"], job["persona"], job["ref"]),
            )
        scope = job["scope"]
        extras = {"companion_identity": (scope, job["persona"])}
        event = SimpleNamespace(unified_msg_origin=scope, get_extra=lambda k, default=None: extras.get(k, default))
        try:
            payload = json.loads(job["payload"])
            payload["post"]["admission_reason"] = job["reason"]
            payload["post"]["discussion"] = payload.get("discussion", "")
            mid = await self.plugin.bridge.remember_observation(
                event, payload["post"], payload["summary"], payload["evidence"]
            )
            if not mid:
                raise RuntimeError("memory_unavailable")
            self.plugin.social.db.save_summary(job["ref"], payload["summary"], mid)
            with self.plugin.store.connect() as db:
                db.execute(
                    "UPDATE observation_memory_jobs SET state='saved',memory_id=?,updated=?,error='' WHERE scope=? AND persona=? AND ref=?",
                    (mid, time.time(), scope, job["persona"], job["ref"]),
                )
        except asyncio.CancelledError:
            with self.plugin.store.connect() as db:
                db.execute(
                    "UPDATE observation_memory_jobs SET state='queued' WHERE scope=? AND persona=? AND ref=?",
                    (scope, job["persona"], job["ref"]),
                )
            raise
        except Exception as exc:
            attempts = job["attempts"] + 1
            with self.plugin.store.connect() as db:
                db.execute(
                    "UPDATE observation_memory_jobs SET state=?,next_try=?,updated=?,error=? WHERE scope=? AND persona=? AND ref=?",
                    (
                        "retry" if attempts < 3 else "failed",
                        time.time() + 30 * 4 ** (attempts - 1),
                        time.time(),
                        type(exc).__name__,
                        scope,
                        job["persona"],
                        job["ref"],
                    ),
                )
            logger.warning("Observation memory admission postponed (%s)", type(exc).__name__)
        return True

    async def loop(self):
        while not self.closed:
            try:
                await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Observation memory worker postponed (%s)", type(exc).__name__)
            await asyncio.sleep(2)

    def dashboard(self, scope):
        with self.plugin.store.connect() as db:
            rows = db.execute(
                "SELECT ref,reason,state,attempts,memory_id,error FROM observation_memory_jobs WHERE scope=? ORDER BY updated DESC LIMIT 20",
                (scope,),
            ).fetchall()
        return [{**dict(row), "reason_label": REASONS[row["reason"]]} for row in rows]
