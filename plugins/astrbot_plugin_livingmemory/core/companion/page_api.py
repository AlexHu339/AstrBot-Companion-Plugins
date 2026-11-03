"""Authenticated AstrBot Plugin View endpoints for companion observability."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import jwt
from astrbot.api.web import request as web_request
from quart import request


class CompanionPageApi:
    """Register routes through the same authenticated host bridge as LivingMemory."""

    def __init__(self, plugin):
        self.plugin = plugin

    def register(self):
        for route, handler, methods in (
            ("status", self.status, ["GET"]),
            ("balance", self.balance, ["POST"]),
            ("balance-reference", self.balance_reference, ["POST"]),
            ("speech-settings", self.speech_settings, ["POST"]),
            ("settings", self.settings, ["POST"]),
            ("consolidation-settings", self.consolidation_settings, ["POST"]),
            ("consolidation-run", self.consolidation_run, ["POST"]),
            ("rhythm", self.rhythm, ["POST"]),
            ("view-entry", self.view_entry, ["POST"]),
            ("emotion-status", self.emotion_status, ["POST"]),
            ("emotion-settings", self.emotion_settings, ["POST"]),
            ("emotion-reset", self.emotion_reset, ["POST"]),
        ):
            self.plugin.context.register_web_api(
                f"/astrbot_plugin_livingmemory/companion/{route}",
                handler,
                methods,
                "Companion memory dashboard",
            )

    async def status(self):
        """Return bounded accounting, memory and storage measurements."""
        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        return {"status": "ok", "data": await self.plugin.companion.dashboard()}

    async def consolidation_settings(self):
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"enabled"} or type(payload['enabled']) is not bool:
            return {"status":"error", "message":"请提交整理开关"}
        self.plugin.config_manager.apply_updates({'memory_consolidation.enabled':payload['enabled']})
        return {'status':'ok', 'data':{'enabled':payload['enabled']}}

    async def consolidation_run(self):
        manager = self.plugin.initializer.consolidation_manager
        if manager is None:
            return {'status':'error','message':'记忆组件尚未就绪'}
        return {'status':'ok','data':await manager.run_consolidation(force=True)}

    async def balance(self):
        """Refresh the shared official balance, without any chat completion call."""
        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        return {"status": "ok", "data": await self.plugin.companion.balance()}

    async def balance_reference(self):
        """Save a user-supplied MiMo balance, explicitly as a local reference."""
        import asyncio

        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"platform", "amount_cny"}:
            return {"status": "error", "message": "请输入 MiMo 官网显示的人民币余额"}
        try:
            await asyncio.to_thread(
                self.plugin.companion.store.set_balance_reference,
                payload["platform"], payload["amount_cny"],
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", "message": "已校准；后续为本机估算余额，非平台实时余额"}

    async def view_entry(self):
        """Open either local view inside the existing sandboxed plugin frame.

        Returns:
            A host-compatible asset URL scoped to this plugin and one view.
        """
        from astrbot.dashboard.services.plugin_page_service import (
            PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS,
            PLUGIN_PAGE_ASSET_TOKEN_TYPE,
            PluginPageService,
        )

        payload = await request.get_json(silent=True) or {}
        view = payload.get("view") if isinstance(payload, dict) else None
        if view not in ("dashboard", "companion"):
            return {"status": "error", "message": "未知的记忆页面"}
        username = web_request.username
        secret = self.plugin.context.get_config().get("dashboard", {}).get("jwt_secret")
        if not username or not isinstance(secret, str) or not secret.strip():
            return {"status": "error", "message": "请重新登录 AstrBot 后打开此页"}
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "username": username,
                "token_type": PLUGIN_PAGE_ASSET_TOKEN_TYPE,
                "purpose": "page_session",
                "plugin_name": "astrbot_plugin_livingmemory",
                "page_name": view,
                "locale": "zh-CN",
                "iat": now,
                "exp": now + timedelta(seconds=PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS),
            },
            secret,
            algorithm="HS256",
        )
        url = PluginPageService.build_plugin_page_view_content_path(
            "astrbot_plugin_livingmemory", view, token
        )
        if view == "dashboard":
            url += "#memory"
        return {"status": "ok", "data": {"url": url}}

    async def speech_settings(self):
        """Calibrate quotas or enter invoice items without accepting credentials."""
        import asyncio

        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True)
        meter = self.plugin.companion.speech
        try:
            if not isinstance(payload, dict):
                raise ValueError("请输入有效设置")
            if set(payload) == {"service", "settings"}:
                await asyncio.to_thread(meter.calibrate, payload["service"], payload["settings"])
            elif set(payload) == {"wallet_cny"}:
                await asyncio.to_thread(meter.set_wallet, payload["wallet_cny"])
            elif set(payload) == {"event_id", "category", "amount", "at"}:
                await asyncio.to_thread(meter.add_expense, payload["event_id"], payload["category"], payload["amount"], payload["at"])
            else:
                raise ValueError("未知的语音统计设置")
        except (ValueError, TypeError, KeyError) as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", "message": "已保存；余额和余量为本机推算，非平台实时查询"}

    async def settings(self):
        """Persist only validated local budgets; never accept credentials."""
        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True) or {}
        try:
            budget = float(payload["daily_budget_cny"])
            if not math.isfinite(budget) or not 0 <= budget <= 10000:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return {
                "status": "error",
                "message": "请输入 0 至 10000 的日预算；0 表示仅统计",
            }
        self.plugin.companion.store.set_setting("daily_budget_cny", budget)
        return {"status": "ok", "data": {"daily_budget_cny": budget}}

    async def rhythm(self):
        """Validate and persist independent chat-rhythm controls."""
        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        from .rhythm import validate_settings

        payload = await request.get_json(silent=True)
        try:
            candidate = validate_settings(
                payload, self.plugin.companion.rhythm.settings
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        self.plugin.config_manager.apply_updates(
            {"chat_rhythm." + key: value for key, value in candidate.items()}
        )
        return {"status": "ok", "data": self.plugin.companion.rhythm.dashboard()}

    async def emotion_status(self):
        """Return affect for a known private namespace without generating text."""
        import asyncio

        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or set(payload) - {"scope", "persona"}:
            return {"status": "error", "message": "请选择已有的私聊与人格"}
        try:
            data = await asyncio.to_thread(
                self.plugin.companion.emotion.dashboard,
                payload.get("scope"),
                payload.get("persona"),
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", "data": data}

    async def emotion_settings(self):
        """Persist validated affect controls independently of persona and rhythm."""
        from .emotion import validate_settings

        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True)
        try:
            settings = validate_settings(
                payload, self.plugin.companion.emotion.settings
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        self.plugin.config_manager.apply_updates(
            {"emotion." + key: value for key, value in settings.items()}
        )
        return {"status": "ok", "data": {"settings": settings}}

    async def emotion_reset(self):
        """Clear the selected affect history and invalidate pending judgments."""
        import asyncio

        if self.plugin.companion is None:
            return {"status": "error", "message": "陪伴增强尚未启用"}
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or set(payload) != {"scope", "persona"}:
            return {"status": "error", "message": "请选择要重置的私聊与人格"}
        emotion = self.plugin.companion.emotion
        if payload not in emotion.namespaces():
            return {"status": "error", "message": "请选择已有的私聊与人格"}
        await asyncio.to_thread(emotion.reset, payload["scope"], payload["persona"])
        return {
            "status": "ok",
            "data": await asyncio.to_thread(
                emotion.dashboard, payload["scope"], payload["persona"]
            ),
        }
