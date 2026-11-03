"""Independent JSON-RPC compatibility adapter for QQ public recommendations.

Protocol references: SnowLuma SDK public-space documentation and NapCat's
published action definitions. No NapCat implementation is bundled or modified.
Opaque feed transfer data, cookies and cursors are deliberately RAM-only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime
from http.cookies import SimpleCookie
from urllib.parse import urlparse

import aiohttp

from .social_store import CST

ROOT = "https://h5.qzone.qq.com/http2rpc/gotrpc/v1/"
ROUTES = {
    "capabilities": ("trpc.public_qzone.entrance_ctrl.EntranceCtrl", "BatchEntranceWhitelist", "0x9741", "0"),
    "feeds": ("trpc.qzone.feeds_reader.FeedsReader", "GetPublicRecomFeeds", "0x9a38", "0"),
    "detail": ("trpc.qzone.feeds_reader.FeedsReader", "GetFeedDetail", "0x97b1", "1"),
    "comments": ("trpc.qzone.feeds_reader.FeedsReader", "GetComment", "0x9bfd", "1"),
    "comment": ("trpc.qzone.interaction_writer.InteractionWriter", "AddComment", "0x9676", "1"),
}
MAX_BYTES = 8 * 1024 * 1024


class PublicReadError(ValueError):
    pass


class PublicWriteRejected(ValueError):
    pass


class PublicWriteUncertain(RuntimeError):
    pass


def rich_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(rich_text(v) for v in value[:200])
    if isinstance(value, dict):
        for name in ("content", "text", "summary", "title", "desc"):
            if name in value:
                return rich_text(value[name])
    return ""


def image_urls(value):
    result = []

    def visit(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, dict):
            for key, item in list(node.items())[:100]:
                if isinstance(item, str) and ("url" in key.lower() or key.lower() in ("src", "cover")):
                    parsed = urlparse(item)
                    if (
                        parsed.scheme in ("http", "https")
                        and parsed.hostname
                        and any(
                            parsed.hostname == d or parsed.hostname.endswith("." + d)
                            for d in ("qpic.cn", "qq.com", "gtimg.cn", "gtimg.com")
                        )
                    ):
                        result.append(item)
                elif isinstance(item, (list, dict)):
                    visit(item, depth + 1)
        elif isinstance(node, list):
            for item in node[:30]:
                visit(item, depth + 1)

    visit(value)
    return list(dict.fromkeys(result))[:6]


def feed_images(feed):
    """Pictures and video covers; never pass a video URL off as an image."""
    sources = [feed.get(key) for key in ("cell_pic", "cell_photo", "cell_image")]
    media = feed.get("cell_media")
    if isinstance(media, dict):
        sources.append(media.get("album_cover"))
        for item in (media.get("media_items") or [])[:20]:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if any(part in key.lower() for part in ("photo", "pic", "image")) and isinstance(value, (dict, list)):
                    sources.append(value)
            video = item.get("video")
            if isinstance(video, dict):
                sources.append(video.get("cover"))
    return list(dict.fromkeys(image_urls(sources)))[:6]


def media_layout(feed):
    """Field names/types only, so compatibility checks never log feed contents."""

    def shape(value, depth=0):
        if depth > 5:
            return type(value).__name__
        if isinstance(value, dict):
            return {str(k): shape(v, depth + 1) for k, v in list(value.items())[:32]}
        if isinstance(value, list):
            return [shape(v, depth + 1) for v in value[:1]]
        return type(value).__name__

    return {
        k: shape(v) for k, v in feed.items() if any(x in k.lower() for x in ("pic", "photo", "image", "video", "media"))
    }


def feed_identity(feed):
    if not isinstance(feed, dict):
        raise PublicReadError("推荐动态格式不完整")
    common = feed.get("cell_common")
    union = common.get("union_id") if isinstance(common, dict) else None
    if not isinstance(union, dict) or not any(
        isinstance(union.get(k), str) and union[k].strip() for k in ("uf_key", "ugc_id", "feed_id", "serialized_data")
    ):
        raise PublicReadError("推荐动态缺少原始完整身份，不能用普通说说编号代替")
    stable = next(
        (
            {k: union[k]}
            for k in ("uf_key", "ugc_id", "feed_id", "serialized_data")
            if isinstance(union.get(k), str) and union[k].strip()
        ),
        None,
    )
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > MAX_BYTES:
        raise PublicReadError("推荐动态身份过大")
    return hashlib.sha256(encoded.encode()).hexdigest()


def parse_feed(feed, scope):
    identity = feed_identity(feed)
    common = feed["cell_common"]
    if common.get("feed_type") == 3 or feed.get("cell_ad"):
        return None
    cell = feed.get("cell_user_info") or {}
    user = cell.get("user") or {}
    author = str(user.get("uin") or "")
    if not author.isdigit() or int(author) == 0:
        author = "uid:" + str(user.get("uid") or "未提供")
    text = rich_text(feed.get("cell_summary") or {})
    images = feed_images(feed)
    video = feed.get("cell_video")
    if not text and isinstance(video, dict):
        text = rich_text(video.get("title") or video.get("desc") or "")
    timestamp = 0
    for value in (common.get("create_time"), common.get("feed_create_time"), common.get("time"), cell.get("time")):
        try:
            candidate = int(value)
            if candidate > 10**12:
                candidate //= 1000
            if 946684800 < candidate < 4102444800:
                timestamp = candidate
                break
        except (ValueError, TypeError):
            continue
    url = ""
    for value in (
        common.get("share_url"),
        common.get("jump_url"),
        common.get("url"),
        (feed.get("cell_detail") or {}).get("url"),
    ):
        if isinstance(value, str) and urlparse(value).scheme in ("http", "https"):
            host = urlparse(value).hostname or ""
            if host == "qq.com" or host.endswith(".qq.com"):
                url = value
                break
    comments = []
    comment_cell = feed.get("cell_comment") or {}
    for comment in (comment_cell.get("comments") or [])[:10]:
        if isinstance(comment, dict):
            comments.append(
                {
                    "nickname": str((comment.get("user") or {}).get("nick", "")),
                    "content": rich_text(comment.get("content"))[:300],
                }
            )
    ref = hashlib.sha256((scope + "|public|" + identity).encode()).hexdigest()[:16]
    return {
        "ref": ref,
        "text": text[:3000],
        "author_id": author,
        "author_name": str(user.get("nick") or cell.get("nick") or "空友"),
        "tid": "public:" + identity,
        "published_at": timestamp,
        "time": datetime.fromtimestamp(timestamp, CST).strftime("%Y-%m-%d %H:%M") if timestamp else "发布时间未提供",
        "images": images,
        "comments": comments,
        "url": url,
        "source": "public_recommendation",
        "source_uri": "qzone-public:" + identity,
    }


class PublicQzone:
    def __init__(self):
        self._http = None
        self._auth = None
        self._feeds = {}
        self._cursor = None
        self.status = {"implemented": True, "state": "not_checked", "count": 0, "last_error": ""}

    async def auth(self, bot):
        try:
            info = await bot.call_action("get_login_info")
        except Exception:
            raise PublicReadError("NapCat当前登录身份暂时不可读取") from None
        uin = str(info.get("user_id", "")) if isinstance(info, dict) else ""
        if not uin.isdigit() or int(uin) == 0:
            raise PublicReadError("当前QQ账号身份不可用")
        if self._auth and self._auth["uin"] != uin:
            self._auth = None
            self._feeds.clear()
            self._cursor = None
        if self._auth and time.time() - self._auth["at"] < 240:
            return self._auth
        try:
            response = await bot.call_action("get_cookies", domain="qzone.qq.com")
            cookies = {k: v.value for k, v in SimpleCookie(str((response or {}).get("cookies", ""))).items()}
        except Exception:
            raise PublicReadError("NapCat当前空间登录态暂时不可读取") from None
        cookie_uin = str(cookies.get("uin") or cookies.get("p_uin") or "").lstrip("oO")
        if cookie_uin != uin:
            raise PublicReadError("空间登录态与当前QQ账号不一致")
        key = cookies.get("p_skey") or cookies.get("skey") or ""
        if not key:
            raise PublicReadError("当前账号没有可用的空间登录态")
        gtk = 5381
        for char in key:
            gtk = (gtk * 33 + ord(char)) & 0x7FFFFFFF
        cookie_parts = [f"uin=o{uin}", f"p_uin=o{uin}"]
        for name in ("skey", "p_skey"):
            if cookies.get(name):
                cookie_parts.append(name + "=" + cookies[name])
        uid = str(info.get("uid") or "")
        if not uid:
            try:
                detail = await bot.call_action("get_stranger_info", user_id=int(uin))
                uid = str(detail.get("uid") or "") if isinstance(detail, dict) else ""
            except Exception:
                uid = ""
        self._auth = {
            "uin": uin,
            "uid": uid,
            "cookie": "; ".join(cookie_parts),
            "gtk": gtk,
            "at": time.time(),
            "nick": str(info.get("nickname") or ""),
        }
        return self._auth

    async def rpc(self, bot, operation, body):
        if operation not in ROUTES:
            raise ValueError("不支持这个推荐空间操作")
        auth = await self.auth(bot)
        service, method, command, service_type = ROUTES[operation]
        headers = {
            "Cookie": auth["cookie"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://h5.qzone.qq.com",
            "Referer": "https://h5.qzone.qq.com/pc-qzone/index.html",
            "x-web-qua": "V1_HT5_QZ_9.9.32_001_IDC_B",
            "x-oidb": json.dumps(
                {"uint32_command": command, "uint32_service_type": service_type}, separators=(",", ":")
            ),
        }
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=18))
        write = operation == "comment"
        try:
            async with self._http.post(
                ROOT + service + "/" + method,
                params={"g_tk": str(auth["gtk"])},
                json=body,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RuntimeError("http")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise RuntimeError("size")
            value = json.loads(data)
        except Exception:
            if write:
                raise PublicWriteUncertain("推荐空间写入结果未确认，请先核对，不自动重试") from None
            raise PublicReadError("推荐空间请求失败、超时或响应无效") from None
        if not isinstance(value, dict) or type(value.get("retcode")) is not int:
            if write:
                raise PublicWriteUncertain("推荐空间没有返回明确业务回执")
            raise PublicReadError("推荐空间没有返回明确成功响应")
        if value["retcode"] != 0:
            error = PublicWriteRejected if write else PublicReadError
            raise error("推荐空间拒绝请求，业务码 " + str(value["retcode"]))
        if not isinstance(value.get("data"), dict):
            if write:
                raise PublicWriteUncertain("推荐空间业务回执不完整")
            raise PublicReadError("推荐空间业务响应不完整")
        if write and not value["data"].get("comment"):
            raise PublicWriteUncertain("推荐评论缺少业务成功回执，请先核对")
        return value["data"]

    async def capabilities(self, bot):
        auth = await self.auth(bot)
        result = await self.rpc(bot, "capabilities", {"uins": [auth["uin"]], "entrance_types": [1, 15]})
        return {"response_keys": list(result)[:20], "rpc_succeeded": True}

    async def read(self, bot, scope, count=3):
        try:
            auth = await self.auth(bot)
            state = self._cursor
            if state and (state["uin"] != auth["uin"] or time.time() - state["at"] > 600):
                state = None
            body = {"refresh_id": state["refresh_id"] if state else auth["uin"] + "_" + str(int(time.time() * 1000))}
            if state:
                body["attach_info"] = state["attach_info"]
                if state.get("last_union"):
                    body["last_client_feed_union_id"] = copy.deepcopy(state["last_union"])
            result = await self.rpc(bot, "feeds", body)
            entries = result.get("feeds")
            if not isinstance(entries, list) or type(result.get("has_more")) is not bool:
                raise PublicReadError("推荐流分页结构不完整")
            rows = []
            shapes = []
            for entry in entries[:40]:
                feed = entry.get("feed", entry) if isinstance(entry, dict) else None
                if not isinstance(feed, dict):
                    continue
                shapes.append(sorted(feed))
                try:
                    post = parse_feed(feed, scope)
                except PublicReadError:
                    continue
                if not post:
                    continue
                self._feeds[post["ref"]] = {"feed": copy.deepcopy(feed), "uin": auth["uin"], "at": time.time()}
                rows.append(post)
            self._feeds = {k: v for k, v in self._feeds.items() if time.time() - v["at"] < 600}
            while len(self._feeds) > 128:
                self._feeds.pop(next(iter(self._feeds)))
            if result["has_more"]:
                attach = result.get("attach_info")
                if not isinstance(attach, str) or not attach or len(attach) > 65536:
                    raise PublicReadError("推荐流缺少可用的续页信息")
                last_union = None
                for entry in reversed(entries):
                    feed = entry.get("feed", entry) if isinstance(entry, dict) else {}
                    common = feed.get("cell_common") or {}
                    if common.get("feed_type") != 3 and isinstance(common.get("union_id"), dict):
                        last_union = common["union_id"]
                        break
                self._cursor = {
                    "uin": auth["uin"],
                    "at": time.time(),
                    "refresh_id": body["refresh_id"],
                    "attach_info": attach,
                    "last_union": copy.deepcopy(last_union),
                }
            else:
                self._cursor = None
            self.status = {
                "implemented": True,
                "state": "available" if rows else "empty",
                "count": len(rows),
                "raw_items": len(entries),
                "has_more": result["has_more"],
                "cell_shapes": shapes[:2],
                "media_layout": media_layout(next(iter(self._feeds.values()))["feed"]) if self._feeds else {},
                "last_error": "",
            }
            return rows[: max(1, min(5, int(count)))]
        except PublicReadError as exc:
            self.status = {"implemented": True, "state": "unavailable", "count": 0, "last_error": str(exc)}
            raise

    def context(self, ref):
        cached = self._feeds.get(ref)
        if not cached or time.time() - cached["at"] > 600:
            raise ValueError("推荐动态互动上下文已过期，请重新读取该来源")
        if not self._auth or cached["uin"] != self._auth["uin"]:
            raise ValueError("推荐动态所属账号已变更，请重新读取")
        return cached

    async def comment(self, bot, ref, text):
        cached = self.context(ref)
        auth = await self.auth(bot)
        if cached["uin"] != auth["uin"]:
            raise ValueError("推荐动态账号已切换")
        feed = cached["feed"]
        union = copy.deepcopy(feed["cell_common"]["union_id"])
        content = str(text).strip()
        if not content or len(content) > 2000:
            raise ValueError("评论需要1至2000字")
        body = {
            "union_id": union,
            "comment": {
                "user": {"uin": auth["uin"], "nick": auth["nick"]},
                "content": [{"type": 0, "content": content}],
            },
        }
        if auth.get("uid"):
            body["comment"]["user"]["uid"] = auth["uid"]
        if "cell_feed_transfer" in feed:
            body["cell_feed_transfer"] = copy.deepcopy(feed["cell_feed_transfer"])
        result = await self.rpc(bot, "comment", body)
        return result["comment"]

    async def close(self):
        if self._http:
            await self._http.close()
        self._http = None
        self._auth = None
        self._feeds.clear()
        self._cursor = None
