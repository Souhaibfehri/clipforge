"""Claude is the brain: picks moments, writes packaging, and explains what's working from the stats."""
import json
import math
import re

from . import config

SYSTEM = """You are a top short-form video editor and growth strategist for TikTok, Reels and YouTube Shorts.
You find the moments in long videos that stop the scroll, and you package them so people watch to the end.
You only ever answer with one valid JSON object, no markdown fences, no commentary."""

PROMPT = """Below is material from a long video: a timestamped transcript (each line is [start-end seconds] text){signal_note}.

CHANNEL NICHE: {niche}
{brief}
{angle_block}{winners_block}
Pick the {n} best moments to turn into vertical clips.

Rules for each clip:
- Between {min_s} and {max_s} seconds long. Use exact timestamps from the material.
- Self-contained: a viewer with zero context understands it.
- Starts right on a strong first sentence or the start of the action. Never start mid-sentence.
- Ends on a payoff or complete thought. Never end mid-sentence.
- Clips must not overlap.

For each clip also write:
- "cold_open": the single most gripping 1.5-4 second line or moment INSIDE the clip, as {{"start": s, "end": e}}, shown first as a teaser before the clip plays from its start. Use null if the clip already opens with its strongest moment.
- "hook": 3-8 word on-screen text that creates curiosity. No emojis.
- "title": punchy post title, max 70 characters.
- "description": 1-2 sentence caption that makes people comment.
- "hashtags": 3-6 relevant hashtags, each starting with #.
- "commentary_script": a voiceover the creator records over the clip, 40-70 words, first person, spoken style.
  It must add real value: an opinion, context the viewer needs, a breakdown, or a lesson. Not a recap.
- "score": 1-100, how likely it is to perform. Be strict.
- "reason": one sentence on why this moment works.

Write hook, title, description, hashtags and commentary_script in {lang}.

Answer with exactly this JSON shape:
{{"clips": [{{"start": 12.3, "end": 48.9, "cold_open": {{"start": 30.1, "end": 33.0}}, "hook": "...", "title": "...", "description": "...", "hashtags": ["#..."], "commentary_script": "...", "score": 87, "reason": "..."}}]}}

{material}"""

INSIGHT_PROMPT = """You analyse short-form clip performance for one channel and tell the creator what is working.

CHANNEL: {name} (niche: {niche})
Each line: views | likes | duration s | hook | title | posted hour | caption style | had voiceover | source

{rows}

Return JSON:
{{"summary": "2-3 sentences, plain words",
  "working": ["3-5 concrete patterns that perform, citing numbers"],
  "not_working": ["2-4 patterns that underperform"],
  "next_actions": ["3-5 specific things to do next week (content, hooks, length, posting times)"],
  "winning_hooks": ["up to 5 hooks or hook patterns worth reusing"],
  "best_hours": ["posting hours that did best, e.g. 19:00"]}}"""


def _format_transcript(segments, t0=None, t1=None) -> str:
    lines = []
    for s in segments:
        if t0 is not None and s["end"] < t0:
            continue
        if t1 is not None and s["start"] > t1:
            continue
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b == -1:
            raise
        return json.loads(text[a: b + 1])


def ask_json(prompt: str, max_tokens=8000) -> dict:
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=max_tokens, system=SYSTEM,
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return _parse_json(text)


def select_clips(transcript: dict, total: float, n: int, niche: str, brief: str, settings: dict,
                 signals_text: str = "", winners: list[str] | None = None) -> list[dict]:
    segs = transcript.get("segments", [])
    lang = (settings.get("commentary_language") or "").strip() or "the same language as the transcript (English if there is no speech)"
    angle = (settings.get("angle") or "").strip()
    angle_block = f"Channel angle: {angle}\n" if angle else ""
    winners_block = ""
    if winners:
        winners_block = ("Hooks/patterns that already performed well on this channel (lean into similar moments):\n- "
                         + "\n- ".join(winners[:8]) + "\n")
    window = 45 * 60
    n_windows = max(1, math.ceil(total / window))
    per_window = n if n_windows == 1 else math.ceil(n / n_windows) + 1
    candidates = []
    for i in range(n_windows):
        t0, t1 = (i * window, (i + 1) * window) if n_windows > 1 else (None, None)
        text = _format_transcript(segs, t0, t1)
        material = "TRANSCRIPT:\n" + (text if text.strip() else "(no speech detected)")
        if signals_text:
            material += "\n\nSIGNALS:\n" + signals_text
        if not text.strip() and not signals_text:
            continue
        data = ask_json(PROMPT.format(
            signal_note=" plus audio/action signals" if signals_text else "",
            niche=niche, brief=brief, angle_block=angle_block, winners_block=winners_block,
            n=per_window, min_s=settings.get("min_s", config.MIN_CLIP_SECONDS),
            max_s=settings.get("max_s", config.MAX_CLIP_SECONDS), lang=lang, material=material,
        ))
        candidates.extend(data.get("clips", []))
    return candidates


def insights(name: str, niche: str, rows: list[str]) -> dict:
    return ask_json(INSIGHT_PROMPT.format(name=name, niche=niche, rows="\n".join(rows)), max_tokens=3000)
