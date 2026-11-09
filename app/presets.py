"""Niche presets. Each channel starts from one and can be tweaked in the UI."""

BASE = {
    "angle": "",
    "commentary_language": "",
    "layout": "auto",            # auto | face | split | fit
    "caption_style": "bold",     # bold | karaoke | boxed | minimal | none
    "num_clips": 5,
    "min_s": 20,
    "max_s": 60,
    "remove_silences": False,    # jump-cut pauses and filler words
    "punch_zoom": False,         # alternate 1.0x / 1.12x zoom on sentence starts
    "cold_open": True,           # flash the best line first, then the clip
    "progress_bar": True,
    "hashtags": "",              # always added to posts
    "cta": "",                   # added to captions, e.g. "Follow for part 2"
    "uploadpost_user": "",       # Upload-Post profile that holds this channel's accounts
    "platforms": ["tiktok", "instagram", "youtube"],
    "auto_post": False,          # schedule clips automatically into the time slots
    "require_voiceover": True,   # only auto-post clips that have your voiceover
    "slots": ["11:30", "17:30", "20:30"],
    "timezone": "America/Toronto",
    "cpm": 0.0,                  # $ per 1000 views (campaign rate) for earnings estimates
    "budget": 0.0,               # campaign budget cap, 0 = none
    "sources": [],               # YouTube channel / playlist URLs to watch
    "watch": False,
    "color": "#ffd23f",
}

PRESETS = {
    "podcast": {
        "label": "Podcast / interviews",
        "color": "#ffd23f",
        "layout": "auto", "caption_style": "bold", "remove_silences": True, "punch_zoom": True,
        "min_s": 25, "max_s": 60,
        "brief": "Talking content. Pick strong opinions, surprising facts, stories with a payoff, heated moments.",
        "hashtags": "#podcast #podcastclips",
    },
    "money": {
        "label": "Money / motivation",
        "color": "#4ade80",
        "layout": "auto", "caption_style": "karaoke", "remove_silences": True, "punch_zoom": True,
        "min_s": 20, "max_s": 50,
        "brief": "Money, business and mindset. Pick quotable lines, contrarian advice, concrete numbers, "
                 "tough-love motivation. Hooks should promise a result or call out a mistake.",
        "hashtags": "#money #motivation #mindset #business",
    },
    "comedy": {
        "label": "Comedy",
        "color": "#f472b6",
        "layout": "auto", "caption_style": "bold", "remove_silences": False, "punch_zoom": True,
        "min_s": 15, "max_s": 45,
        "brief": "Comedy. Each clip needs a clear setup and punchline; start close to the setup, end right after "
                 "the laugh. Prefer crowd-work, roasts, absurd stories. Use audio peaks (laughter) as signals.",
        "hashtags": "#comedy #funny #standup",
    },
    "sports": {
        "label": "Sports",
        "color": "#60a5fa",
        "layout": "fit", "caption_style": "boxed", "remove_silences": False, "punch_zoom": False,
        "cold_open": False, "min_s": 15, "max_s": 45,
        "brief": "Sports. Pick game-changing plays, goals, fights, big reactions, press-conference drama. "
                 "Crowd noise spikes (audio peaks) usually mark the big moments.",
        "hashtags": "#sports #highlights",
    },
    "anime": {
        "label": "Anime",
        "color": "#a78bfa",
        "layout": "fit", "caption_style": "boxed", "remove_silences": False, "punch_zoom": False,
        "cold_open": False, "min_s": 15, "max_s": 45,
        "brief": "Anime. Pick fight peaks, power-ups, emotional or iconic lines, plot twists. Fast scene cuts "
                 "plus loud audio usually mark action peaks.",
        "hashtags": "#anime #animeedit #animemoments",
    },
    "movies": {
        "label": "Movies / TV",
        "color": "#fb923c",
        "layout": "fit", "caption_style": "boxed", "remove_silences": False, "punch_zoom": False,
        "cold_open": False, "min_s": 20, "max_s": 60,
        "brief": "Movies and TV. Pick iconic scenes, twists, great dialogue, tense confrontations. "
                 "Each clip must make sense without having seen the film.",
        "hashtags": "#movies #moviescenes #film",
    },
}


def settings_for(niche: str, overrides: dict | None = None) -> dict:
    p = PRESETS.get(niche, PRESETS["podcast"])
    s = dict(BASE)
    s.update({k: v for k, v in p.items() if k not in ("label", "brief")})
    if overrides:
        s.update({k: v for k, v in overrides.items() if k in BASE})
    return s


def brief_for(niche: str) -> str:
    return PRESETS.get(niche, PRESETS["podcast"])["brief"]
