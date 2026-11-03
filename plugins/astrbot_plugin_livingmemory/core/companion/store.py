"""Local source archive, derived continuity and an auditable usage ledger."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from .policy import LOCAL_TZ, clip_tokens, price_for, redact, source_hash
from .billing import platform_for, platform_totals


class CompanionStore:
    """Persist additions without creating a second semantic-memory index."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "companion.db"
        self.deletion_journal = root / "deletion_tombstones.jsonl"
        self._deleted = set()
        if self.deletion_journal.exists():
            for line in self.deletion_journal.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    self._deleted.add((item["namespace"], item["digest"]))
                except (ValueError, KeyError, TypeError):
                    continue
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger(
                    id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, at REAL NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, category TEXT NOT NULL,
                    scope TEXT NOT NULL, input_other INTEGER, input_cached INTEGER, output INTEGER,
                    amount REAL, currency TEXT NOT NULL, state TEXT NOT NULL, elapsed REAL NOT NULL,
                    price_snapshot TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ledger_at ON ledger(at);
                CREATE TABLE IF NOT EXISTS archive(
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, scope TEXT NOT NULL,
                    persona TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                    digest TEXT NOT NULL, at REAL NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(scope,persona,event_id,role));
                CREATE INDEX IF NOT EXISTS archive_scope ON archive(scope,persona,at);
                CREATE TABLE IF NOT EXISTS tombstones(scope TEXT,persona TEXT,digest TEXT,
                    PRIMARY KEY(scope,persona,digest));
                CREATE TABLE IF NOT EXISTS facets(
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, persona TEXT NOT NULL,
                    kind TEXT NOT NULL, fact_key TEXT NOT NULL, text TEXT NOT NULL,
                    evidence TEXT NOT NULL, source_id INTEGER NOT NULL, status TEXT NOT NULL,
                    confidence REAL NOT NULL, at REAL NOT NULL, expires REAL,
                    last_used REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS facets_scope ON facets(scope,persona,kind,status);
                CREATE TABLE IF NOT EXISTS traces(
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, scope TEXT NOT NULL,
                    payload TEXT NOT NULL);
            """)
            for row in db.execute(
                "SELECT id,scope,persona,digest FROM archive WHERE hidden=0"
            ).fetchall():
                if (
                    source_hash(row["scope"] + "|" + row["persona"]),
                    row["digest"],
                ) in self._deleted:
                    db.execute(
                        "UPDATE archive SET hidden=1,text='' WHERE id=?", (row["id"],)
                    )

    @contextmanager
    def connect(self):
        """Open a short-lived WAL connection with a bounded busy wait."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_setting(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def install_rates(self, defaults: dict, configured: dict) -> dict:
        """Add missing model rates and price complete, previously unpriced usage.

        Preserve overrides, paid estimates and original token counts. A local
        audit row retains every changed price field; no model call is made.
        """
        updated, amount = 0, 0.0
        with self.connect() as db:
            saved = db.execute("SELECT value FROM settings WHERE key='rates'").fetchone()
            rates = {**defaults, **configured, **(json.loads(saved[0]) if saved else {})}
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('rates',?)",
                (json.dumps(rates, ensure_ascii=False),),
            )
            db.execute("""CREATE TABLE IF NOT EXISTS pricing_backfills(
                ledger_id INTEGER PRIMARY KEY, at REAL NOT NULL,
                previous_currency TEXT NOT NULL, previous_state TEXT NOT NULL,
                previous_snapshot TEXT NOT NULL)""")
            rows = db.execute(
                "SELECT * FROM ledger WHERE amount IS NULL AND state='price_missing' "
                "AND input_other IS NOT NULL AND input_cached IS NOT NULL AND output IS NOT NULL"
            ).fetchall()
            for row in rows:
                rule, currency = price_for(row["model"], rates, row["at"])
                if rule is None or currency != "CNY":
                    continue
                try:
                    prices = [float(rule[key]) for key in ("input", "cached", "output")]
                except (KeyError, TypeError, ValueError):
                    continue
                counts = [row[key] for key in ("input_other", "input_cached", "output")]
                if any(not math.isfinite(x) or x < 0 for x in [*prices, *counts]):
                    continue
                cost = sum(n * p for n, p in zip(counts, prices)) / 1_000_000
                db.execute(
                    "INSERT OR IGNORE INTO pricing_backfills VALUES(?,?,?,?,?)",
                    (row["id"], time.time(), row["currency"], row["state"], row["price_snapshot"]),
                )
                db.execute(
                    "UPDATE ledger SET amount=?,currency=?,state='priced_backfill',price_snapshot=? WHERE id=?",
                    (cost, currency, json.dumps(rule, ensure_ascii=False), row["id"]),
                )
                updated += 1
                amount += cost
        return {"updated": updated, "amount_cny": amount}

    def archive(
        self,
        scope: str,
        persona: str,
        event_id: str,
        role: str,
        text: str,
        at: float | None = None,
    ):
        """Archive sanitized source text, respecting deletion tombstones."""
        text = redact(text).strip()
        if not text:
            return
        digest = source_hash(text)
        if (source_hash(scope + "|" + persona), digest) in self._deleted:
            return
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM tombstones WHERE scope=? AND persona=? AND digest=?",
                (scope, persona, digest),
            ).fetchone():
                return
            db.execute(
                "INSERT OR IGNORE INTO archive(event_id,scope,persona,role,text,digest,at) VALUES(?,?,?,?,?,?,?)",
                (event_id, scope, persona, role, text, digest, at or time.time()),
            )

    def ingest(self, source_id: int, scope: str, persona: str, companion: dict):
        """Maintain source-linked facts and pending topics with replacement history."""
        now = time.time()
        with self.connect() as db:
            for kind in ("facts", "continuity", "purposes"):
                for item in companion.get(kind, []):
                    key, text = item["key"], item["text"]
                    old = db.execute(
                        "SELECT * FROM facets WHERE scope=? AND persona=? AND kind=? AND fact_key=? AND status='open' ORDER BY at DESC LIMIT 1",
                        (scope, persona, kind, key),
                    ).fetchone()
                    if old and old["source_id"] == source_id and old["text"] == text:
                        continue
                    if old and old["text"] == text and item["status"] == "open":
                        continue
                    if old:
                        db.execute(
                            "UPDATE facets SET status='superseded' WHERE id=?",
                            (old["id"],),
                        )
                    expires = (
                        None if kind == "facts" else now + item["expires_days"] * 86400
                    )
                    db.execute(
                        "INSERT INTO facets(scope,persona,kind,fact_key,text,evidence,source_id,status,confidence,at,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            scope,
                            persona,
                            kind,
                            key,
                            text,
                            item["evidence"],
                            source_id,
                            item["status"],
                            item["confidence"],
                            now,
                            expires,
                        ),
                    )

    def rebind_consolidation_sources(self, old_ids: list[int], new_id: int):
        """Change provenance only: do not reopen expired or superseded facets."""
        ids = list(dict.fromkeys(int(i) for i in old_ids))
        if not ids:
            return
        placeholders = ','.join('?' for _ in ids)
        with self.connect() as db:
            db.execute(f'UPDATE facets SET source_id=? WHERE source_id IN ({placeholders})', (int(new_id), *ids))

    def rebind_facets(self, old_id: int, new_id: int, invalidate: bool = False):
        """Follow a rebuilt record, dropping stale derivatives after manual edits."""
        with self.connect() as db:
            if invalidate:
                db.execute("DELETE FROM facets WHERE source_id=?", (old_id,))
            else:
                db.execute(
                    "UPDATE facets SET source_id=? WHERE source_id=?", (new_id, old_id)
                )

    def active_facets(self, scope: str, persona: str, kind: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM facets WHERE scope=? AND persona=? AND kind=? AND status='open' AND (expires IS NULL OR expires>?) ORDER BY confidence DESC,at DESC LIMIT 20",
                (scope, persona, kind, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

    def continuity_block(
        self, scope: str, persona: str, budgets: dict, proactive: bool = False
    ) -> tuple[str, list[dict]]:
        """Render only bounded, evidence-backed continuity and one optional purpose."""
        blocks, purposes = [], []
        for kind, label in (
            ("facts", "关键事实"),
            ("continuity", "近期连续状态"),
            ("purposes", "可选话头"),
        ):
            rows = self.active_facets(scope, persona, kind)
            if kind == "purposes":
                rows = [r for r in rows if time.time() - r["last_used"] > 86400]
                purposes = rows[:1]
                rows = purposes if proactive else rows[:2]
            if rows:
                body = "\n".join(
                    f"- {r['text']}（来源记忆 {r['source_id']}）" for r in rows
                )
                blocks.append(
                    clip_tokens(label + "：\n" + body, budgets.get(kind, 200))
                )
        return "\n".join(blocks), purposes

    def mark_purpose_used(self, facet_id: int):
        with self.connect() as db:
            db.execute(
                "UPDATE facets SET last_used=? WHERE id=?", (time.time(), facet_id)
            )

    def forget_sources(
        self, source_id: int, scope: str, persona: str, texts: list[str]
    ):
        """Invalidate derived facets and prevent source archive resurrection."""
        with self.connect() as db:
            db.execute("DELETE FROM facets WHERE source_id=?", (source_id,))
            for text in texts:
                digest = source_hash(text)
                namespace = source_hash(scope + "|" + persona)
                if (namespace, digest) not in self._deleted:
                    with self.deletion_journal.open("a", encoding="utf-8") as stream:
                        stream.write(
                            json.dumps({"namespace": namespace, "digest": digest})
                            + "\n"
                        )
                    self._deleted.add((namespace, digest))
                db.execute(
                    "INSERT OR IGNORE INTO tombstones VALUES(?,?,?)",
                    (scope, persona, digest),
                )
                db.execute(
                    "UPDATE archive SET text='',hidden=1 WHERE scope=? AND persona=? AND digest=?",
                    (scope, persona, digest),
                )

    def archive_sources(
        self,
        source_id: int,
        scope: str,
        persona: str,
        texts: list[str],
        restore: bool = False,
    ):
        """Hide soft-forgotten sources, retaining a reversible archive."""
        with self.connect() as db:
            db.execute(
                "UPDATE facets SET status=? WHERE source_id=? AND status=?",
                (
                    "open" if restore else "archived",
                    source_id,
                    "archived" if restore else "open",
                ),
            )
            for text in texts:
                digest = source_hash(text)
                if (
                    restore
                    and db.execute(
                        "SELECT 1 FROM tombstones WHERE scope=? AND persona=? AND digest=?",
                        (scope, persona, digest),
                    ).fetchone()
                ):
                    continue
                db.execute(
                    "UPDATE archive SET hidden=? WHERE scope=? AND persona=? AND digest=?",
                    (0 if restore else 1, scope, persona, digest),
                )

    def search_archive(
        self, scope: str, persona: str, query: str, limit: int = 2
    ) -> list[dict]:
        """Read old text by explicit clues; age never excludes an old event."""
        import re

        terms = re.findall(r"[A-Za-z0-9_]{3,}|[\u3400-\u9fff]{2,}", query)
        terms = [
            x
            for x in terms
            if x not in ("你还记得", "还记得", "之前", "以前", "上次", "什么", "那次")
        ]
        terms = [
            x
            for t in terms[:6]
            for x in (
                [t] if len(t) < 5 else [t] + [t[i : i + 2] for i in range(len(t) - 1)]
            )
        ]
        if not terms:
            return []
        with self.connect() as db:
            clause = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in terms)
            escaped = [
                t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                for t in terms
            ]
            rows = db.execute(
                f"SELECT id,text,at FROM archive WHERE scope=? AND persona=? AND hidden=0 AND role='user' AND ({clause}) LIMIT 500",
                [scope, persona, *[f"%{x}%" for x in escaped]],
            ).fetchall()
        ranked = sorted(
            rows,
            key=lambda r: sum(len(t) for t in terms if t in r["text"]),
            reverse=True,
        )
        return [
            {"id": r["id"], "text": clip_tokens(r["text"], 180), "at": r["at"]}
            for r in ranked[:limit]
        ]

    def record_usage(
        self,
        *,
        request_id: str,
        provider: str,
        model: str,
        category: str,
        scope: str,
        usage: dict | None,
        elapsed: float,
        rates: dict,
        at: float | None = None,
        state: str = "completed",
    ):
        """Record actual provider usage once; missing usage is not zero spending."""
        at = at or time.time()
        rule, currency = price_for(model, rates, at)
        amount = None
        if usage is not None and rule is not None:
            amount = (
                usage["input_other"] * float(rule["input"])
                + usage["input_cached"] * float(rule["cached"])
                + usage["output"] * float(rule["output"])
            ) / 1_000_000
        if usage is None:
            state = "usage_missing" if state == "completed" else state
        elif rule is None:
            state = "price_missing"
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO ledger(request_id,at,provider,model,category,scope,input_other,input_cached,output,amount,currency,state,elapsed,price_snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    at,
                    provider,
                    model,
                    category,
                    scope,
                    *(
                        usage.get(k) if usage is not None else None
                        for k in ("input_other", "input_cached", "output")
                    ),
                    amount,
                    currency,
                    state,
                    elapsed,
                    json.dumps(rule or {}),
                ),
            )

    def trace(self, scope: str, payload: dict):
        with self.connect() as db:
            db.execute(
                "INSERT INTO traces(at,scope,payload) VALUES(?,?,?)",
                (
                    time.time(),
                    scope,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )
            db.execute(
                "DELETE FROM traces WHERE id NOT IN (SELECT id FROM traces ORDER BY id DESC LIMIT 100)"
            )

    def set_balance_reference(self, platform: str, amount: float) -> dict:
        """Calibrate a manual balance without copying login credentials."""
        if platform != "mimo" or isinstance(amount, bool):
            raise ValueError("仅支持校准 MiMo 的人民币余额")
        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("请输入有效的人民币余额") from exc
        if not math.isfinite(amount) or not -10000 <= amount <= 1000000:
            raise ValueError("余额须在 -10000 至 1000000 元之间")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cutoff = db.execute("SELECT COALESCE(max(id),0) FROM ledger").fetchone()[0]
            reference = {"amount_cny": amount, "at": time.time(), "ledger_id": cutoff}
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                ("balance_reference_mimo", json.dumps(reference)),
            )
        return reference

    def balance_estimate(self, aliases: dict | None = None) -> dict:
        """Subtract only calls recorded after calibration, excluding old repricing."""
        with self.connect() as db:
            saved = db.execute("SELECT value FROM settings WHERE key='balance_reference_mimo'").fetchone()
            if not saved:
                return {"state": "not_set", "platform": "mimo"}
            reference = json.loads(saved[0])
            rows = db.execute(
                "SELECT provider,amount,currency FROM ledger WHERE id>?", (reference["ledger_id"],)
            ).fetchall()
        selected = [r for r in rows if platform_for(r["provider"], aliases) == "mimo"]
        spending = sum(float(r["amount"] or 0) for r in selected if r["currency"] == "CNY")
        return {
            "state": "estimated", "platform": "mimo", "currency": "CNY",
            "reference_cny": reference["amount_cny"], "at": reference["at"],
            "amount_cny": reference["amount_cny"] - spending,
            "deducted_cny": spending,
            "pending": sum(r["amount"] is None or r["currency"] != "CNY" for r in selected),
        }

    def dashboard(self, aliases: dict | None = None) -> dict:
        """Return monetary totals by currency without exposing source credentials."""
        now = datetime.now(LOCAL_TZ)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self.connect() as db:
            rows = db.execute(
                "SELECT category,provider,model,currency,count(*) calls,sum(input_other) input_other,sum(input_cached) input_cached,sum(output) output,sum(amount) amount,sum(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) unknown FROM ledger WHERE at>=? GROUP BY category,provider,model,currency ORDER BY category,provider,model,currency",
                (today,),
            ).fetchall()
            monthly = db.execute(
                "SELECT currency,sum(amount) amount,count(*) calls,sum(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) unknown FROM ledger WHERE at>=? GROUP BY currency",
                (month,),
            ).fetchall()
            month_details = db.execute(
                "SELECT category,provider,model,currency,count(*) calls,sum(input_other) input_other,sum(input_cached) input_cached,sum(output) output,sum(amount) amount,sum(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) unknown FROM ledger WHERE at>=? GROUP BY category,provider,model,currency ORDER BY category,provider,model,currency",
                (month,),
            ).fetchall()
            traces = db.execute(
                "SELECT at,scope,payload FROM traces ORDER BY id DESC LIMIT 10"
            ).fetchall()
            counts = {
                "archive_messages": db.execute(
                    "SELECT count(*) FROM archive WHERE hidden=0"
                ).fetchone()[0],
                "active_facets": db.execute(
                    "SELECT count(*) FROM facets WHERE status='open' AND (expires IS NULL OR expires>?)",
                    (time.time(),),
                ).fetchone()[0],
            }
            started = db.execute("SELECT min(at) FROM ledger").fetchone()[0]
        today_rows, month_rows = [dict(r) for r in rows], [dict(r) for r in month_details]
        return {
            "today": today_rows,
            "month": [dict(r) for r in monthly],
            "month_details": month_rows,
            "platforms": platform_totals(today_rows, month_rows, aliases),
            "counts": counts,
            "started_at": started,
            "traces": [
                {"at": r["at"], "scope": r["scope"], **json.loads(r["payload"])}
                for r in traces
            ],
            "timezone": "Asia/Shanghai",
        }

    def backup(self, keep: int = 3):
        """Take a consistent SQLite snapshot with bounded daily rotation."""
        backup_root = self.root / "backups"
        backup_root.mkdir(exist_ok=True)
        today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        for source in self.root.glob("*.db"):
            destination = backup_root / (today + "-" + source.name)
            if not destination.exists():
                with (
                    closing(
                        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
                    ) as src,
                    closing(sqlite3.connect(destination)) as dst,
                ):
                    src.backup(dst)
        snapshots = list(backup_root.glob("????-??-??-*.db"))
        days = sorted({p.name[:10] for p in snapshots})
        for old in snapshots:
            if old.name[:10] in days[: -max(1, keep)]:
                old.unlink()
