"""大肥鱼唱歌服务：歌词 + 曲子名 → SoulX-Singer 唱出来的 wav。

模型只在启动时加载一次，之后每首歌十几秒。

  python sing_server.py --soulx D:\\dafeiyu\\sing\\SoulX-Singer --model D:\\dafeiyu\\sing\\models\\SoulX-Singer\\model.pt

两个后端：
  soulx   本地 SoulX-Singer，按谱唱，准；要 N 卡，给了 --soulx 和 --model 才开
  doubao  豆包 Seed Audio 1.0（云端），更像真人但不按谱，抽 3 次挑音准最好的；
          找得到火山凭证才开（环境变量或 --volc-env 指的文件，见 doubao_backend.py），1 元/分钟

  GET  /health → {"ok": true, "backends": ["soulx", "doubao"], ...}
  POST /sing   {"melody": "小星星", "lyrics": "第一句/第二句/第三句/第四句", "backend": "soulx"}
       → {"ok": true, "path": "...wav", "file": "xxx.wav", "seconds": 17.9, "gen_time": 12.3, "lyrics": [...]}
  GET  /out/<file>   取生成好的 wav（唱歌服务和 AstrBot 不在同一台电脑时用）
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import melodies  # noqa: E402

STATE = {}
LOCK = threading.Lock()


def load(args):
    STATE.update(out=args.out, backends=[])
    try:
        import doubao_backend
        creds = doubao_backend.load_creds(args.volc_env)
    except ImportError as e:  # 没装 praat-parselmouth 之类
        print(f"[sing] doubao backend off: {e!r}", flush=True)
        creds = None
    if creds:
        STATE.update(doubao=doubao_backend, creds=creds)
        STATE["backends"].append("doubao")
    if args.soulx and args.model and os.path.isfile(args.model):
        os.chdir(args.soulx)
        load_soulx(args)
        STATE["backends"].append("soulx")
    print(f"[sing] backends: {STATE['backends'] or 'none'}", flush=True)


def load_soulx(args):
    sys.path.insert(0, args.soulx)
    import torch
    from soulxsinger.utils.file_utils import load_config
    from soulxsinger.utils.data_processor import DataProcessor
    from cli.inference import build_model

    device = "cuda" if torch.cuda.is_available() and args.device != "cpu" else "cpu"
    config = load_config(os.path.join(args.soulx, "soulxsinger", "config", "soulxsinger.yaml"))
    t0 = time.time()
    model = build_model(args.model, config, device=device, use_fp16=device == "cuda")
    phoneset_path = os.path.join(args.soulx, "soulxsinger", "utils", "phoneme", "phone_set.json")
    dp = DataProcessor(hop_size=config.audio.hop_size, sample_rate=config.audio.sample_rate,
                       phoneset_path=phoneset_path, device=device)
    with open(os.path.join(args.voice, "prompt.json"), encoding="utf-8") as f:
        prompt_meta = json.load(f)[0]
    prompt_median = statistics.median(int(p) for p in prompt_meta["note_pitch"].split() if int(p) > 0)
    prompt = dp.process(prompt_meta, os.path.join(args.voice, "prompt.wav"))
    with open(phoneset_path, encoding="utf-8") as f:
        phoneset = set(json.load(f))
    STATE.update(torch=torch, model=model, config=config, dp=dp, prompt=prompt, device=device,
                 prompt_median=prompt_median, phoneset=phoneset)
    print(f"[sing] model ready on {device} in {time.time() - t0:.1f}s", flush=True)


def sing(melody, lyrics):
    import numpy as np
    import soundfile as sf

    torch, model, config, dp = STATE["torch"], STATE["model"], STATE["config"], STATE["dp"]
    metas, shift, lines = melodies.build(melody, lyrics, STATE["phoneset"], STATE["prompt_median"])
    sr = config.audio.sample_rate
    merged = np.zeros(int(metas[-1]["time"][1] / 1000 * sr), dtype=np.float32)
    t0 = time.time()
    with LOCK:
        for meta in metas:
            start = int(meta["time"][0] / 1000 * sr)
            target = dp.process(dict(meta), None)
            with torch.no_grad():
                audio = model.infer({"prompt": STATE["prompt"], "target": target}, auto_shift=False,
                                    pitch_shift=shift, n_steps=config.infer.n_steps, cfg=config.infer.cfg,
                                    control="score", use_fp16=STATE["device"] == "cuda")
            audio = audio.squeeze().cpu().numpy()
            n = min(len(audio), len(merged) - start)
            merged[start:start + n] = audio[:n]
    gen_time = time.time() - t0
    peak = float(np.abs(merged).max()) or 1.0
    merged = merged / peak * 0.9
    tail = np.zeros(int(0.4 * sr), dtype=np.float32)
    merged = np.concatenate([merged, tail])
    name = f"{time.strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.wav"
    path = os.path.join(STATE["out"], name)
    sf.write(path, merged, sr, subtype="PCM_16")
    print(f"[sing] {melodies.resolve_melody(melody)} {'/'.join(lines)} -> {name} "
          f"{len(merged) / sr:.1f}s audio in {gen_time:.1f}s", flush=True)
    return {"ok": True, "path": path, "file": name, "seconds": round(len(merged) / sr, 1),
            "gen_time": round(gen_time, 1), "melody": melodies.resolve_melody(melody), "lyrics": lines,
            "backend": "soulx"}


def sing_doubao(melody, lyrics):
    with LOCK:  # 一次只抽一首，免得并发多花钱
        res = STATE["doubao"].sing(melody, lyrics, STATE["creds"], STATE["out"],
                                   log=lambda s: print(s, flush=True))
    print(f"[sing] doubao {res['melody']} {'/'.join(res['lyrics'])} -> {res['file']} in_tune={res['in_tune']} "
          f"takes={len(res['takes'])} billed={res['billed_seconds']}s ({res['cost_yuan']} yuan)", flush=True)
    return res


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": bool(STATE.get("backends")), "backends": STATE.get("backends", []),
                                    "device": STATE.get("device"), "melodies": list(melodies.MELODIES)})
        if self.path.startswith("/out/"):
            name = os.path.basename(self.path[5:])
            path = os.path.join(STATE["out"], name)
            if not name.endswith(".wav") or not os.path.isfile(path):
                return self._json(404, {"ok": False, "error": "not found"})
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/sing":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            backend = req.get("backend") or "soulx"
            if backend not in STATE.get("backends", []):
                return self._json(400, {"ok": False, "error": f"唱歌后端 {backend} 没开，现在能用的：{STATE.get('backends')}"})
            fn = sing_doubao if backend == "doubao" else sing
            self._json(200, fn(req.get("melody", "小星星"), req.get("lyrics", "")))
        except Exception as e:  # 唱劈了也要回个话，别让插件干等
            print(f"[sing] error: {e!r}", flush=True)
            self._json(500, {"ok": False, "error": repr(e)})

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--soulx", help="SoulX-Singer 源码目录（不给就不开 soulx 后端）")
    ap.add_argument("--model", help="SoulX 的 model.pt 路径")
    ap.add_argument("--volc-env", help="火山凭证文件（豆包后端用），默认 ~/.config/volc-speech/env")
    ap.add_argument("--voice", default=os.path.join(HERE, "voice"), help="放 prompt.wav 和 prompt.json 的目录")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18620)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()
    args.out = os.path.abspath(args.out)
    os.makedirs(args.out, exist_ok=True)
    load(args)
    print(f"[sing] listening on http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
