"""Low-frequency persona-driven profile changes using real QQ APIs."""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
import re
import time
import uuid
from pathlib import Path

from .social_store import midnight, similar_public_text

STATUS = {
    "在线": (10, 0),
    "离开": (30, 0),
    "隐身": (40, 0),
    "忙碌": (50, 0),
    "请勿打扰": (70, 0),
    "Q我吧": (60, 0),
    "爱你": (10, 2006),
    "摸鱼中": (10, 1300),
    "元气满满": (10, 1058),
    "emo中": (10, 1401),
    "想静静": (10, 1061),
    "学习中": (10, 1018),
    "搬砖中": (10, 2023),
    "睡觉中": (10, 1016),
    "悠哉哉": (10, 1059),
    "无聊中": (10, 1060),
    "我没事": (10, 1052),
    "听歌中": (10, 1028),
    "我想开了": (10, 2013),
    "去旅行": (10, 2015),
}
ALIASES = {"online": "在线", "away": "离开", "invisible": "隐身", "busy": "忙碌", "dnd": "请勿打扰"}
CATEGORIES = {
    "开心": "happy",
    "难过": "sad",
    "心疼": "sad",
    "委屈": "sad",
    "担心": "confused",
    "害羞": "shy",
    "喜欢": "like",
    "生气": "angry",
    "撒娇": "meow",
}


def profile_changes(value):
    """Accept a legacy single action or a small explicit set of distinct fields."""
    actions = value.get("changes") if isinstance(value, dict) and "changes" in value else [value]
    if not isinstance(actions, list) or len(actions) > 4:
        raise ValueError("一次资料变化最多包含四项")
    result, used = [], set()
    for action in actions:
        if not isinstance(action, dict) or action.get("field") not in ("nickname", "avatar", "signature", "status"):
            raise ValueError("资料变化项目不正确")
        if action["field"] in used or not isinstance(action.get("value"), str):
            raise ValueError("同一项资料每次只修改一次，内容须为文字")
        used.add(action["field"])
        result.append({"field": action["field"], "value": action["value"]})
    return result


