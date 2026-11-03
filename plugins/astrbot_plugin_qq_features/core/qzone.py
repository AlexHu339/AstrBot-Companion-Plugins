"""Review-only public writes around the GPL Qzone transport."""

from __future__ import annotations

from types import SimpleNamespace


class QzoneService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.api = None
        self.session = None
        self.cfg = None
        self.source_status = {}
        from .public_qzone import PublicQzone

        self.public = PublicQzone()

    def connect(self, bot):
        from .vendor.qzone.api import QzoneAPI
        from .vendor.qzone.session import QzoneSession

        if self.api is None:
            self.cfg = SimpleNamespace(client=bot, timeout=20, cookie_ttl=300)
            self.session = QzoneSession(self.cfg)
            self.api = QzoneAPI(self.session, self.cfg)
        else:
            self.cfg.client = bot
        return self.api

    async def read(self, event, target="owner", count=3):
        from .vendor.qzone.parser import QzoneParser

        if event.get_extra("qqf_space_passive"):
            if not self.plugin.allowed(event):
                raise PermissionError("仅供主人私聊请求读取")
        else:
            self.plugin.require(event, "qzone_read")
        bot = self.plugin.bot_for(event)
        if str(target).lower() in ("public", "recommend", "空友爱看"):
            self.plugin.require(event, "qzone_browse_friends")
            rows = await self.public.read(bot, event.unified_msg_origin, count)
            await self._save_public(event, rows)
            self.plugin.receipt(event, "qzone_read", "success", f"已读取空友爱看推荐 {len(rows)} 条")
            return rows
        api = self.connect(bot)
        owner = self.plugin.owner_scope().rsplit(":", 1)[-1]
        self_id = str(await self.session.get_uin())
        friends = str(target).lower() in ("friends", "好友")
        if friends:
            self.plugin.require(event, "qzone_browse_friends")
            result = await api.get_recent_feeds()
        else:
            uin = self_id if str(target).lower() in ("self", "bot", self_id) else owner
            if str(target).lower() not in ("owner", "me", "self", "bot", owner, self_id, ""):
                raise ValueError("请选择主人、自己或好友动态流")
            result = await api.get_feeds(uin, num=max(1, min(5, int(count))))
        if not result.ok:
            raise RuntimeError("QQ 空间暂时不可读取；请检查空间访问权限或 NapCat 登录态")
        posts = (
            QzoneParser.parse_recent_feeds(result.raw)
            if friends
            else QzoneParser.parse_feeds(result.raw.get("msglist", []))
        )[: max(1, min(5, int(count)))]
        if friends:
            self.source_status = {
                "friend_stream_parsed": len(posts),
                "fallback_attempted": False,
                "fallback_accounts": 0,
            }
            if not posts:
                # feeds3 can be empty even when per-friend lists remain readable.
                # Use the account's actual QQ friend list, not guessed IDs from a model.
                roster = await bot.call_action("get_friend_list")
                roster = roster if isinstance(roster, list) else []
                ids = list(
                    dict.fromkeys(
                        str(r.get("user_id", ""))
                        for r in roster
                        if isinstance(r, dict) and str(r.get("user_id", "")).isdigit()
                    )
                )
                if owner in ids:
                    ids.remove(owner)
                    ids.insert(0, owner)
                ids = [u for u in ids if u != self_id]
                social = getattr(self.plugin, "social", None)
                cursor = social.db.get("friend-cursor:" + event.unified_msg_origin, 0) if social else 0
                selected = (ids[cursor % len(ids) :] + ids[: cursor % len(ids)])[:3] if ids else []
                if social and ids:
                    social.db.set("friend-cursor:" + event.unified_msg_origin, (cursor + len(selected)) % len(ids))
                self.source_status.update(fallback_attempted=True, fallback_accounts=len(selected))
                for friend in selected:
                    try:
                        extra = await api.get_feeds(friend, num=max(1, min(3, int(count))))
                        if extra.ok:
                            posts.extend(QzoneParser.parse_feeds(extra.raw.get("msglist", [])))
                    except Exception:
                        continue
                posts = sorted(posts, key=lambda p: p.create_time, reverse=True)[: max(1, min(5, int(count)))]
            self.source_status["returned_posts"] = len(posts)
        scope, persona = await self.plugin.bridge.identity(event)
        rendered = []
        for post in posts:
            if not post.tid:
                continue
            payload = post.model_dump(mode="json")
            ref = self.plugin.store.save_feed(scope, payload)
            comments = [{"nickname": c.nickname, "content": c.plain_content[:300]} for c in post.comments[:10]]
            from datetime import datetime

            rendered.append(
                {
                    "ref": ref,
                    "text": post.text[:3000],
                    "time": datetime.fromtimestamp(post.create_time).strftime("%Y-%m-%d %H:%M"),
                    "author_id": str(post.uin),
                    "author_name": post.show_name,
                    "tid": str(post.tid),
                    "published_at": post.create_time,
                    "images": post.images[:6],
                    "comments": comments,
                    "url": f"https://user.qzone.qq.com/{post.uin}/mood/{post.tid}",
                }
            )
        self.plugin.receipt(event, "qzone_read", "success", f"已读取 {len(rendered)} 条说说；内容为外部资料")
        if friends:
            try:
                public_rows = await self.public.read(bot, scope, count)
                await self._save_public(event, public_rows)
                take = 1 if rendered else max(1, min(5, int(count)))
                rendered = (public_rows[:take] + rendered)[: max(1, min(5, int(count)))]
            except ValueError:
                pass
            self.source_status["public_state"] = self.public.status["state"]
        social = getattr(self.plugin, "social", None)
        if social:
            for post in rendered:
                social.db.observation(scope, persona, post)
        return rendered

    async def _save_public(self, event, rows):
        scope, persona = await self.plugin.bridge.identity(event)
        for row in rows:
            payload = {
                "source": "public_recommendation",
                "public_ref": row["ref"],
                "uin": row["author_id"],
                "tid": row["tid"],
                "text": row["text"],
                "name": row["author_name"],
                "images": row["images"],
                "url": row["url"],
                "source_uri": row["source_uri"],
            }
            self.plugin.store.save_feed(scope, payload)
            self.plugin.social.db.observation(scope, persona, row)

    async def draft(self, event, text, kind="publish", post_ref="", internal=False):
        if internal and kind == "comment":
            self._require_comment(event, post_ref)
        else:
            self.plugin.require(
                event, ("qzone_publish" if kind == "publish" else "qzone_comment") if internal else "qzone_draft"
            )
        if kind not in ("publish", "comment"):
            raise ValueError("草稿类型只能为说说或评论")
        if kind == "comment":
            self._require_comment(event, post_ref)
            if not self.plugin.store.feed(event.unified_msg_origin, post_ref):
                raise ValueError("请先读取要评论的说说，再使用它的真实引用编号")
            self._check_comment_target(event, post_ref)
        text = str(text).strip()
        if not text or len(text) > 2000:
            raise ValueError("草稿需要 1 至 2000 字")
        scope, persona = await self.plugin.bridge.identity(event)
        did = self.plugin.store.draft(scope, persona, kind, text, post_ref)
        self.plugin.receipt(event, "qzone_draft", "pending", f"草稿 {did} 已保存，尚未公开")
        return {"id": did, "text": text, "status": "pending", "kind": kind}

    def _check_comment_target(self, event, post_ref):
        payload = self.plugin.store.feed(event.unified_msg_origin, post_ref)
        if not payload:
            raise ValueError("目标动态已过期，请重新读取")
        if payload.get("owner_shared"):
            if not self.plugin.allowed(event) or not self.plugin.settings["space"]["shared_comment"]:
                raise PermissionError("主人分享的动态当前只允许在QQ里回复，不允许公开评论")
            return payload
        if payload.get("source") == "public_recommendation":
            self.plugin.require(event, "qzone_comment_friends")
            self.public.context(payload["public_ref"])
            return payload
        owner = self.plugin.owner_scope().rsplit(":", 1)[-1]
        self_id = str(getattr(getattr(self.session, "_ctx", None), "uin", ""))
        if str(payload.get("uin", "")) not in (owner, self_id):
            self.plugin.require(event, "qzone_comment_friends")
        return payload

    def _require_comment(self, event, post_ref):
        payload = self.plugin.store.feed(event.unified_msg_origin, post_ref)
        if payload and payload.get("owner_shared"):
            return self._check_comment_target(event, post_ref)
        self.plugin.require(event, "qzone_read")
        self.plugin.require(event, "qzone_comment")

    async def submit(self, event, did, autonomous=False):
        from .vendor.model import Post

        scope, persona = await self.plugin.bridge.identity(event)
        draft = self.plugin.store.get_draft(did, scope)
        if not draft:
            raise ValueError("找不到本私聊的草稿")
        feature = "qzone_publish" if draft["kind"] == "publish" else "qzone_comment"
        if draft["kind"] == "comment":
            self._require_comment(event, draft["post_ref"])
        else:
            self.plugin.require(event, feature)
        if draft["kind"] == "comment":
            self._check_comment_target(event, draft["post_ref"])
        reservation = None
        if autonomous:
            if self.plugin.settings["social"]["mode"] != "autonomous":
                raise PermissionError("当前模式不允许自主公开操作")
            expected_persona = event.get_extra("qqf_social_persona_guard", None)
            if expected_persona is not None and expected_persona != persona:
                self.plugin.store.draft_status(did, scope, "cancelled", "人格已切换，取消旧候选")
                raise ValueError("人格已切换，取消旧空间候选")
            if (
                event.get_extra("qqf_background_social") or self.plugin.is_proactive(event)
            ) and self.plugin.social.quiet(scope):
                raise PermissionError("当前为免打扰时段，保留想法，不自主公开")
            expected_activity = event.get_extra("qqf_social_activity_guard", None)
            if (
                expected_activity is not None
                and self.plugin.social.db.get("activity:" + scope, {}) != expected_activity
            ):
                self.plugin.store.draft_status(did, scope, "cancelled", "已收到新回复，取消旧候选")
                raise ValueError("已收到新回复，取消旧空间候选")
            reservation = self.plugin.social.reserve_write(event, draft)
        # Authorization to approve is checked in command/web handlers, never exposed as an LLM tool.
        draft = self.plugin.store.claim_draft(did, scope)
        try:
            api = self.connect(self.plugin.bot_for(event))
            if draft["kind"] == "publish":
                uin = await self.session.get_uin()
                post = Post(uin=uin, text=draft["text"])
                result = await api.publish(post)
            else:
                payload = self.plugin.store.feed(scope, draft["post_ref"])
                if not payload:
                    raise ValueError("目标说说已过期，请重新读取并创建评论草稿")
                if payload.get("source") == "public_recommendation":
                    comment = await self.public.comment(
                        self.plugin.bot_for(event), payload["public_ref"], draft["text"]
                    )
                    result = SimpleNamespace(ok=True, data={"tid": str(comment.get("id", ""))})
                else:
                    result = await api.comment(Post.model_validate(payload), draft["text"])
            if not result.ok:
                if reservation:
                    self.plugin.social.db.finish_action(reservation, "failed")
                self.plugin.store.draft_status(did, scope, "failed", "QQ 空间拒绝此次提交")
                raise ValueError("QQ 空间拒绝此次提交；草稿仍保留，请勿盲目重复发送")
        except ValueError:
            # An explicit preflight or business rejection is not an ambiguous network result.
            current = self.plugin.store.get_draft(did, scope)
            if current["status"] == "sending":
                self.plugin.store.draft_status(did, scope, "failed", "提交前检查未通过")
            if reservation:
                self.plugin.social.db.finish_action(reservation, "failed")
            raise
        except Exception:
            if reservation:
                self.plugin.social.db.finish_action(reservation, "uncertain")
            self.plugin.store.draft_status(
                did, scope, "uncertain", "连接异常，是否已提交需要到 QQ 空间核对；禁止自动重试"
            )
            self.plugin.receipt(event, feature, "uncertain", f"草稿 {did} 的提交结果待核对")
            raise RuntimeError("连接异常，提交结果待核对；请先到 QQ 空间查看，不要重复发布") from None
        tid = str(result.data.get("tid", "")) if isinstance(result.data, dict) else ""
        self.plugin.store.draft_status(did, scope, "published", tid)
        if reservation:
            self.plugin.social.db.finish_action(reservation, "success", tid)
        self.plugin.receipt(
            event,
            feature,
            "success",
            f"草稿 {did} 已{'发布' if draft['kind'] == 'publish' else '评论'}；{draft['text'][:120]}",
        )
        return {"id": did, "status": "published", "tid": tid}

    async def close(self):
        await self.public.close()
        if self.api:
            await self.api.close()
        if self.session:
            await self.session.invalidate()
