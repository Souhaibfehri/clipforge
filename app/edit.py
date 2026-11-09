"""Edit planning: which parts of the source to keep, where to zoom, and how words map to the output timeline.

All times in a plan are relative to the render window start (the -ss point), on the INPUT timeline.
"""
FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "euh", "hmm", "mm", "mhm", "ah", "eh", "heu"}


def _norm(w: str) -> str:
    return "".join(ch for ch in w.lower() if ch.isalpha())


def keep_segments(words, start, end, remove_silences: bool, max_gap=0.45, pad_before=0.08, pad_after=0.14):
    """words: absolute-time words inside [start, end]. Returns [(s, e)] relative to start."""
    dur = end - start
    if not remove_silences or not words:
        return [(0.0, round(dur, 3))]
    spoken = [w for w in words if _norm(w["word"]) not in FILLERS]
    if not spoken:
        return [(0.0, round(dur, 3))]
    segs = []
    first = spoken[0]["start"] - start
    cur_s = max(0.0, first - pad_before) if first > max_gap else 0.0
    prev_end = spoken[0]["end"] - start
    for w in spoken[1:]:
        ws = w["start"] - start
        if ws - prev_end > max_gap:
            segs.append((cur_s, min(dur, prev_end + pad_after)))
            cur_s = max(0.0, ws - pad_before)
        prev_end = w["end"] - start
    segs.append((cur_s, min(dur, max(prev_end + pad_after, prev_end))))
    # tidy: merge overlaps, drop slivers
    out = []
    for s, e in segs:
        if e - s < 0.25:
            continue
        if out and s <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return [(round(s, 3), round(e, 3)) for s, e in out] or [(0.0, round(dur, 3))]


def map_time(t, segs):
    """Input-relative time -> output time, or None if t falls in a removed part."""
    acc = 0.0
    for s, e in segs:
        if s <= t <= e:
            return acc + (t - s)
        acc += e - s
    return None


def out_duration(segs) -> float:
    return round(sum(e - s for s, e in segs), 3)


def map_words(words, start, segs):
    """Absolute-time words -> output-timeline words (fillers inside removed gaps disappear)."""
    out = []
    for w in words:
        s_rel, e_rel = w["start"] - start, w["end"] - start
        mid = (s_rel + e_rel) / 2
        if map_time(mid, segs) is None:
            continue
        seg = next(sg for sg in segs if sg[0] <= mid <= sg[1])
        s_c, e_c = max(s_rel, seg[0]), min(e_rel, seg[1])
        out.append({"start": map_time(s_c, segs), "end": map_time(e_c, segs), "word": w["word"]})
    return out


def zoom_ranges(words, start, segs, every_sentences=1):
    """Alternate 1.0x / 1.12x zoom at sentence boundaries (a classic retention trick).

    Returns [(s, e)] input-relative ranges that should be zoomed in."""
    if not words:
        return []
    bounds = [0.0]
    for w in words:
        if w["word"].strip()[-1:] in ".!?":
            bounds.append(w["end"] - start)
    total = segs[-1][1]
    bounds.append(total)
    # also force a change at least every 6 seconds
    filled = [bounds[0]]
    for b in bounds[1:]:
        while b - filled[-1] > 6.0:
            filled.append(filled[-1] + 4.0)
        if b - filled[-1] >= 1.2:
            filled.append(b)
    ranges = []
    for i in range(len(filled) - 1):
        if i % 2 == 1:
            ranges.append((round(filled[i], 2), round(filled[i + 1], 2)))
    return ranges
