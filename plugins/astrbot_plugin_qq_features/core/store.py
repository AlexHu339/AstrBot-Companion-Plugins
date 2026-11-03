"""Local action receipts and review queue, never a second memory database."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "qq_features.db"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,at REAL,scope TEXT,persona TEXT,kind TEXT,status TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS messages(scope TEXT,id TEXT,role TEXT,at REAL,text TEXT,PRIMARY KEY(scope,id));
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,scope TEXT,persona TEXT,kind TEXT,text TEXT,post_ref TEXT,status TEXT,created REAL,updated REAL,result TEXT);
            CREATE TABLE IF NOT EXISTS feeds(ref TEXT PRIMARY KEY,scope TEXT,payload TEXT,at REAL);
            CREATE TABLE IF NOT EXISTS signature_changes(id INTEGER PRIMARY KEY,scope TEXT,kind TEXT,at REAL);
            """)
            # An interrupted external write has an uncertain result: never blindly retry it.
            db.execute("UPDATE drafts SET status='uncertain' WHERE status='sending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def receipt(self, scope, persona, kind, status, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO receipts(at,scope,persona,kind,status,detail) VALUES(?,?,?,?,?,?)",
                (time.time(), scope, persona, kind, status, str(detail)[:600]),
            )
            db.execute("DELETE FROM receipts WHERE id NOT IN (SELECT id FROM receipts ORDER BY id DESC LIMIT 1000)")
            if kind in ("mood_signature", "signature") and status == "success":
                db.execute("INSERT INTO signature_changes(scope,kind,at) VALUES(?,?,?)", (scope, kind, time.time()))
                db.execute("DELETE FROM signature_changes WHERE at<?", (time.time() - 90 * 86400,))

    def recent_receipts(self, scope=None, limit=15):
        with self.connect() as db:
            if scope:
                rows = db.execute("SELECT * FROM receipts WHERE scope=? ORDER BY id DESC LIMIT ?", (scope, limit))
            else:
                rows = db.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def message(self, scope, mid, role, text, at=None):
        if not str(mid).isdigit():
            return
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?)",
                (scope, str(mid), role, at or time.time(), str(text)[:1200]),
            )
            db.execute("DELETE FROM messages WHERE at<?", (time.time() - 3600,))

    def messages(self, scope, role=None, minutes=10):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE scope=? AND at>? AND (? IS NULL OR role=?) ORDER BY at DESC LIMIT 20",
                (scope, time.time() - minutes * 60, role, role),
            )
            return [dict(r) for r in rows]

    def remove_message(self, scope, mid):
        with self.connect() as db:
            db.execute("DELETE FROM messages WHERE scope=? AND id=?", (scope, str(mid)))

    def message_within(self, scope, mid, role, seconds):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM messages WHERE scope=? AND id=? AND role=? AND at>?",
                (scope, str(mid), role, time.time() - seconds),
            ).fetchone()
            return dict(row) if row else None

    def signature_count(self, scope):
        import datetime

        now = datetime.datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*),MAX(at) FROM signature_changes WHERE scope=? AND kind='mood_signature' AND at>=?",
                (scope, midnight),
            ).fetchone()
            last = db.execute("SELECT MAX(at) FROM signature_changes WHERE scope=?", (scope,)).fetchone()[0]
            return row[0], float(last or 0)

    def draft(self, scope, persona, kind, text, post_ref=""):
        did = uuid.uuid4().hex[:10]
        now = time.time()
        with self.connect() as db:
            db.execute(
                "INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (did, scope, persona, kind, text, post_ref, "pending", now, now, ""),
            )
        return did

    def get_draft(self, did, scope):
        with self.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=? AND scope=?", (did, scope)).fetchone()
            return dict(row) if row else None

    def drafts(self, scope=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM drafts WHERE (? IS NULL OR scope=?) ORDER BY created DESC LIMIT 50", (scope, scope)
            )
            return [dict(r) for r in rows]

    def edit_draft(self, did, scope, text):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE drafts SET text=?,updated=? WHERE id=? AND scope=? AND status='pending'",
                (text, time.time(), did, scope),
            ).rowcount
        if not changed:
            raise ValueError("只能修改尚未提交的待审核草稿")

    def claim_draft(self, did, scope):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE drafts SET status='sending',updated=? WHERE id=? AND scope=? AND status='pending'",
                (time.time(), did, scope),
            ).rowcount
            if not changed:
                raise ValueError("草稿已处理、正在提交或结果待核对，不能重复发送")
            return dict(db.execute("SELECT * FROM drafts WHERE id=? AND scope=?", (did, scope)).fetchone())

    def draft_status(self, did, scope, status, result=""):
        with self.connect() as db:
            db.execute(
                "UPDATE drafts SET status=?,result=?,updated=? WHERE id=? AND scope=?",
                (status, result[:500], time.time(), did, scope),
            )

    def save_feed(self, scope, payload):
        import hashlib
        import re

        identity = str(payload.get("uin", "")) + "|" + str(payload.get("tid", ""))
        ref = (
            hashlib.sha256((scope + "|" + identity).encode()).hexdigest()[:16]
            if payload.get("tid")
            else uuid.uuid4().hex[:10]
        )
        if payload.get("source") == "public_recommendation" and re.fullmatch(
            r"[a-f0-9]{16}", str(payload.get("public_ref", ""))
        ):
            ref = payload["public_ref"]
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO feeds VALUES(?,?,?,?)",
                (ref, scope, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            db.execute("DELETE FROM feeds WHERE at<?", (time.time() - 86400,))
        return ref

    def feed(self, scope, ref):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM feeds WHERE ref=? AND scope=? AND at>?", (ref, scope, time.time() - 86400)
            ).fetchone()
            return json.loads(row[0]) if row else None
