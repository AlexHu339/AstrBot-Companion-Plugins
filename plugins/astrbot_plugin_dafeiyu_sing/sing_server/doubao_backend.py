"""豆包 Seed Audio 1.0 唱歌后端：写一段「人设 + 曲子 + 歌词」的提示交给云端生成，
用音准检查打分，不在调上就重抽，最多抽 3 次，留分数最好的一条。

豆包每次结果都不一样，有时唱在调上，有时只是哼、甚至念出来，所以要抽。
按生成的音频时长计费：1 元/分钟（火山引擎「豆包音频生成模型 1.0」）。

凭证：环境变量 VOLC_SPEECH_APP_ID、VOLC_SPEECH_ACCESS_TOKEN，或者一个写着这两行的文件
（默认 ~/.config/volc-speech/env，格式 KEY=VALUE）。凭证不会写进日志。
"""
import base64
import difflib
import io
import json
import os
import re
import time
import urllib.request
import uuid

import numpy as np
import soundfile as sf

import melodies
import pitch_check

URL = "https://openspeech.bytedance.com/api/v3/tts/create"
PRICE_PER_MIN = 1.0
PERSONA = "一个憨萌软糯的小女生，声音甜甜的、有点奶，嘴馋爱撒娇，带一点小傲娇"
TUNES = {
    "小星星": "《小星星》（一闪一闪亮晶晶，满天都是小星星）",
    "两只老虎": "《两只老虎》（两只老虎，两只老虎，跑得快，跑得快）",
    "生日快乐": "《生日快乐歌》（祝你生日快乐）",
}
LYRICS_MIN = 0.8  # 字幕里的字和歌词至少对上八成
_HAN = re.compile(r"[一-鿿]")


def load_creds(path=None):
    app_id, token = os.environ.get("VOLC_SPEECH_APP_ID"), os.environ.get("VOLC_SPEECH_ACCESS_TOKEN")
    path = os.path.expanduser(path or "~/.config/volc-speech/env")
    if not (app_id and token) and os.path.isfile(path):
        env = dict(l.strip().split("=", 1) for l in open(path, encoding="utf-8-sig") if "=" in l)
        app_id, token = env.get("VOLC_SPEECH_APP_ID"), env.get("VOLC_SPEECH_ACCESS_TOKEN")
    return (app_id, token) if app_id and token else None


def build_prompt(melody, lines):
    m = melodies.MELODIES[melody]
    sung, beats = [], 0.0
    for spec, line in zip(m["lines"], lines):
        sung += [line] * spec.get("repeat", 1)
        beats += sum(b for _, b in spec["notes"]) * spec.get("repeat", 1)
    seconds = round(beats * 60 / m["bpm"])
    # 不写速度时豆包会唱得很赶（28 个字 10 秒），旋律也跟着丢，所以把整首该唱多久写进去
    return (f"{PERSONA}。她像幼儿园老师教小朋友那样，慢慢地清唱儿歌{TUNES[melody]}，"
            f"旋律和原曲一模一样，只是把歌词换成了她自己编的。一个字一拍，音唱准、唱稳，"
            f"每句最后一个字拖长，整首大约唱{seconds}秒。不说话、不加别的词，直接开唱："
            f"“{'，'.join(sung)}。”"), "".join(sung)


def generate(creds, prompt):
    body = {"model": "seed-audio-1.0", "text_prompt": prompt,
            "audio_config": {"format": "mp3", "sample_rate": 24000, "enable_subtitle": True}}
    req = urllib.request.Request(URL, json.dumps(body, ensure_ascii=False).encode("utf-8"), {
        "X-Api-App-Id": creds[0], "X-Api-Access-Key": creds[1],
        "X-Api-Resource-Id": "seed-audio-1.0", "Content-Type": "application/json"})
    j = json.load(urllib.request.urlopen(req, timeout=300))
    if not j.get("audio"):
        raise RuntimeError(f"豆包没出音频：code={j.get('code')} message={j.get('message')}")
    sub = j.get("subtitle") or {}
    return base64.b64decode(j["audio"]), float(j.get("original_duration") or 0), sub.get("text", "")


def lyric_match(heard, expected):
    a, b = "".join(_HAN.findall(heard)), "".join(_HAN.findall(expected))
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 2) if b else 0.0


def sing(melody, lyrics, creds, out_dir, tries=3, log=print):
    melody = melodies.resolve_melody(melody)
    lines = melodies.lyric_lines(melody, lyrics)
    prompt, expected = build_prompt(melody, lines)
    stem = f"{time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-doubao"
    takes, billed, t_all = [], 0.0, time.time()
    for i in range(1, tries + 1):
        t0 = time.time()
        mp3, dur, heard = generate(creds, prompt)
        billed += dur
        audio, sr = sf.read(io.BytesIO(mp3), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = np.concatenate([audio, np.zeros(int(0.3 * sr), np.float32)])
        path = os.path.join(out_dir, f"{stem}-{i}.wav")
        sf.write(path, audio, sr, subtype="PCM_16")
        res = pitch_check.score(path, melody)
        res.update(take=i, billed=round(dur, 1), gen_time=round(time.time() - t0, 1),
                   lyrics_match=lyric_match(heard, expected))
        res["ok"] = pitch_check.passed(res) and res["lyrics_match"] >= LYRICS_MIN
        takes.append((res, path))
        log(f"[sing] doubao take {i}: {res}")
        if res["ok"]:
            break
    best, best_path = max(takes, key=lambda t: (t[0]["ok"], -(t[0]["melody_err"] if t[0]["melody_err"] is not None else 9)))
    for _, p in takes:
        if p != best_path:
            os.remove(p)
    return {"ok": True, "path": best_path, "file": os.path.basename(best_path),
            "seconds": best["seconds"], "gen_time": round(time.time() - t_all, 1),
            "melody": melody, "lyrics": lines, "backend": "doubao", "in_tune": best["ok"],
            "takes": [t for t, _ in takes], "billed_seconds": round(billed, 1),
            "cost_yuan": round(billed / 60 * PRICE_PER_MIN, 2)}
