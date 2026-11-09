"""Speech-to-text with word-level timestamps.

Output format (saved as transcript.json):
{"language": "en", "segments": [{"start": 1.2, "end": 4.5, "text": "...",
  "words": [{"start": 1.2, "end": 1.5, "word": "Hello"}, ...]}]}
"""
import json
import math
import subprocess
from pathlib import Path

import requests

from . import config

_local_model = None


def _local(audio: Path, language: str | None) -> dict:
    global _local_model
    from faster_whisper import WhisperModel

    if _local_model is None:
        _local_model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = _local_model.transcribe(
        str(audio), language=language or None, word_timestamps=True, vad_filter=True
    )
    out = []
    for s in segments:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (s.words or [])
            if w.word.strip()
        ]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "words": words})
    return {"language": info.language, "segments": out}


def _duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _groq(audio: Path, language: str | None) -> dict:
    """Groq's hosted Whisper: ~1h of audio in well under a minute. Audio is sent in 20-min chunks."""
    if not config.GROQ_API_KEY:
        raise RuntimeError("TRANSCRIBE_PROVIDER=groq but GROQ_API_KEY is empty")
    total = _duration(audio)
    chunk = 20 * 60
    segments, lang = [], None
    for i in range(math.ceil(total / chunk)):
        offset = i * chunk
        part = audio.parent / f"groq_part_{i}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(offset), "-t", str(chunk), "-i", str(audio),
             "-ac", "1", "-ar", "16000", "-b:a", "48k", str(part)],
            check=True,
        )
        data = {"model": config.GROQ_MODEL, "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"]}
        if language:
            data["language"] = language
        with open(part, "rb") as f:
            r = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                data=data, files={"file": (part.name, f, "audio/mpeg")}, timeout=600,
            )
        part.unlink(missing_ok=True)
        if r.status_code != 200:
            raise RuntimeError(f"Groq transcription failed ({r.status_code}): {r.text[:300]}")
        res = r.json()
        lang = lang or res.get("language")
        words = [{"start": w["start"] + offset, "end": w["end"] + offset, "word": w["word"].strip()}
                 for w in res.get("words", []) if w["word"].strip()]
        wi = 0
        for s in res.get("segments", []):
            s_start, s_end = s["start"] + offset, s["end"] + offset
            seg_words = []
            while wi < len(words) and words[wi]["start"] < s_end - 0.01:
                seg_words.append(words[wi])
                wi += 1
            segments.append({"start": round(s_start, 3), "end": round(s_end, 3),
                             "text": s["text"].strip(), "words": seg_words})
        if wi < len(words) and segments:
            segments[-1]["words"].extend(words[wi:])
    return {"language": lang, "segments": segments}


def transcribe(audio: Path, out_json: Path, language: str | None = None) -> dict:
    if config.TRANSCRIBE_PROVIDER == "groq":
        result = _groq(audio, language)
    else:
        result = _local(audio, language)
    out_json.write_text(json.dumps(result, ensure_ascii=False))
    return result
