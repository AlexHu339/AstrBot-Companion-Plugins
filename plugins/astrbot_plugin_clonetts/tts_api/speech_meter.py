"""Credential-free speech metering, shared verbatim with the two audio adapters."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
SERVICES = {
    "seed-icl-2.0": {
        "name": "声音复刻 2.0",
        "category": "voice_synthesis",
        "unit": "characters",
        "rate": 3 / 10000,
    },
    "seed-audio-1.0": {
        "name": "音频生成 1.0 · 唱歌",
        "category": "singing_audio",
        "unit": "seconds",
        "rate": 1 / 60,
    },
    "seed-tts-2.0": {
        "name": "语音合成 2.0 · 预设音色",
        "category": "preset_voice",
        "unit": "characters",
        "rate": 3 / 10000,
    },
}


def meter_for(adapter_file):
    """Resolve the configured memory directory without loading any credentials."""
    here = Path(adapter_file).resolve()
    plugins = next((p for p in here.parents if p.name == "plugins"), None)
    if plugins is None:
        return None
    config = plugins.parent / "config/astrbot_plugin_livingmemory_config.json"
    values = (
        json.loads(config.read_text(encoding="utf-8-sig")) if config.exists() else {}
    )
    root = (values.get("companion") or {}).get("data_root")
    if not root:
        from astrbot.api.star import StarTools

        root = StarTools.get_data_dir("astrbot_plugin_livingmemory")
    return SpeechMeter(Path(root))


class SpeechMeter:
    """Keep request-level measurements apart from platform quota snapshots."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "speech_usage.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS speech_calls(
                    id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    at REAL NOT NULL, finished REAL, service TEXT NOT NULL, category TEXT NOT NULL,
                    characters REAL, seconds REAL, char_basis TEXT, duration_basis TEXT,
                    state TEXT NOT NULL, mode TEXT NOT NULL, amount REAL, covered REAL,
                    uncovered REAL, rate REAL NOT NULL, origin TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS speech_at ON speech_calls(at);
                CREATE TABLE IF NOT EXISTS expenses(
                    id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, at REAL NOT NULL,
                    category TEXT NOT NULL, amount REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS media_receipts(
                    scope TEXT,persona TEXT,kind TEXT,event_id TEXT,state TEXT,
                    title TEXT,text TEXT,at REAL,
                    PRIMARY KEY(scope,persona,kind,event_id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def media_receipt(
        self, scope, persona, kind, event_id, state, *, title="", text=""
    ):
        """Keep actual media outcomes across reloads without replaying messages."""
        if kind not in ("voice", "singing") or state not in (
            "generating",
            "ready",
            "sent",
            "failed",
            "interrupted",
            "delivery_unknown",
        ):
            raise ValueError("Invalid media receipt")
        with self.connect() as db:
            db.execute(
                "INSERT INTO media_receipts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope,persona,kind,event_id) DO UPDATE SET state=excluded.state,title=excluded.title,text=excluded.text,at=excluded.at WHERE media_receipts.state!='sent' OR excluded.state='sent'",
                (
                    scope,
                    persona,
                    kind,
                    event_id,
                    state,
                    str(title)[:120],
                    str(text)[:600],
                    time.time(),
                ),
            )
            db.execute(
                "DELETE FROM media_receipts WHERE at<?", (time.time() - 30 * 86400,)
            )

    def recent_media(self, scope, persona, kind, max_age=86400):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM media_receipts WHERE scope=? AND persona=? AND kind=? AND at>? ORDER BY at DESC LIMIT 1",
                (scope, persona, kind, time.time() - max_age),
            ).fetchone()
        return dict(row) if row else None

    def interrupt_media(self, kind):
        with self.connect() as db:
            db.execute(
                "UPDATE media_receipts SET state='interrupted' WHERE kind=? AND state IN ('generating','ready')",
                (kind,),
            )

    def _setting(self, db, service):
        row = db.execute(
            "SELECT value FROM settings WHERE key=?", (service,)
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "mode": "trial"
                if service in ("seed-icl-2.0", "seed-audio-1.0")
                else "unknown",
                "remaining": None,
                "expires": None,
                "at": None,
                "cutoff": 0,
                "rate": SERVICES[service]["rate"],
                "precision": 0,
                "origin": "未校准",
            }
        )

    def begin(self, request_id, service, *, category=None, at=None, origin="live"):
        if service not in SERVICES:
            raise ValueError("Unsupported speech service")
        with self.connect() as db:
            settings = self._setting(db, service)
            db.execute(
                "INSERT OR IGNORE INTO speech_calls(request_id,at,service,category,state,mode,rate,origin) VALUES(?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    time.time() if at is None else at,
                    service,
                    category or SERVICES[service]["category"],
                    "pending",
                    settings["mode"],
                    settings["rate"],
                    origin,
                ),
            )

    def finish(
        self,
        request_id,
        *,
        characters=None,
        seconds=None,
        char_basis="provider",
        duration_basis="provider",
        state="completed",
    ):
        """Settle once, before local packaging or QQ delivery can fail."""
        for value in (characters, seconds):
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Invalid usage")
        if state not in ("completed", "rejected", "uncertain"):
            raise ValueError("Invalid request state")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM speech_calls WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None or row["state"] != "pending":
                return
            settings = self._setting(db, row["service"])
            unit = SERVICES[row["service"]]["unit"]
            units = characters if unit == "characters" else seconds
            amount, covered, uncovered = None, 0, None
            mode = row["mode"]
            if state == "rejected":
                amount, units, uncovered = 0, 0, 0
            elif state == "completed" and units is not None:
                if mode == "paid":
                    amount, uncovered = units * row["rate"], units
                elif (
                    mode in ("trial", "package")
                    and settings["mode"] == mode
                    and settings["remaining"] is not None
                    and row["id"] > settings["cutoff"]
                    and (not settings["expires"] or row["at"] < settings["expires"])
                ):
                    used = db.execute(
                        "SELECT COALESCE(sum(covered),0) FROM speech_calls WHERE service=? AND id>?",
                        (row["service"], settings["cutoff"]),
                    ).fetchone()[0]
                    covered = min(units, max(0, settings["remaining"] - used))
                    uncovered = units - covered
                    amount = 0 if uncovered <= 1e-8 else None
                else:
                    uncovered = units
            db.execute(
                "UPDATE speech_calls SET finished=?,characters=?,seconds=?,char_basis=?,duration_basis=?,state=?,amount=?,covered=?,uncovered=? WHERE request_id=?",
                (
                    time.time(),
                    characters,
                    seconds,
                    char_basis,
                    duration_basis,
                    state,
                    amount,
                    covered,
                    uncovered,
                    request_id,
                ),
            )

    def calibrate(self, service, payload):
        """Store a console snapshot; never rewrite historical billing modes."""
        allowed = {"mode", "remaining", "expires", "rate", "precision", "origin"}
        if (
            service not in SERVICES
            or not isinstance(payload, dict)
            or set(payload) - allowed
        ):
            raise ValueError("未知服务或校准字段")
        mode = payload.get("mode")
        if mode not in ("trial", "package", "paid", "unknown"):
            raise ValueError("请选择试用、资源包、按量付费或待确认")
        remaining = payload.get("remaining")
        expires = payload.get("expires")
        rate = payload.get("rate", SERVICES[service]["rate"])
        precision = payload.get("precision", 0)
        for key, value in (
            ("remaining", remaining),
            ("expires", expires),
            ("rate", rate),
            ("precision", precision),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or value > 1e12
            ):
                raise ValueError(f"{key} 必须是非负数")
        if rate is None or precision is None:
            raise ValueError("请填写有效单价和显示精度")
        if mode in ("trial", "package") and remaining is None:
            raise ValueError("请填写控制台当前剩余量")
        origin = str(payload.get("origin") or "用户校准")
        if origin not in ("用户校准", "用户截图"):
            raise ValueError("不支持此校准来源")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cutoff = db.execute(
                "SELECT COALESCE(max(id),0) FROM speech_calls"
            ).fetchone()[0]
            if db.execute(
                "SELECT count(*) FROM speech_calls WHERE service=? AND state='pending' AND at>?",
                (service, time.time() - 1800),
            ).fetchone()[0]:
                raise ValueError(
                    "仍有生成请求未结算，请等生成结束后校准，避免重复扣额度"
                )
            saved = {
                "mode": mode,
                "remaining": remaining,
                "expires": expires or None,
                "rate": rate,
                "precision": precision,
                "origin": origin,
                "cutoff": cutoff,
                "at": time.time(),
            }
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                (service, json.dumps(saved)),
            )
        return saved

    def set_wallet(self, amount):
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or not -10000 <= amount <= 1e6
        ):
            raise ValueError("请输入有效的火山账户余额")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT count(*) FROM speech_calls WHERE state='pending' AND at>?",
                (time.time() - 1800,),
            ).fetchone()[0]:
                raise ValueError("仍有音频请求未结算，请等生成结束后校准钱包")
            call_id = db.execute(
                "SELECT COALESCE(max(id),0) FROM speech_calls"
            ).fetchone()[0]
            expense_id = db.execute(
                "SELECT COALESCE(max(id),0) FROM expenses"
            ).fetchone()[0]
            saved = {
                "amount": amount,
                "call_id": call_id,
                "expense_id": expense_id,
                "at": time.time(),
            }
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('wallet',?)",
                (json.dumps(saved),),
            )

    def add_expense(self, event_id, category, amount, at=None):
        if category not in (
            "voice_slot",
            "speech_resource_purchase",
            "speech_other_fee",
        ):
            raise ValueError("请选择音色槽位、资源包购买或语音其他费用")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 100:
            raise ValueError("无效记录编号")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or not 0 <= amount <= 1e6
        ):
            raise ValueError("请输入有效支出金额")
        at = time.time() if at is None else at
        if (
            isinstance(at, bool)
            or not isinstance(at, (float, int))
            or not math.isfinite(at)
            or not 946684800 <= at <= time.time() + 60
        ):
            raise ValueError("请输入有效的支出日期")
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO expenses(event_id,at,category,amount) VALUES(?,?,?,?)",
                (event_id, at, category, amount),
            )

    def dashboard(self, now=None):
        now = datetime.now(TZ) if now is None else datetime.fromtimestamp(now, TZ)
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        start_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self.connect() as db:
            rows = [
                dict(r) for r in db.execute("SELECT * FROM speech_calls ORDER BY id")
            ]
            expenses = [
                dict(r) for r in db.execute("SELECT * FROM expenses ORDER BY id")
            ]
            services = []
            for service, info in SERVICES.items():
                selected = [r for r in rows if r["service"] == service]
                if service == "seed-tts-2.0" and not selected:
                    continue
                saved = self._setting(db, service)
                newer = [r for r in selected if r["id"] > saved["cutoff"]]
                known_used = sum(float(r[info["unit"]] or 0) for r in newer)
                remaining = (
                    None
                    if saved["remaining"] is None
                    else max(0, saved["remaining"] - known_used)
                )
                expired = bool(saved["expires"] and saved["expires"] <= now.timestamp())
                data = dict(
                    id=service,
                    **info,
                    settings=saved,
                    remaining=remaining,
                    expired=expired,
                    available=0 if expired else remaining,
                    quota_uncertain=sum(r["amount"] is None for r in newer),
                    started_at=min((r["at"] for r in selected), default=None),
                )
                for name, since in (("today", start_today), ("month", start_month)):
                    group = [r for r in selected if since <= r["at"] <= now.timestamp()]
                    data[name] = self._totals(group)
                services.append(data)
            wallet_row = db.execute(
                "SELECT value FROM settings WHERE key='wallet'"
            ).fetchone()
            wallet = {"state": "not_set"}
            if wallet_row:
                anchor = json.loads(wallet_row[0])
                newer = [r for r in rows if r["id"] > anchor["call_id"]]
                deducted = sum(float(r["amount"] or 0) for r in newer)
                deducted += sum(
                    r["amount"] for r in expenses if r["id"] > anchor["expense_id"]
                )
                wallet = {
                    "state": "estimated",
                    "amount_cny": anchor["amount"] - deducted,
                    "at": anchor["at"],
                    "pending": sum(r["amount"] is None for r in newer),
                }
        result = {
            "services": services,
            "wallet": wallet,
            "timezone": "Asia/Shanghai",
            "rows": {},
        }
        for period, since in (("today", start_today), ("month", start_month)):
            groups = {}
            for r in rows:
                if not since <= r["at"] <= now.timestamp():
                    continue
                groups.setdefault((r["service"], r["category"]), []).append(r)
            out = []
            for (service, category), group in groups.items():
                stats = self._totals(group)
                out.append(
                    {
                        "category": category,
                        "model": service,
                        "provider": "volcengine",
                        "currency": "CNY",
                        "calls": stats["calls"],
                        "amount": stats["amount_cny"],
                        "unknown": stats["pending"],
                        "input_other": None,
                        "input_cached": None,
                        "output": None,
                        "speech": stats,
                    }
                )
            for category in (
                "voice_slot",
                "speech_resource_purchase",
                "speech_other_fee",
            ):
                group = [
                    r
                    for r in expenses
                    if r["category"] == category and since <= r["at"] <= now.timestamp()
                ]
                if group:
                    out.append(
                        {
                            "category": category,
                            "model": "火山平台 · 手工账单",
                            "provider": "volcengine",
                            "currency": "CNY",
                            "calls": len(group),
                            "amount": sum(r["amount"] for r in group),
                            "unknown": 0,
                            "input_other": None,
                            "input_cached": None,
                            "output": None,
                            "manual": True,
                        }
                    )
            result["rows"][period] = out
        return result

    @staticmethod
    def _totals(rows):
        return {
            "calls": len(rows),
            "characters": sum(float(r["characters"] or 0) for r in rows),
            "seconds": sum(float(r["seconds"] or 0) for r in rows),
            "estimated_chars": sum(
                r["characters"] is not None and r["char_basis"] != "provider"
                for r in rows
            ),
            "pending": sum(r["amount"] is None for r in rows),
            "amount_cny": sum(float(r["amount"] or 0) for r in rows),
            "rejected": sum(r["state"] == "rejected" for r in rows),
            "uncertain": sum(r["state"] in ("pending", "uncertain") for r in rows),
        }
