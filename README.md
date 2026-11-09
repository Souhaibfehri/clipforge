# ClipForge

Your own clipping operation on your own server. Run several niche channels (anime, sports, comedy, movies,
money/motivation, podcasts), turn long videos into edited vertical clips, add your voiceover, post on a schedule,
and see what actually gets views.

## What it does

**Per channel** (each channel = its own accounts, style, sources, posting times, stats):
- **Niche presets** that set the editing: talking content gets face tracking, jump cuts and zooms; anime, sports and movies get a blurred "fit" layout and audio/action-based moment detection.
- **Claude picks the moments** from the transcript plus **loud-moment** (crowd, laughter, impacts) and **fast-cut action** signals, and writes the hook, title, caption, hashtags and a commentary script for your voiceover.
- **Learning loop**: Claude's insights on your best clips ("winning hooks") are fed back into future picks for that channel.

**Pro editing**
- Face tracking with camera cuts (YuNet neural face detector), **split-screen** when two people talk
- **Jump cuts**: pauses and filler words (um, uh, euh) removed, captions stay in sync
- **Cold open**: the best 2-4 seconds play first as a teaser, then the clip
- **Zoom punch-ins** on sentence changes, **progress bar**, **CTA** at the end
- Caption styles: bold yellow, big green karaoke, white box, minimal, none
- Your **voiceover** recorded on the phone while the clip plays; original audio auto-ducked under your voice

**Volume engine**
- Paste many links at once (batch)
- **Watch sources**: give a channel YouTube channels/playlists and new uploads get clipped automatically

**Posting** (via Upload-Post)
- Connect TikTok / Instagram / YouTube per channel from the app
- Auto-schedule clips into daily time slots (optionally only after you've added your voiceover)
- Or post yourself and paste the link: views get tracked either way

**Stats**
- Views tracked per post at decaying intervals (every 3h for 2 days, then 12h, then 48h until day 30)
- Dashboard: total views, views/day chart, earnings estimate from your campaign CPM, budget used
- "What works": average views by clip length, posting hour, platform, voiceover vs none, caption style
- Claude insights: what's working, what isn't, what to do next week

---

## Deploy on your Hostinger VPS

You need: the VPS IP and root password (hPanel > VPS > Overview), an Anthropic API key, ideally a domain.

**1. Copy the app to the VPS** (from the folder where you unzipped it):
```bash
scp -r clipforge root@YOUR_VPS_IP:/opt/clipforge
```

**2. Install Docker** (skip if you picked Hostinger's Docker template):
```bash
ssh root@YOUR_VPS_IP
curl -fsSL https://get.docker.com | sh
```

**3. Configure:**
```bash
cd /opt/clipforge
cp .env.example .env
nano .env      # set ANTHROPIC_API_KEY, APP_PASSWORD, DOMAIN (and UPLOADPOST_API_KEY / GROQ_API_KEY if you have them)
```

**4. Point your domain**: in hPanel > Domains > DNS add an **A record** `clips` → `YOUR_VPS_IP`.

**5. Start with HTTPS:**
```bash
docker compose --profile https up -d --build
```
Open `https://clips.yourdomain.com`, user `admin`, your `APP_PASSWORD`.
(No domain yet? `docker compose up -d --build` and open `http://YOUR_VPS_IP:8000`. Recording from the phone needs HTTPS, but uploading a voice memo works.)

**6. Lock it down** once HTTPS works: in `docker-compose.yml` change `"8000:8000"` to `"127.0.0.1:8000:8000"`, then `docker compose --profile https up -d`.

## First hour

1. **Channels → New channel** for each niche you want to test (e.g. "Anime Peaks" / Anime, "Money Moves" / Money).
2. In each channel's **Settings**: write the angle, pick caption style, set time slots, and if it's a paid campaign enter the **$ per 1,000 views** and budget. Click **Connect TikTok / Instagram / YouTube** if you have Upload-Post.
3. **New** → pick the channel → paste a few links → Start.
4. Open a finished job: watch the clips, **Record** your commentary using the script as a teleprompter, then **Post** (schedule) or download and post yourself and paste the link.
5. After ~5 posts per channel, open **What works** → **Generate insights**.

## Speed and costs

| | Default | Faster / notes |
|---|---|---|
| Transcription | Local Whisper on the VPS, roughly real time on 2 vCPU | `TRANSCRIBE_PROVIDER=groq` + key: ~1 min per hour of video |
| Claude | a few cents per video | |
| Rendering | 15-60 s per clip (zoom and cold open add time) | `X264_PRESET=ultrafast` |
| Upload-Post | free plan 10 uploads/month | paid plan for daily posting |

Jobs run one at a time. Delete old jobs to free disk space.

## Troubleshooting
- **YouTube "Sign in to confirm you're not a bot"**: see `YTDLP_COOKIES` in `.env`, or upload the file directly.
- **Views not updating**: Instagram often needs cookies for view counts; TikTok and YouTube usually work as is. Use ↻ on a post to retry.
- **Logs**: `docker compose logs -f app`
- **Update**: replace the files, then `docker compose --profile https up -d --build`. Your data in `./data` is kept.

## Rules of the game
Anime, sports, movie and TV footage belongs to studios and leagues: expect Content ID claims and takedowns, and
keep each channel's account separate. Paid clipping campaigns (the creator wants clips) and podcasts that allow
clipping are the safest sources. Always add your own commentary. Platforms demonetize and ban repost farms.

## Files
```
app/main.py        API              app/pipeline.py  job flow (download > transcript > signals > Claude > render)
app/db.py          SQLite storage   app/presets.py   niche presets (tweak defaults here)
app/llm.py         Claude prompts   app/edit.py      jump cuts, cold open, zoom planning
app/video.py       ffmpeg/OpenCV    app/captions.py  caption styles
app/signals.py     audio peaks + action detection
app/poster.py      Upload-Post posting + time slots
app/tracker.py     view tracking, stats, insights
app/watcher.py     auto-clip new uploads from sources
app/static/index.html   the web app
```
