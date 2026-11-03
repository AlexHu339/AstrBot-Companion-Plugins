"""Host-authenticated management page; no credentials or autonomous approvals."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from quart import request

from .settings import FEATURES, validate_patch


class PageAPI:
    def __init__(self, plugin):
        self.plugin = plugin

    def register(self):
        for route, handler, methods in (
            ("status", self.status, ["GET"]),
            ("settings", self.settings, ["POST"]),
            ("draft", self.draft, ["POST"]),
            ("edit-draft", self.edit_draft, ["POST"]),
            ("approve", self.approve, ["POST"]),
            ("reject", self.reject, ["POST"]),
            ("feed", self.feed, ["POST"]),
            ("profile", self.profile, ["POST"]),
            ("social-preview", self.social_preview, ["POST"]),
            ("public-source-check", self.public_source_check, ["POST"]),
            ("inspect-share", self.inspect_share, ["POST"]),
        ):
            self.plugin.context.register_web_api(
                "/astrbot_plugin_qq_features/" + route, handler, methods, "qq功能拓展管理"
            )

    async def payload(self):
        value = await request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("请求需要有效的 JSON 对象")
        return value

    async def run(self, callback):
        try:
            return {"status": "ok", "data": await callback()}
        except (PermissionError, ValueError) as exc:
            return {"status": "error", "message": str(exc)[:160]}
        except Exception:
            return {"status": "error", "message": "接口暂时不可用，未确认操作成功；请查看动作记录"}

    def event(self):
        plugin = self.plugin
        scope = plugin.owner_scope()
        if not scope:
            raise PermissionError("请先在插件配置中指定唯一的主人私聊会话")
        extras = {"qqf_manual_profile": True}
        event = SimpleNamespace(
            unified_msg_origin=scope,
            session_id=scope,
            plugins_name=None,
            bot=plugin.bot_for(),
            get_sender_id=lambda: scope.rsplit(":", 1)[-1],
            get_group_id=lambda: "",
            get_platform_name=lambda: "aiocqhttp",
            get_message_str=lambda: "主人在管理页明确要求本次操作",
            get_extra=lambda key, default=None: extras.get(key, default),
            set_extra=lambda key, value: extras.__setitem__(key, value),
            get_messages=lambda: [],
            message_obj=SimpleNamespace(message_id="management"),
        )
        return event

    async def status(self):
        p = self.plugin
        owner = p.owner_scope()
        connection = p.bot_for()
        proactive = p.context.get_registered_star("astrbot_plugin_proactive_chat")
        avatar_event = self.event() if owner else None
        if avatar_event:
            _, persona = await p.bridge.identity(avatar_event)
            avatar_event.set_extra("persona_id", persona)
        return {
            "status": "ok",
            "data": {
                "name": "qq功能拓展",
                "version": "2.3.1",
                "workspace_version": 2,
                "settings": p.settings,
                "features": [
                    {"key": key, "title": title, "description": description}
                    for key, (title, description) in FEATURES.items()
                ],
                "drafts": p.store.drafts(owner) if owner else [],
                "receipts": p.store.recent_receipts(owner, 30) if owner else [],
                "integration": {
                    "owner_ready": bool(owner),
                    "napcat_available": connection is not None,
                    "memory": p.bridge.available(),
                    "proactive": bool(proactive and proactive.activated),
                    "native_segmentation_preserved": True,
                },
                "storage": str(p.store.root),
                "meme_reaction": p.meme_reaction.dashboard(),
                "social": p.social.dashboard(),
                "source_status": p.qzone.source_status,
                "profile_life": {
                    "recent": p.profile_life.recent(owner),
                    "avatar_categories": list(p.profile_life.avatar_pool(avatar_event)) if avatar_event else [],
                },
                "cost_note": "聊天互动、接话和当轮资料变化复用当前模型；收表情互动优先融入当轮，识别较慢时的短句补发单列为“表情收藏互动”。复用表情包管理器已有识图描述，不再识图一次。后台空间自主决策、资料自主决策及空间看图分别记录在LivingMemory账本。图越多实际输入用量可能越高。定时检查、抽取GIF静态帧和QQ接口操作本身不消耗聊天token。未定价或缺用量的调用保留待核对状态，不算作免费。",
            },
        }

    async def settings(self):
        async def action():
            patch = validate_patch(await self.payload())
            limits = {**self.plugin.settings["limits"], **patch.get("limits", {})}
            if limits["recall_delay_seconds"] >= limits["recall_window_seconds"]:
                raise ValueError("自动撤回的等待时间必须短于允许撤回时限，请分别设置")
            social = {**self.plugin.settings["social"], **patch.get("social", {})}
            for kind in ("unanswered", "closure"):
                if social[kind + "_min_minutes"] > social[kind + "_max_minutes"]:
                    raise ValueError("随机等待的最少时间不能大于最多时间")
            for group, values in patch.items():
                section = dict(self.plugin.config.get(group, {}) or {})
                section.update(values)
                self.plugin.config[group] = section
            self.plugin.config.save_config()
            return self.plugin.settings

        return await self.run(action)

    async def draft(self):
        async def action():
            payload = await self.payload()
            return await self.plugin.qzone.draft(
                self.event(), payload.get("text", ""), payload.get("kind", "publish"), payload.get("post_ref", "")
            )

        return await self.run(action)

    async def edit_draft(self):
        async def action():
            payload = await self.payload()
            event = self.event()
            self.plugin.require(event, "qzone_draft")
            text = str(payload.get("text", "")).strip()
            if not 1 <= len(text) <= 2000:
                raise ValueError("草稿需要 1 至 2000 字")
            self.plugin.store.edit_draft(str(payload.get("id", "")), event.unified_msg_origin, text)
            return {"saved": True}

        return await self.run(action)

    async def approve(self):
        async def action():
            payload = await self.payload()
            return await self.plugin.qzone.submit(self.event(), str(payload.get("id", "")))

        return await self.run(action)

    async def reject(self):
        async def action():
            payload = await self.payload()
            event = self.event()
            did = str(payload.get("id", ""))
            draft = self.plugin.store.get_draft(did, event.unified_msg_origin)
            if not draft or draft["status"] != "pending":
                raise ValueError("只能驳回待审核草稿")
            self.plugin.store.draft_status(did, event.unified_msg_origin, "rejected", "主人驳回，未发送")
            return {"rejected": True}

        return await self.run(action)

    async def feed(self):
        async def action():
            payload = await self.payload()
            event = self.event()
            event.set_extra("qqf_space_passive", True)
            return await self.plugin.qzone.read(event, str(payload.get("target", "owner")), payload.get("count", 3))

        return await self.run(action)

    async def profile(self):
        async def action():
            payload = await self.payload()
            avatar = None
            if payload.get("field") == "avatar":
                raw = str(payload.get("image", ""))
                if len(raw) > 7 * 1024 * 1024:
                    raise ValueError("头像文件不能超过 5 MB")
                if raw.startswith("data:"):
                    raw = raw.split(",", 1)[-1]
                try:
                    avatar = base64.b64decode(raw, validate=True)
                except Exception:
                    raise ValueError("图片内容无法识别") from None
            return {
                "message": await self.plugin.profile(
                    self.event(), payload.get("field"), payload.get("value", ""), avatar
                )
            }

        return await self.run(action)

    async def social_preview(self):
        async def action():
            event = self.event()
            target = "friends" if self.plugin.feature("qzone_browse_friends") else "owner"
            posts = await self.plugin.qzone.read(event, target, self.plugin.settings["social"]["read_batch_size"])
            plan = await self.plugin.social.decide(event, "preview", posts, dry_run=True) if posts else None
            return {"read_count": len(posts), "plan": plan, "public_actions_executed": False}

        return await self.run(action)

    async def public_source_check(self):
        async def action():
            event = self.event()
            event.set_extra("qqf_space_passive", True)
            self.plugin.require(event, "qzone_browse_friends")
            public = self.plugin.qzone.public
            try:
                capability = await public.capabilities(self.plugin.bot_for(event))
            except ValueError as exc:
                capability = {"rpc_succeeded": False, "message": str(exc)}
            rows = await self.plugin.qzone.read(event, "public", 3)
            return {
                "capability": capability,
                "source_status": public.status,
                "read_count": len(rows),
                "text_count": sum(bool(p["text"]) for p in rows),
                "image_count": sum(bool(p["images"]) for p in rows),
                "author_count": sum(p["author_id"] != "uid:未提供" and p["author_name"] != "空友" for p in rows),
                "timestamp_count": sum(bool(p["published_at"]) for p in rows),
                "current_user_uid_available": bool((public._auth or {}).get("uid")),
                "public_actions_executed": False,
            }

        return await self.run(action)

    async def inspect_share(self):
        async def action():
            event = self.event()
            payload = await self.payload()
            from .space_browser import share_targets

            rows = []
            for uin, tid in share_targets(str(payload.get("url", ""))):
                rows.extend(
                    await self.plugin.space_browser.person_page(event, uin, shared_tid=tid, owner_supplied=True)
                )
            return {
                "read_count": len(rows),
                "authors": sum(bool(r["author_name"]) for r in rows),
                "text_count": sum(bool(r["text"]) for r in rows),
                "image_count": sum(bool(r["images"]) for r in rows),
                "refs": [r["ref"] for r in rows],
                "public_actions_executed": False,
                "model_calls": 0,
            }

        return await self.run(action)
