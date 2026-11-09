import os
from pathlib import Path


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
JOBS_DIR = DATA_DIR / "jobs"

# Brain
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# Transcription: "local" (faster-whisper on the VPS CPU) or "groq" (fast cloud API)
TRANSCRIBE_PROVIDER = os.getenv("TRANSCRIBE_PROVIDER", "local").lower()
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3-turbo")

# Clip rules
DEFAULT_NUM_CLIPS = _int("DEFAULT_NUM_CLIPS", 5)
MIN_CLIP_SECONDS = _int("MIN_CLIP_SECONDS", 20)
MAX_CLIP_SECONDS = _int("MAX_CLIP_SECONDS", 60)

# Downloads
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", "")  # path to a cookies.txt (helps when YouTube blocks the VPS)
MAX_HEIGHT = _int("MAX_HEIGHT", 1080)

# Posting (https://upload-post.com) — optional
UPLOADPOST_API_KEY = os.getenv("UPLOADPOST_API_KEY", "")

# Background loops
WATCH_EVERY_HOURS = float(os.getenv("WATCH_EVERY_HOURS", "3"))
STATS_EVERY_MINUTES = float(os.getenv("STATS_EVERY_MINUTES", "60"))

# Security
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
APP_USER = os.getenv("APP_USER", "admin")

# Rendering
OUT_W, OUT_H = 1080, 1920
X264_PRESET = os.getenv("X264_PRESET", "veryfast")
X264_CRF = os.getenv("X264_CRF", "21")
CAPTION_FONT = os.getenv("CAPTION_FONT", "DejaVu Sans")
