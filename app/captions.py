"""ASS subtitles: word-by-word captions in several styles + hook banner + optional CTA."""
from . import config

# ASS colours are &HAABBGGRR
YELLOW = "&H0000E5FF&"
GREEN = "&H0066FF5A&"
WHITE = "&H00FFFFFF&"

# name: (style line fields, highlight colour or None, uppercase, words per chunk, highlight scale)
STYLES = {
    "bold": ("{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,7,3,2,90,90,{mv},1",
             YELLOW, True, 3, 110),
    "karaoke": ("{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,8,0,2,80,80,{mv},1",
                GREEN, True, 2, 118),
    "boxed": ("{font},70,&H00111111,&H00111111,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,2,110,110,{mv},1",
              None, False, 4, 100),
    "minimal": ("{font},66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,2,2,120,120,{mv},1",
                None, False, 5, 100),
}

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{caption}
Style: Hook,{font},66,&H00111111,&H00111111,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,3,22,0,8,110,110,{hook_mv},1
Style: CTA,{font},54,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,14,0,8,140,140,{hook_mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _t(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(word: str) -> str:
    return word.replace("{", "(").replace("}", ")").replace("\\", "").replace("\n", " ").strip()


def _chunks(words, max_words, max_chars=20, max_gap=0.6):
    chunk = []
    for w in words:
        if chunk:
            gap = w["start"] - chunk[-1]["end"]
            chars = sum(len(x["word"]) + 1 for x in chunk) + len(w["word"])
            if (len(chunk) >= max_words or chars > max_chars or gap > max_gap
                    or chunk[-1]["word"][-1:] in ".!?"):
                yield chunk
                chunk = []
        chunk.append(w)
    if chunk:
        yield chunk


def build_ass(words, duration, hook="", style="bold", layout="face", cta="") -> str:
    """words: output-timeline words [{"start","end","word"}]."""
    # captions sit lower-middle for full-frame layouts, under the picture for 'fit'
    mv = {"fit": 380, "split": 860}.get(layout, 560)
    hook_mv = 250 if layout != "fit" else 300
    caption_line, hi, upper, per_chunk, scale = STYLES.get(style, STYLES["bold"])
    lines = [HEADER.format(w=config.OUT_W, h=config.OUT_H, font=config.CAPTION_FONT, hook_mv=hook_mv,
                           caption=caption_line.format(font=config.CAPTION_FONT, mv=mv))]

    if hook.strip():
        lines.append(f"Dialogue: 2,{_t(0)},{_t(min(3.5, duration))},Hook,,0,0,0,,{_clean(hook)}")
    if cta.strip() and duration > 8:
        lines.append(f"Dialogue: 2,{_t(duration - 3.0)},{_t(duration)},CTA,,0,0,0,,{_clean(cta)}")
    if style == "none":
        return "\n".join(lines) + "\n"

    words = [dict(w, word=_clean(w["word"])) for w in words if _clean(w["word"])]
    if upper:
        words = [dict(w, word=w["word"].upper()) for w in words]
    words = [w for w in words if w["end"] > 0 and w["start"] < duration]
    chunks = list(_chunks(words, per_chunk))
    for ci, chunk in enumerate(chunks):
        chunk_end = chunk[-1]["end"]
        if ci + 1 < len(chunks):
            nxt = chunks[ci + 1][0]["start"]
            if nxt - chunk_end < 0.35:
                chunk_end = nxt
        chunk_end = min(chunk_end, duration)
        if hi is None:  # whole chunk at once
            if chunk_end > chunk[0]["start"]:
                lines.append(f"Dialogue: 0,{_t(chunk[0]['start'])},{_t(chunk_end)},Caption,,0,0,0,,"
                             + " ".join(x["word"] for x in chunk))
            continue
        for i, w in enumerate(chunk):
            start = max(0.0, w["start"])
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for j, x in enumerate(chunk):
                if j == i:
                    parts.append(f"{{\\c{hi}\\fscx{scale}\\fscy{scale}}}{x['word']}{{\\c{WHITE}\\fscx100\\fscy100}}")
                else:
                    parts.append(x["word"])
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"
