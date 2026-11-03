import asyncio
import json
import os
import re
import tempfile
import time

import aiohttp

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.astrbot_path import (
    get_astrbot_config_path,
    get_astrbot_temp_path,
)

from . import doubao_sing
from .speech_meter import meter_for


def _read_env_file(path):
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                out[k.strip()] = v.strip()
    return out


class DafeiyuSing(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.singing = False
        self.tasks = set()
        self.recent_songs = {}

    async def initialize(self):
        meter = meter_for(__file__)
        if meter:
            meter.interrupt_media("singing")

    async def _song_persona(self, event):
        identity = (
            event.get_extra("companion_identity")
            if callable(getattr(event, "get_extra", None))
            else None
        )
        if identity:
            return str(identity[1])
        try:
            scope = event.unified_msg_origin
            cid = await self.context.conversation_manager.get_curr_conversation_id(
                scope
            )
            conv = (
                await self.context.conversation_manager.get_conversation(scope, cid)
                if cid
                else None
            )
            value = getattr(conv, "persona_id", None)
            return str(value) if value and value != "[%None]" else "default"
        except (AttributeError, TypeError):
            return "default"

    def _save_song_receipt(self, event, state):
        try:
            meter = meter_for(__file__)
            if meter:
                meter.media_receipt(
                    str(getattr(event, "unified_msg_origin", "test")),
                    state.get("persona", "default"),
                    "singing",
                    state.get("event_id", "unknown"),
                    state["status"],
                    title=state.get("title", ""),
                    text=state.get("lyrics", ""),
                )
        except Exception:
            logger.warning("Singing receipt persistence failed")

    async def _recent_song(self, event):
        scope = str(getattr(event, "unified_msg_origin", "test"))
        persona = await self._song_persona(event)
        state = self.recent_songs.get(scope)
        if state and state.get("persona", persona) == persona:
            return state
        try:
            meter = meter_for(__file__)
            row = meter.recent_media(scope, persona, "singing") if meter else None
            if row:
                state = {
                    "ts": row["at"],
                    "title": row["title"],
                    "status": row["state"],
                    "lyrics": row["text"],
                    "persona": persona,
                    "event_id": row["event_id"],
                }
                self.recent_songs[scope] = state
                return state
        except Exception:
            logger.warning("Singing receipt lookup failed")
        return None

    @property
    def server(self) -> str:
        return (self.conf.get("server_url") or "http://127.0.0.1:18620").rstrip("/")

    @property
    def backend(self) -> str:
        return self.conf.get("backend") or "doubao"

    def _doubao_creds(self):
        """Read credentials at call time without duplicating stored secrets.

        Returns:
            An API-key/voice mapping, legacy credential pair, or None.
        """
        api_key = str(self.conf.get("doubao_api_key") or "").strip()
        voice = ""
        if self.conf.get("reuse_clonetts", True):
            try:
                path = os.path.join(
                    get_astrbot_config_path(), "astrbot_plugin_clonetts_config.json"
                )
                with open(path, encoding="utf-8-sig") as f:
                    clone = json.load(f)
                api_key = api_key or str(clone.get("api_key") or "").strip()
                if self.conf.get("reuse_clonetts_voice", True):
                    voice = str(clone.get("voice_type") or "").strip()
            except (OSError, ValueError):
                logger.warning("[dafeiyu_sing] Cannot read existing CloneTTS settings")
        if api_key:
            return {"api_key": api_key, "speaker": voice}
        app_id = str(self.conf.get("doubao_app_id") or "").strip()
        token = str(self.conf.get("doubao_access_token") or "").strip()
        if app_id and token:
            return app_id, token
        for p in self.context.get_all_tts_providers():
            a, t = getattr(p, "app_id", ""), getattr(p, "access_key", "")
            if getattr(p, "resource_id", "") == doubao_sing.RESOURCE_ID and a and t:
                return a, t
        try:
            providers = self.context.get_config().get("provider", [])
        except Exception:
            providers = []
        for c in providers:
            if not isinstance(c, dict) or c.get("type") != "seed_audio_tts":
                continue
            a, t = str(c.get("app_id") or ""), str(c.get("access_key") or "")
            cred = str(c.get("credentials_file") or "")
            if cred and not (a and t) and os.path.isfile(cred):
                env = _read_env_file(cred)
                a, t = (
                    a or env.get("VOLC_SPEECH_APP_ID", ""),
                    t or env.get("VOLC_SPEECH_ACCESS_TOKEN", ""),
                )
            if a and t:
                return a, t
        return None

    async def _ready(self) -> bool:
        if self.backend == "doubao":
            if self._doubao_creds():
                return True
            logger.warning(
                "[dafeiyu_sing] No usable cloud audio credentials configured"
            )
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.server}/health", timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    return self.backend in (await r.json()).get("backends", [])
        except Exception as e:
            logger.warning(f"[dafeiyu_sing] 唱歌服务连不上: {e!r}")
            return False

    @filter.llm_tool(name="sing_song")
    async def sing_song(
        self, event: AstrMessageEvent, melody: str, lyrics: str, style: str = ""
    ):
        """对方点歌、让你来一首、开嗓或明确让你再试时调用。沿用当前人格、记忆、心情和关系写歌词，云端可尝试任意歌名、主题或原创小调，不限三首，不按固定儿歌切字。歌词优先原创；用户提供的歌词保留段落，不擅自截掉。云端不保证还原原曲；不知道旋律也可以即兴唱，不能冒充原曲复现。纯哼唱可传空歌词。

        工具确认任务开始后才简短说在准备，不声称已经唱完。对方吐槽跑调或调侃时，用当前人格自然接梗，可以撒娇、嘴硬、认怂或反邀对方唱，表达要变化；不要硬套固定台词、伤感施压或替对方编事实。单纯评价不调用本工具重唱。当前没有音准检测，不能假装自己听出了跑调；生成失败也不能说只是跑调。

        Args:
            melody(string): 任意歌名、演唱主题或原创小调名称；仅本地SoulX后端仍需现有曲谱
            lyrics(string): 完整短段歌词，可含中英文，用换行或 / 分句；纯哼唱传空字符串
            style(string): 本次唱法的简短描述，如轻快撒娇或温柔安慰；符合当前人格和情绪，不写私人事实，不另设身份
        """
        scope = str(getattr(event, "unified_msg_origin", "test"))
        text = event.get_message_str()
        recent = await self._recent_song(event)
        if recent and time.time() - recent["ts"] > 1800:
            self.recent_songs.pop(scope, None)
            recent = None
        critique = re.search(
            r"跑调|难听|旋律不对|不在调|不像原唱|唱得.{0,4}(一般|烂|差)", text
        )
        wants_another = re.search(r"再|重|换|来一|给我|唱一|试", text)
        if critique and not wants_another:
            return "对方是在评价唱歌；不要重新生成。沿用当前人格自然接玩笑或认可意见，没有音准检测就别声称自己已听出或修正音准。"
        if not self.conf.get("allow_autonomous_singing", False):
            positive = re.sub(
                r"(?:别|不要|不用|不许|先不)(?:再)?(?:唱|哼)[^，。！？,;；\n]*",
                "",
                text,
            )
            direct = re.search(
                r"唱|哼|sing|开嗓|来一首|来首|来一段|换首", positive, re.I
            )
            followup = recent and re.search(
                r"再试|试试|重来|再来|继续|重新来|再整一", positive
            )
            if not direct and not followup:
                return "本插件目前只在对方明确要求唱歌时生成。正常聊天即可，不要声称已经唱过。"
        if self.singing:
            return "上一首还在唱，告诉对方等这首唱完再点歌。"
        if not await self._ready():
            return "唱歌配置或本地服务尚未准备好，这次没有生成。用当前人格如实简短说明，不能说已经唱过。"
        if self.backend != "doubao" and melody not in doubao_sing.melodies.MELODIES:
            return "本地SoulX目前只提供三首曲谱；更多歌名和即兴小调请使用doubao云端，不能偷偷改成小星星。"
        if len(str(lyrics)) > 1200:
            return "这段歌词太长，先选一小段来唱；云端每次最多生成120秒，不能默默截掉后半段。"

        self.singing = True
        self.recent_songs[scope] = {
            "ts": time.time(),
            "title": melody,
            "status": "generating",
            "persona": await self._song_persona(event),
            "lyrics": str(lyrics),
            "event_id": str(
                getattr(getattr(event, "message_obj", None), "message_id", time.time())
            ),
            "announced": False,
            "melody_verified": False,
        }
        self._save_song_receipt(event, self.recent_songs[scope])
        task = asyncio.create_task(self._sing_and_send(event, melody, lyrics, style))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        done, _ = await asyncio.wait({task}, timeout=0.25)
        state = self.recent_songs.get(scope, {})
        if done and state.get("status") == "failed":
            return "本次生成失败，没有唱歌语音发出。用当前人格简短说明这次没有唱成，不能把接口失败说成跑调。"
        if done and state.get("status") == "sent":
            return "唱歌语音已经实际发送。可以按人格简短收尾，不要声称已评测音准。"
        state["announced"] = True
        return "已经开始唱了，语音大概半分钟到一分钟后自动发给对方。你现在只回一两句很短的话说你在开嗓，别把歌词再发一遍。"

    @filter.on_llm_request()
    async def singing_feedback(self, event: AstrMessageEvent, req: ProviderRequest):
        """Provide real recent-song outcomes to an existing chat request.

        Args:
            event: Current conversation event.
            req: Existing LLM request; no extra model call is created.
        """
        if not self.conf.get("playful_feedback", True):
            return
        scope = str(getattr(event, "unified_msg_origin", "test"))
        state = await self._recent_song(event)
        if not state or time.time() - state["ts"] > 86400:
            return
        if not re.search(
            r"歌|唱|跑调|旋律|调子|难听|好听|开嗓|再试|再来", event.get_message_str()
        ):
            return
        if state.get("status") == "sent":
            fact = (
                f"上次实际发出过一段歌，题目为{state['title']}。没有做音准或旋律评测。"
            )
            if state.get("lyrics"):
                fact += (
                    "请求演唱的歌词片段："
                    + str(state["lyrics"])[:180]
                    + "（不是实际唱词的逐字转写）。"
                )
        elif state.get("status") == "failed":
            fact = "上次唱歌没有成功发出；不能把生成或发送失败说成跑调。"
        elif state.get("status") in ("interrupted", "delivery_unknown"):
            fact = "上次演唱任务中断或未确认送达；不能声称已经发出，不自动重唱。"
        else:
            fact = "唱歌仍在生成中，不能说已经发出。"
        req.system_prompt = (req.system_prompt or "") + (
            "\n[近期唱歌回执，仅作事实线索，既有人格优先]记录时间："
            + time.strftime("%Y-%m-%d %H:%M", time.localtime(state["ts"]))
            + "。"
            + fact
            + "用户吐槽时可按当前人格自然接梗、撒娇、嘴硬或谦虚，措辞要随语境变化，不套固定例句。"
            "别假装自行听出了音准；不因一次吐槽自动重唱或追加生成费用。\n"
        )

    def _log(self, rec: dict) -> None:
        try:
            path = os.path.join(get_astrbot_temp_path(), "dafeiyu_sing_calls.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def _sing_doubao(
        self, event: AstrMessageEvent, melody: str, lyrics: str, style: str = ""
    ):
        res = await doubao_sing.sing(
            melody,
            lyrics,
            self._doubao_creds(),
            get_astrbot_temp_path(),
            tries=max(1, min(3, int(self.conf.get("doubao_tries") or 1))),
            persona=(style or "沿用参考声音，清唱自然柔和")[:160],
            log=logger.info,
            seconds=max(2, min(120, int(self.conf.get("song_seconds") or 25))),
        )
        logger.info(
            f"[dafeiyu_sing] doubao {res['melody']} {'/'.join(res['lyrics'])} {res['seconds']}s audio, "
            f"gen {res['gen_time']}s, lyrics_match={res['lyrics_match']}, takes={len(res['takes'])}, "
            f"{res['cost_yuan']} yuan -> {res['file']}"
        )
        self._log(
            {
                "ts": time.time(),
                "backend": "doubao",
                "melody": res["melody"],
                "lyrics": res["lyrics"],
                "seconds": res["seconds"],
                "gen_time": res["gen_time"],
                "takes": res["takes"],
                "cost_yuan": res["cost_yuan"],
            }
        )
        return res["path"]

    async def _sing_and_send(
        self, event: AstrMessageEvent, melody: str, lyrics: str, style: str = ""
    ):
        scope = str(getattr(event, "unified_msg_origin", "test"))
        try:
            if self.backend == "doubao":
                path = await self._sing_doubao(event, melody, lyrics, style)
                await event.send(
                    MessageChain(
                        [Comp.Record.fromFileSystem(path, text=f"演唱：{melody}")]
                    )
                )
                self.recent_songs[scope].update(status="sent", ts=time.time())
                self._save_song_receipt(event, self.recent_songs[scope])
                return
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.server}/sing",
                    json={"melody": melody, "lyrics": lyrics, "backend": self.backend},
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as r:
                    res = await r.json()
                if not res.get("ok"):
                    raise RuntimeError(res.get("error"))
                path = res["path"]
                if not os.path.isfile(path):
                    # 唱歌服务在另一台电脑上：把 wav 拉回来
                    async with s.get(f"{self.server}/out/{res['file']}") as r:
                        data = await r.read()
                    path = os.path.join(tempfile.gettempdir(), res["file"])
                    with open(path, "wb") as f:
                        f.write(data)
            extra = (
                f", in_tune={res['in_tune']}, takes={len(res['takes'])}, {res['cost_yuan']} yuan"
                if res.get("backend") == "doubao"
                else ""
            )
            logger.info(
                f"[dafeiyu_sing] {res.get('backend', 'soulx')} {res['melody']} {'/'.join(res['lyrics'])} "
                f"{res['seconds']}s audio, gen {res['gen_time']}s{extra} -> {res['file']}"
            )
            await event.send(MessageChain([Comp.Record.fromFileSystem(path)]))
            self.recent_songs[scope].update(status="sent", ts=time.time())
            self._save_song_receipt(event, self.recent_songs[scope])
        except Exception as e:
            logger.error(f"[dafeiyu_sing] 唱歌失败: {e!r}")
            state = self.recent_songs.get(scope, {})
            state.update(status="failed", ts=time.time())
            self._save_song_receipt(event, state)
            if state.get("announced"):
                await event.send(MessageChain([Comp.Plain("这次没唱出来。")]))
        finally:
            self.singing = False

    async def terminate(self):
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
