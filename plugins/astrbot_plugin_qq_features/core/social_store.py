"""Persistent social candidates, quotas and source-linked observations."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def midnight(at=None):
    return datetime.fromtimestamp(at or time.time(), CST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def fingerprint(text):
    return hashlib.sha256(str(text).strip().encode()).hexdigest()


def similar_public_text(left, right):
    def normalized(text):
        return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", str(text)).casefold())

    a, b = normalized(left), normalized(right)
    if not a or not b:
        return str(left).strip() == str(right).strip()
    if a == b:
        return True
    return min(len(a), len(b)) >= 12 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.88


class SocialStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS social_state(key TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS social_jobs(id TEXT PRIMARY KEY,scope TEXT,persona TEXT,kind TEXT,due REAL,payload TEXT,status TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS social_actions(id TEXT PRIMARY KEY,scope TEXT,kind TEXT,at REAL,status TEXT,fingerprint TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS social_public_texts(action_id TEXT PRIMARY KEY,text TEXT);
            CREATE TABLE IF NOT EXISTS social_observations(ref TEXT PRIMARY KEY,scope TEXT,persona TEXT,author TEXT,tid TEXT,url TEXT,text TEXT,summary TEXT,observed REAL,published REAL,memory_id INTEGER);
            CREATE TABLE IF NOT EXISTS social_topics(id TEXT PRIMARY KEY,scope TEXT,persona TEXT,ref TEXT,text TEXT,created REAL,expires REAL,status TEXT);
            CREATE TABLE IF NOT EXISTS social_observation_details(scope TEXT,persona TEXT,ref TEXT,payload TEXT,PRIMARY KEY(scope,persona,ref));
            """)
            db.execute(
                "UPDATE social_actions SET status='uncertain' WHERE status='reserved' AND kind IN ('publish','comment')"
            )
            db.execute("UPDATE social_jobs SET status='interrupted' WHERE status='running'")
            db.execute(
                "UPDATE social_jobs SET status='missed' WHERE status='pending' AND ((kind='browse' AND due<?) OR (kind IN ('unanswered','closure') AND due<?))",
                (time.time() - 3600, time.time() - 6 * 3600),
            )

    def get(self, key, default=None):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM social_state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, payload):
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO social_state VALUES(?,?)", (key, json.dumps(payload, ensure_ascii=False))
            )

    def add_job(self, scope, persona, kind, due, payload):
        jid = uuid.uuid4().hex[:12]
        with self.store.connect() as db:
            if kind in ("unanswered", "closure"):
                db.execute(
                    "UPDATE social_jobs SET status='superseded' WHERE scope=? AND kind=? AND status='pending'",
                    (scope, kind),
                )
            db.execute(
                "INSERT INTO social_jobs VALUES(?,?,?,?,?,?,?,?)",
                (jid, scope, persona, kind, due, json.dumps(payload, ensure_ascii=False), "pending", time.time()),
            )
            db.execute("DELETE FROM social_jobs WHERE created<? AND status!='pending'", (time.time() - 30 * 86400,))
        return jid

    def cancel_delayed(self, scope):
        with self.store.connect() as db:
            db.execute(
                "UPDATE social_jobs SET status='cancelled_reply' WHERE scope=? AND kind IN ('unanswered','closure') AND status='pending'",
                (scope,),
            )

    def cancel_browse(self, scope):
        with self.store.connect() as db:
            db.execute(
                "UPDATE social_jobs SET status='rescheduled' WHERE scope=? AND kind='browse' AND status='pending'",
                (scope,),
            )

    def jobs(self, scope, status="pending"):
        with self.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,scope,persona,kind,due,status,created FROM social_jobs WHERE scope=? AND status=? ORDER BY due LIMIT 20",
                    (scope, status),
                )
            ]

    def claim_due(self, scope, at=None):
        now = at or time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM social_jobs WHERE scope=? AND status='pending' AND due<=? ORDER BY due LIMIT 1",
                (scope, now),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE social_jobs SET status='running' WHERE id=? AND status='pending'", (row["id"],))
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            return item

    def finish_job(self, jid, status):
        with self.store.connect() as db:
            db.execute("UPDATE social_jobs SET status=? WHERE id=?", (status, jid))

    def postpone(self, jid, due):
        with self.store.connect() as db:
            db.execute("UPDATE social_jobs SET due=?,status='pending' WHERE id=?", (due, jid))

    def count(self, scope, kind, since):
        with self.store.connect() as db:
            statuses = (
                "('reserved','success','uncertain')"
                if kind in ("publish", "comment")
                else "('reserved','success','uncertain','failed')"
            )
            return db.execute(
                f"SELECT COUNT(*) FROM social_actions WHERE scope=? AND kind=? AND at>=? AND status IN {statuses}",
                (scope, kind, since),
            ).fetchone()[0]

    def reserve(self, scope, kind, limit, since, key, content_fingerprint="", content_text=""):
        aid = fingerprint(scope + "|" + kind + "|" + key)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM social_actions WHERE id=?", (aid,)).fetchone():
                raise ValueError("这个自主动作已经处理过，不重复执行")
            statuses = (
                "('reserved','success','uncertain')"
                if kind in ("publish", "comment")
                else "('reserved','success','uncertain','failed')"
            )
            used = db.execute(
                f"SELECT COUNT(*) FROM social_actions WHERE scope=? AND kind=? AND at>=? AND status IN {statuses}",
                (scope, kind, since),
            ).fetchone()[0]
            if used >= limit:
                raise ValueError("自主活动已达到本周期频率上限")
            if kind == "publish" and content_text:
                prior = db.execute(
                    "SELECT t.text FROM social_public_texts t JOIN social_actions a ON t.action_id=a.id WHERE a.scope=? AND a.kind='publish' AND a.at>? AND a.status IN ('reserved','success','uncertain') UNION SELECT text FROM drafts WHERE scope=? AND kind='publish' AND updated>? AND status IN ('sending','published','uncertain')",
                    (scope, time.time() - 30 * 86400, scope, time.time() - 30 * 86400),
                ).fetchall()
                if any(similar_public_text(content_text, row[0]) for row in prior):
                    raise ValueError("近期说说内容相同或过于相似，跳过重复内容")
            if (
                content_fingerprint
                and db.execute(
                    "SELECT 1 FROM social_actions WHERE scope=? AND kind=? AND fingerprint=? AND at>? AND status IN ('reserved','success','uncertain')",
                    (scope, kind, content_fingerprint, time.time() - 30 * 86400),
                ).fetchone()
            ):
                raise ValueError("近期已经做过相同的空间互动，跳过重复内容")
            db.execute(
                "INSERT INTO social_actions VALUES(?,?,?,?,?,?,?)",
                (aid, scope, kind, time.time(), "reserved", content_fingerprint, ""),
            )
            if kind == "publish" and content_text:
                db.execute("INSERT INTO social_public_texts VALUES(?,?)", (aid, content_text[:2000]))
        return aid

    def recent_publications(self, scope):
        with self.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT kind,text,status FROM drafts WHERE scope=? AND updated>? AND status IN ('published','sending','uncertain') ORDER BY updated DESC LIMIT 8",
                    (scope, time.time() - 30 * 86400),
                )
            ]

    def finish_action(self, aid, status, result=""):
        with self.store.connect() as db:
            db.execute("UPDATE social_actions SET status=?,result=? WHERE id=?", (status, result[:500], aid))

    def observation(self, scope, persona, post):
        self.observation_detail(scope, persona, post)
        ref = post["ref"]
        with self.store.connect() as db:
            exists = db.execute("SELECT ref FROM social_observations WHERE ref=?", (ref,)).fetchone()
            if exists:
                db.execute('UPDATE social_observations SET observed=? WHERE ref=? AND scope=? AND persona=?',(time.time(),ref,scope,persona))
                return False
            db.execute(
                "INSERT INTO social_observations VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    ref,
                    scope,
                    persona,
                    str(post.get("author_id", "")),
                    str(post.get("tid", "")),
                    post["url"],
                    post["text"][:4000],
                    "",
                    time.time(),
                    post.get("published_at", 0),
                ),
            )
        return True

    def observation_detail(self, scope, persona, post):
        """Retain source labels and paid visual descriptions, never signed image URLs."""
        detail = {k: post[k] for k in ("author_name", "time", "visual_observation", "source_uri") if post.get(k)}
        if not detail:
            return
        with self.store.connect() as db:
            old = db.execute(
                "SELECT payload FROM social_observation_details WHERE scope=? AND persona=? AND ref=?",
                (scope, persona, post["ref"]),
            ).fetchone()
            payload = json.loads(old[0]) if old else {}
            payload.update(detail)
            db.execute(
                "INSERT OR REPLACE INTO social_observation_details VALUES(?,?,?,?)",
                (scope, persona, post["ref"], json.dumps(payload, ensure_ascii=False)),
            )

    def save_summary(self, ref, summary, memory_id=None):
        with self.store.connect() as db:
            db.execute(
                "UPDATE social_observations SET summary=?,memory_id=COALESCE(?,memory_id) WHERE ref=?",
                (summary[:600], memory_id, ref),
            )

    def observations(self, scope, limit=12, persona=None):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT o.*,d.payload AS observation_detail FROM social_observations o "
                "LEFT JOIN social_observation_details d ON d.scope=o.scope AND d.persona=o.persona AND d.ref=o.ref "
                "WHERE o.scope=? AND (? IS NULL OR o.persona=?) ORDER BY o.observed DESC LIMIT ?", (scope, persona, persona, limit)
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            detail = json.loads(value.pop('observation_detail') or '{}')
            value.update({k:v for k,v in detail.items() if k in ('author_name','time','visual_observation','source_uri')})
            result.append(value)
        return result

    def topic(self, scope, persona, ref, text, ttl_hours):
        tid = fingerprint(scope + "|" + persona + "|" + ref + "|" + text)[:14]
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO social_topics VALUES(?,?,?,?,?,?,?,?)",
                (tid, scope, persona, ref, text[:400], time.time(), time.time() + ttl_hours * 3600, "pending"),
            )
        return tid

    def topics(self, scope, persona=None):
        with self.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT t.*,o.url,o.author,o.observed,o.published,o.text AS source_text,o.summary AS source_summary FROM social_topics t LEFT JOIN social_observations o ON t.ref=o.ref AND t.scope=o.scope AND t.persona=o.persona WHERE t.scope=? AND (? IS NULL OR t.persona=?) AND t.status='pending' AND t.expires>? ORDER BY t.created DESC LIMIT 4",
                    (scope, persona, persona, time.time()),
                )
            ]

    def used_topics(self, ids):
        with self.store.connect() as db:
            for tid in ids:
                db.execute("UPDATE social_topics SET status='shared' WHERE id=?", (tid,))

    def prune(self, days):
        with self.store.connect() as db:
            db.execute("DELETE FROM social_topics WHERE expires<?", (time.time() - 7 * 86400,))
            db.execute(
                "DELETE FROM social_observations WHERE observed<? AND ref NOT IN (SELECT ref FROM social_topics WHERE status='pending' AND expires>?)",
                (time.time() - days * 86400, time.time()),
            )
            db.execute("DELETE FROM social_observation_details WHERE ref NOT IN (SELECT ref FROM social_observations)")
