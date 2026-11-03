"""Generate free-form cloud singing without a fixed song catalog."""
import base64
import difflib
import json
import os
import re
import time
import uuid
import wave

import aiohttp

try:
    from .speech_meter import meter_for
except ImportError:
    meter_for = None

try:
    from .sing_server import melodies
except ImportError:  # 单独跑测试时
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sing_server"))
    import melodies

URL = "https://openspeech.bytedance.com/api/v3/tts/create"
RESOURCE_ID = "seed-audio-1.0"
SAMPLE_RATE = 24000
PRICE_PER_MIN = 1.0
PERSONA = "沿用参考声音，清唱自然柔和"
LYRICS_MIN = 0.8
TAIL_SILENCE = 0.3  # 末尾补 0.3 秒静音，免得最后一个字被截掉
_WORDS = re.compile(r"[一-鿿A-Za-z0-9]")


def build_prompt(melody, lines, persona=PERSONA, seconds=25):
    """Build a song/topic prompt while preserving all supplied lyric lines.

    Args:
        melody: Requested title, theme, or free-form melody description.
        lines: Complete lyric lines, or an empty list for humming.
        persona: Short performance direction from the current conversation.
        seconds: Desired duration; the provider decides the actual duration.

    Returns:
        A generation prompt and the expected lyric text.
    """
    title = str(melody or "原创小调").strip()[:120]
    seconds = max(2, min(120, int(seconds or 25)))
    prompt = (
        f"唱法：{persona}。如果提供参考声音，由@音频1演唱，保留参考声线，不朗读参考录音。"
        f"本次歌名或主题是「{title}」。认真唱成有旋律、有呼吸和感情的短歌，不能念白。"
        "歌名可以作为风格或主题参考；不能准确还原原曲时也继续唱，采用自然的原创旋律。"
        "不要强行套成小星星、两只老虎或生日快乐，不宣称是原曲复刻。"
        f"整段期望约{seconds}秒，不要解说、报幕或长前奏。"
    )
    if lines:
        prompt += "按以下歌词和段落演唱，不遗漏、不擅自补重复段落：\n" + "\n".join(lines)
    else:
        prompt += "本次没有歌词，用哼唱或啦声演唱一段契合主题的原创小调。"
    if len(prompt) > 3000:
        raise ValueError("Song prompt exceeds the provider's 3000-character limit")
    return prompt, "".join(lines)


def lyric_match(heard, expected):
    """Compare subtitle words, never melody or pitch.

    Args:
        heard: Optional subtitles returned by the provider.
        expected: Requested lyric text.

    Returns:
        Text similarity, or None when subtitles/lyrics are unavailable.
    """
    a = "".join(_WORDS.findall(heard or "")).lower()
    b = "".join(_WORDS.findall(expected or "")).lower()
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 2) if a and b else None


