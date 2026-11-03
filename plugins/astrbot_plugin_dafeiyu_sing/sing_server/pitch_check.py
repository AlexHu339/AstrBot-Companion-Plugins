"""音准检查：一段唱出来的音频像不像在按某首曲子唱。

两个数：
- stable：有声部分里音高稳住（150 毫秒内波动不超过 0.8 个半音）的比例。念白一般 0–0.25，唱歌 0.6 以上。
- melody_err：把音频切成一个个音，和谱子的音高起伏做动态对齐（允许整体移调），平均每步差几个半音。
  按谱唱的 SoulX 是 0.08；数越小越像原曲。
"""
import numpy as np
import parselmouth

import melodies


def target_pitches(melody):
    m = melodies.MELODIES[melodies.resolve_melody(melody)]
    out = []
    for line in m["lines"]:
        out += [p for p, _ in line["notes"]] * line.get("repeat", 1)
    return np.array(out, float)


def _semitones(path):
    snd = parselmouth.Sound(path)
    f = snd.to_pitch_ac(time_step=0.01, pitch_floor=100, pitch_ceiling=900).selected_array["frequency"]
    st = np.where(f > 0, 12 * np.log2(np.maximum(f, 1) / 440) + 69, np.nan)
    return st, snd.duration


def _stable_ratio(st):
    voiced = ~np.isnan(st)
    if voiced.sum() < 20:
        return 0.0
    stable = np.zeros(len(st), bool)
    w = 15
    for i in range(len(st) - w):
        seg = st[i:i + w]
        if not np.isnan(seg).any() and seg.max() - seg.min() <= 0.8:
            stable[i:i + w] = True
    return float(stable[voiced].mean())


def _notes(st):
    segs, cur = [], []
    for x in st:
        if np.isnan(x) or (cur and abs(x - np.median(cur)) > 1.0):
            if len(cur) >= 8:
                segs.append(float(np.median(cur)))
            cur = [] if np.isnan(x) else [x]
        else:
            cur.append(x)
    if len(cur) >= 8:
        segs.append(float(np.median(cur)))
    return np.array(segs)


def _dtw(a, b):
    n, m = len(a), len(b)
    d = np.full((n + 1, m + 1), np.inf)
    d[0, 0] = 0
    cost = np.abs(a[:, None] - b[None, :])
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = cost[i - 1, j - 1] + min(d[i - 1, j], d[i, j - 1], d[i - 1, j - 1])
    return d[n, m] / (n + m)


def score(path, melody):
    st, dur = _semitones(path)
    notes = _notes(st)
    target = target_pitches(melody)
    target = target - target[0]
    res = {"seconds": round(dur, 1), "stable": round(_stable_ratio(st), 2), "n_notes": int(len(notes))}
    if len(notes) < 6:
        res["melody_err"] = None
        return res
    best = min(_dtw(notes - off, target) for off in np.arange(notes.min() - 12, notes.max() + 1, 0.25))
    res["melody_err"] = round(float(best), 2)
    return res


# 合格线：按 10-24、10-25 的样本定（见 changes.md 唱歌那节）
STABLE_MIN = 0.6
MELODY_ERR_MAX = 0.6


def passed(res):
    return (res.get("melody_err") is not None and res["stable"] >= STABLE_MIN
            and res["melody_err"] <= MELODY_ERR_MAX)