def static_avatar(data):
    from PIL import Image, ImageOps

    if len(data) > 5 * 1024 * 1024:
        raise ValueError("头像源图片不能超过5MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("头像源图片尺寸过大")
            image.seek(min(getattr(image, "n_frames", 1) // 2, 60))
            frame = ImageOps.exif_transpose(image.copy()).convert("RGBA")
            frame = ImageOps.contain(frame, (512, 512))
            canvas = Image.new("RGBA", (512, 512), (255, 255, 255, 255))
            canvas.alpha_composite(frame, ((512 - frame.width) // 2, (512 - frame.height) // 2))
            result = io.BytesIO()
            canvas.convert("RGB").save(result, "JPEG", quality=92)
            return result.getvalue()
    except (OSError, EOFError):
        raise ValueError("这张图片无法转换为静态头像") from None


class ProfileLife:
    def __init__(self, plugin):
        self.plugin = plugin
        with plugin.store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS profile_life(id TEXT PRIMARY KEY,scope TEXT,persona TEXT,field TEXT,value TEXT,digest TEXT,at REAL,status TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS profile_life_scope_time ON profile_life(scope,at)")
            db.execute("CREATE TABLE IF NOT EXISTS profile_batch_actions(action_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL)")
            db.execute("UPDATE profile_life SET status='uncertain' WHERE status='reserved'")

    def recent(self, scope, limit=8):
        with self.plugin.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT field,value,at,status FROM profile_life WHERE scope=? AND status IN ('reserved','success','uncertain') ORDER BY at DESC LIMIT ?",
                    (scope, limit),
                )
            ]

    @staticmethod
    def availability(db, scope, conf):
        now = time.time()
        count = db.execute(
            "SELECT COUNT(*) FROM profile_life WHERE scope=? AND status IN ('reserved','success','uncertain') AND at>=?",
            (scope, midnight()),
        ).fetchone()[0]
        automatic = dict(db.execute(
            "SELECT field,MAX(at) FROM profile_life WHERE scope=? AND status IN ('reserved','success','uncertain') GROUP BY field", (scope,)
        ).fetchall())
        manual = dict(db.execute(
            "SELECT kind,MAX(at) FROM receipts WHERE scope=? AND kind IN ('nickname','avatar','signature','status') AND status='success' GROUP BY kind", (scope,)
        ).fetchall())
        fields = [key for key in ("nickname", "avatar", "signature", "status") if conf[key]
                  and now - max(float(automatic.get(key) or 0), float(manual.get(key) or 0)) >= conf["cooldown_hours"] * 3600]
        return fields, max(0, conf["daily_limit"] - count)

    def ready(self, event, field=None):
        conf = self.plugin.settings["profile_auto"]
        if not conf["enabled"] or conf["daily_limit"] <= 0 or not self.plugin.allowed(event):
            return False
        if (self.plugin.is_proactive(event) or event.get_extra("qqf_background_social")) and self.plugin.social.quiet(
            event.unified_msg_origin
        ):
            return False
        with self.plugin.store.connect() as db:
            fields, remaining = self.availability(db, event.unified_msg_origin, conf)
        return remaining > 0 and (field in fields if field else bool(fields))

    def avatar_pool(self, event):
        meta = self.plugin.context.get_registered_star("meme_manager")
        manager = getattr(meta, "star_cls", None) if meta and getattr(meta, "activated", False) else None
        if not manager or not hasattr(manager, "_resolve_runtime_pack_context"):
            return {}
        try:
            context = manager._resolve_runtime_pack_context(event=event)
            roots = [Path(p) / "memes" for p in (context.get("pack_dirs") or {}).values()]
            if not roots and context.get("memes_dir"):
                roots = [Path(context["memes_dir"])]
            pool = {}
            for root in roots:
                root = root.resolve()
                if not root.is_dir():
                    continue
                for folder in root.iterdir():
                    if folder.name.lower() in ("needs_review", "pending", "_pending", "temp", "backup"):
                        continue
                    if not folder.is_dir() or not re.fullmatch(r"[\w\-]{1,40}", folder.name):
                        continue
                    for file in folder.iterdir():
                        if (
                            file.is_file()
                            and file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
                            and file.resolve().is_relative_to(root)
                            and file.stat().st_size <= 5 * 1024 * 1024
                        ):
                            pool.setdefault(folder.name, []).append(file)
            return pool
        except Exception:
            return {}

    def offer(self, event, persona, background=False):
        if event.get_extra("qqf_profile_considered"):
            return event.get_extra("qqf_profile_candidate", {})
        event.set_extra("qqf_profile_considered", True)
        if not self.ready(event) or random.random() >= self.plugin.settings["profile_auto"]["probability"]:
            return {}
        if event.get_extra("meme_manager_resolved_persona_id") is None:
            event.set_extra("persona_id", persona)
        pool = self.avatar_pool(event)
        conf = self.plugin.settings["profile_auto"]
        with self.plugin.store.connect() as db:
            eligible, remaining = self.availability(db, event.unified_msg_origin, conf)
        fields = [k for k in eligible if k != "avatar" or pool]
        if not fields:
            return {}
        maximum = int(min(conf["max_changes_per_opportunity"], len(fields), remaining))
        maximum = random.randint(0, maximum) if conf["randomize_change_count"] else maximum
        if maximum == 0:
            return {}
        candidate = {
            "id": uuid.uuid4().hex,
            "max_changes": maximum,
            "persona": persona,
            "fields": fields,
            "categories": list(pool),
            "statuses": list(STATUS),
            "background": background,
            "activity": self.plugin.social.db.get("activity:" + event.unified_msg_origin, {}),
        }
        event.set_extra("qqf_profile_candidate", candidate)
        return candidate

    def guidance(self, event, persona):
        candidate = self.offer(event, persona)
        if not candidate:
            return ""
        preferred = ["happy", "sad", "like", "shy", "meow", "angry", "confused", "sigh"]
        categories = list(
            dict.fromkeys([v for v in preferred if v in candidate["categories"]] + candidate["categories"])
        )[:8]
        states = [
            v
            for v in ["爱你", "摸鱼中", "emo中", "元气满满", "想静静", "在线", "忙碌", "睡觉中"]
            if v in candidate["statuses"]
        ]
        recent = [r["value"][:20] for r in self.recent(event.unified_msg_origin, 3)]
        return (
            f"【主动资料机会】本次最多改{candidate['max_changes']}项，可少改或不改；按当前人格、情绪、目的和语境选择有意义的项目，不凑数，不套例句、不公开隐私或猜测、不把没回当不爱。"
            "范围："
            + "/".join(candidate["fields"])
            + "；头像分类："
            + "/".join(categories)
            + "；状态："
            + "/".join(states)
            + '。用qq_profile，每项各调用一次；主动消息也可附[qqprofile]{"changes":[{"field":"nickname/avatar/signature/status","value":"文字或分类"}]}[/qqprofile]。同一项不重复，成功才说已改。避开近期：'
            + "/".join(recent)
        )

    async def perform(self, event, field, value):
        # One plan may contain several fields; commit API calls in order so the
        # per-event authorization token cannot race between parallel tool calls.
        async with self.plugin.lock(event.unified_msg_origin, "profile-plan"):
            return await self._perform(event, field, value)

    async def perform_many(self, event, plan):
        actions = profile_changes(plan)
        candidate = event.get_extra("qqf_profile_candidate", {})
        if len(actions) > min(candidate.get("max_changes", 0), self.plugin.settings["profile_auto"]["max_changes_per_opportunity"]):
            raise ValueError("超过本轮允许的资料并发上限")
        results = []
        for action in actions:
            try:
                result = await self.perform(event, action["field"], action["value"])
                results.append({"field": action["field"], "success": True, "result": result})
            except Exception as exc:
                self.plugin.action_error(event, "auto_" + action["field"], exc)
                results.append({"field": action["field"], "success": False})
        return results

    async def _perform(self, event, field, value):
        candidate = event.get_extra("qqf_profile_candidate", {})
        if field not in candidate.get("fields", []) or event.get_extra("qqf_profile_done"):
            raise PermissionError("本轮没有这个主动资料修改机会")
        _, persona = await self.plugin.bridge.identity(event)
        if (
            persona != candidate["persona"]
            or self.plugin.social.db.get("activity:" + event.unified_msg_origin, {}) != candidate["activity"]
        ):
            raise ValueError("人格或对话已变化，取消旧的资料想法")
        if not self.ready(event, field) or not self.plugin.settings["profile_auto"].get(field):
            raise ValueError("主动资料修改当前处于关闭、冷却或额度已满状态")
        avatar = None
        value = str(value).strip()
        if field == "avatar":
            category = CATEGORIES.get(value, value)
            files = list(self.avatar_pool(event).get(category, []))
            random.shuffle(files)
            with self.plugin.store.connect() as db:
                used = {
                    r[0]
                    for r in db.execute(
                        "SELECT digest FROM profile_life WHERE scope=? AND field='avatar' AND at>? AND status IN ('reserved','success','uncertain')",
                        (event.unified_msg_origin, time.time() - 14 * 86400),
                    )
                }
            for file in files[:80]:
                try:
                    data = static_avatar(file.read_bytes())
                except (ValueError, OSError):
                    continue
                if hashlib.sha256(data).hexdigest() not in used:
                    avatar = data
                    break
            if avatar is None:
                raise ValueError("这个表情分类没有可用且近期未重复的头像")
            value = category
        elif field == "status":
            value = ALIASES.get(value, value)
            if value not in STATUS:
                raise ValueError("请从实际支持的QQ在线状态中选择")
        elif not 1 <= len(value) <= (36 if field == "nickname" else 80):
            raise ValueError("昵称或签名的长度不合适")
        digest = hashlib.sha256(avatar or value.encode()).hexdigest()
        aid = uuid.uuid4().hex
        conf = self.plugin.settings["profile_auto"]
        with self.plugin.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM profile_life WHERE scope=? AND at>? AND status IN ('reserved','success','uncertain')",
                (event.unified_msg_origin, time.time() - 14 * 86400),
            ).fetchall()
            available, remaining = self.availability(db, event.unified_msg_origin, conf)
            batch_rows = db.execute(
                "SELECT p.field FROM profile_life p JOIN profile_batch_actions b ON p.id=b.action_id "
                "WHERE b.batch_id=? AND p.status IN ('reserved','success','uncertain')", (candidate["id"],)
            ).fetchall()
            if remaining <= 0 or field not in available:
                raise ValueError("主动资料变化仍在冷却或已达今日次数上限")
            live_maximum = min(candidate["max_changes"], conf["max_changes_per_opportunity"])
            if len(batch_rows) >= live_maximum or any(r["field"] == field for r in batch_rows):
                raise ValueError("本轮资料并发额度已满，或已修改过同一项")
            repeated = any(
                r["field"] == field
                and (
                    r["digest"] == digest
                    or field in ("nickname", "signature")
                    and similar_public_text(value, r["value"])
                )
                for r in rows
            )
            if field == "status":
                statuses = [r for r in rows if r["field"] == "status"]
                repeated = bool(statuses and max(statuses, key=lambda r: r["at"])["digest"] == digest)
            if repeated:
                raise ValueError("这项资料近期已使用过，不重复改回相同内容")
            db.execute(
                "INSERT INTO profile_life VALUES(?,?,?,?,?,?,?,?)",
                (aid, event.unified_msg_origin, persona, field, value, digest, time.time(), "reserved"),
            )
            db.execute("INSERT INTO profile_batch_actions VALUES(?,?)", (aid, candidate["id"]))
        event.set_extra("qqf_auto_profile_authorized", {"field": field, "at": time.time()})
        try:
            result = await self.plugin.profile(event, field, value, avatar_bytes=avatar, automatic=True)
        except asyncio.CancelledError:
            status = "uncertain"
            raise
        except (ValueError, PermissionError):
            status = "failed"
            raise
        except Exception:
            status = "uncertain"
            raise
        else:
            status = "success"
            with self.plugin.store.connect() as db:
                used = db.execute(
                    "SELECT COUNT(*) FROM profile_life p JOIN profile_batch_actions b ON p.id=b.action_id "
                    "WHERE b.batch_id=? AND p.status IN ('reserved','success','uncertain')", (candidate["id"],)
                ).fetchone()[0]
            event.set_extra("qqf_profile_done", used >= live_maximum)
            return result
        finally:
            event.set_extra("qqf_auto_profile_authorized", None)
            with self.plugin.store.connect() as db:
                db.execute("UPDATE profile_life SET status=? WHERE id=?", (status, aid))