async def _generate_raw(creds, prompt, timeout, request_id):
    body = {"model": RESOURCE_ID, "text_prompt": prompt,
            "audio_config": {"format": "pcm", "sample_rate": SAMPLE_RATE, "enable_subtitle": True}}
    headers = {"X-Api-Request-Id": request_id, "Content-Type": "application/json"}
    if isinstance(creds, dict):
        headers["X-Api-Key"] = creds["api_key"]
        if creds.get("speaker"):
            body["references"] = [{"speaker": creds["speaker"]}]
    else:
        headers.update({"X-Api-App-Id": creds[0], "X-Api-Access-Key": creds[1],
                        "X-Api-Resource-Id": RESOURCE_ID})
    async with aiohttp.ClientSession() as s:
        async with s.post(URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers,
                          timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            raw = await r.text()
    try:
        j = json.loads(raw)
    except Exception:
        raise RuntimeError(f"Audio generation returned non-JSON: HTTP {r.status}") from None
    if r.status != 200 or not j.get("audio"):
        detail = str(j.get("message") or "No audio returned")
        secrets = list(creds.values()) if isinstance(creds, dict) else list(creds)
        for secret in secrets:
            if secret:
                detail = detail.replace(str(secret), "[REDACTED]")
        detail = detail[:500]
        log_id = str(r.headers.get("X-Tt-Logid") or "") if hasattr(r, "headers") else ""
        raise RuntimeError(
            f"Audio generation failed: HTTP {r.status}, code={j.get('code')}, "
            f"detail={detail}, log_id={log_id}"
        )
    sub = j.get("subtitle") or {}
    heard = sub.get("text") or "".join(x.get("text", "") for x in sub.get("sentences", []))
    duration = float(j.get("original_duration") or 0)
    return j["audio"], duration if duration > 0 else None, heard


async def _generate(creds, prompt, timeout):
    """Meter each upstream take, even if later decoding or delivery fails."""
    from astrbot.api import logger

    request_id = str(uuid.uuid4())
    meter = None
    state, duration = "uncertain", None
    try:
        meter = meter_for(__file__) if meter_for else None
        if meter:
            meter.begin(request_id, RESOURCE_ID)
    except Exception:
        logger.warning("Singing accounting start failed")
        meter = None
    try:
        audio, duration, heard = await _generate_raw(creds, prompt, timeout, request_id)
        state = "completed"
    except Exception as exc:
        if re.search(r"HTTP 4\d\d", str(exc)):
            state = "rejected"
        raise
    finally:
        if meter:
            try:
                meter.finish(request_id, seconds=duration, state=state)
            except Exception:
                logger.warning("Singing accounting settlement failed")
    return base64.b64decode(audio), float(duration or 0), heard


def _channels(pcm, dur):
    # 豆包回的 pcm 是 16 位双声道（10-25 实测）；按计费时长反推声道数，接口改了也不会把歌拉长一倍
    if dur <= 0:
        return 2
    return min(2, max(1, round(len(pcm) / 2 / SAMPLE_RATE / dur)))


def _write_wav(path, pcm, channels):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm + b"\0\0" * channels * int(TAIL_SILENCE * SAMPLE_RATE))


async def sing(melody, lyrics, creds, out_dir, tries=1, timeout=150, persona=PERSONA, log=print,
               seconds=25):
    melody = str(melody or "原创小调").strip()[:120]
    lines = [s.strip() for s in re.split(r"[/／\r\n]+", str(lyrics or "")) if s.strip()]
    prompt, expected = build_prompt(melody, lines, persona, seconds)
    os.makedirs(out_dir, exist_ok=True)
    stem = f"sing-{time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    takes, billed, t_all, best = [], 0.0, time.time(), None
    for i in range(1, max(1, tries) + 1):
        t0 = time.time()
        try:
            pcm, dur, heard = await _generate(creds, prompt, timeout)
        except Exception as e:
            log(f"[dafeiyu_sing] 豆包第 {i} 次没成：{e}")
            takes.append({"take": i, "error": str(e)[:200]})
            if re.search(r"HTTP 4\d\d", str(e)):
                break
            continue
        billed += dur
        take = {"take": i, "billed": round(dur, 1), "gen_time": round(time.time() - t0, 1),
                "lyrics_match": lyric_match(heard, expected), "heard": heard}
        takes.append(take)
        log(f"[dafeiyu_sing] 豆包第 {i} 次：{take}")
        quality = take["lyrics_match"] if take["lyrics_match"] is not None else -1
        best_quality = (best[0]["lyrics_match"] if best is not None
                        and best[0]["lyrics_match"] is not None else -1)
        if best is None or quality > best_quality:
            best = (take, pcm, _channels(pcm, dur))
        if take["lyrics_match"] is None or take["lyrics_match"] >= LYRICS_MIN:
            break
    if best is None:
        raise RuntimeError(f"豆包 {len(takes)} 次都没出音频：{takes[-1].get('error')}")
    take, pcm, channels = best
    path = os.path.join(out_dir, f"{stem}.wav")
    _write_wav(path, pcm, channels)
    return {"ok": True, "path": path, "file": os.path.basename(path),
            "seconds": round(len(pcm) / 2 / channels / SAMPLE_RATE + TAIL_SILENCE, 1),
            "gen_time": round(time.time() - t_all, 1), "melody": melody, "lyrics": lines,
            "backend": "doubao", "lyrics_match": take["lyrics_match"], "takes": takes,
            "billed_seconds": round(billed, 1), "cost_yuan": round(billed / 60 * PRICE_PER_MIN, 2),
            "melody_verified": False}
