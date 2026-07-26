# TikTok Automated Posting Pipeline

Clip prep → queue → post, built against TikTok's Content Posting API.

**Read the compliance summary before you build on this.** The headline finding
is that the fully-automated public-posting path you asked about is very likely
to fail TikTok's audit — not because of your content, but because of what the
audit is structurally designed to approve. The pipeline here is built so the
same code works whether you clear the audit or fall back to the semi-automated
path.

---

## 1. Go / No-Go Compliance Summary

### Verdict: NO-GO for fully-automated public posting. GO for semi-automated.

Three independent findings, in order of how badly they bite:

#### 1. Unaudited is harsher than "posts as draft"

The common description — "unaudited apps post as private/draft" — understates
it. Unaudited clients are subject to **all** of:

| Restriction | Effect |
|---|---|
| Content forced to `SELF_ONLY` | Only you can see the post |
| Posting accounts must be **set to private** at post time | Your whole account is private while testing |
| Max 5 users per 24h | Fine for solo use |
| 24h active-creator cap | Set from your audit application's usage estimate |

The account-private requirement is the one that surprises people. You cannot
run an unaudited client against a public account and quietly collect private
drafts — TikTok checks account visibility at post time. Making content public
later is a **manual, per-video** action: flip the account to public, then change
each post's privacy individually. There is no bulk unlock and no API for it.

#### 2. The audit is built for multi-user platforms, not solo auto-posters

This is the structural problem. TikTok's UX guidelines require an app to show,
before each post:

- a consent line — *"By posting, you agree to TikTok's Music Usage Confirmation"*
- the creator's nickname, fetched live from `creator_info`
- **editable** preset text — the user must be able to change any pre-filled caption
- commercial-disclosure toggles ("Promotional content" / "Paid partnership"), defaulting OFF
- live processing status after upload

Every item presumes **a human reviewing each post in a UI**. The stated
principle is that users must have "full awareness and control of what is being
posted to their TikTok accounts."

A headless cron job that posts on a schedule has no UI to submit for review. In
practice reviewers ask for screenshots or a video walkthrough of these screens.
The most common rejection reason reported by tools like Postiz and Mixpost is
verbatim *"Your application did not follow our UX Guidelines."* Being a solo
developer posting only to your own account doesn't exempt you — it just means
you have nothing to show the reviewer.

#### 3. Repurposed/clipped content is named in the prohibited list

The guidelines require apps that let "authentic creators post original content"
and explicitly prohibit *"an app that copies arbitrary contents from other
platforms to TikTok."*

Your constraint that you own or license the source material is genuinely
important and keeps you clear of copyright strikes — but the audit criterion
here is about **app behavior**, not content rights. An app whose described
purpose is "automatically repurpose clips and post them" pattern-matches to the
prohibited category regardless of who owns the footage. Original AI-generated
"brainrot" content is on better footing for *this specific* criterion, though
findings 1 and 2 still apply.

### What this means practically

| Your goal | Verdict |
|---|---|
| Auto-post to your own account, private, for testing | ✅ Works today, no audit |
| Auto-post **publicly**, no human in the loop | ❌ Audit very likely rejected |
| Auto-prep + queue + one manual tap to publish | ✅ **Recommended** — §5 Option A |
| Auto-post publicly via an approved third party | ✅ Works — §5 Option B |
| Build a real multi-creator product with a posting UI | ✅ Audit is passable if you build the UI |

**Recommendation:** don't spend weeks on an audit that is likely to be rejected
for reasons unrelated to your content quality. Build the prep and queue stages
— which is where the actual leverage is — and use TikTok Studio's native
scheduler or an approved third party for the final publish. §5 covers both.

### Automation risk to the account

Separate from the API question. What actually triggers suppression:

- **Volume.** More than ~3–5 posts/day from one account reads as spam. Defaults
  here cap at 5/day with 45 minutes minimum spacing.
- **Near-duplicate uploads.** Re-posting visually identical content with a new
  caption is detected and suppressed. Vary hooks, framing, and audio.
- **Metronomic timing.** Posting at exactly `:00` every 4 hours is a bot
  signature. The scheduler jitters within its posting hours.
- **Undisclosed AI content.** Set `is_aigc=True` for synthetic content. The
  label costs you far less reach than a takedown for undisclosed AI does.
- **Third-party posting tools that aren't API partners.** Anything driving the
  TikTok app via browser automation or an unofficial mobile API violates the
  ToS and is the single fastest route to a ban. Avoid entirely.

### Personal vs Business account

**This choice is a trap in one specific direction — pick carefully.**

| | Personal / Creator | Business |
|---|---|---|
| Content Posting API | ✅ | ✅ |
| **Full commercial music library** | ✅ | ❌ **Commercial Sound Library only** |
| Trending sounds | ✅ | ❌ Mostly unavailable |
| Analytics API | Limited | Full |
| Link in bio | Needs 1k followers | ✅ |

**Switching to a Business account cuts you off from trending sounds.** For
short-form content that rides trending audio — brainrot content especially —
this is a serious reach penalty. Stay on a **Personal/Creator account** unless
you specifically need the commercial features. Nothing in this pipeline
requires Business.

---

## 2. TikTok API Setup

### Step 1 — Developer account

1. Register at [developers.tiktok.com](https://developers.tiktok.com/) with the
   TikTok account you'll post from.
2. Verify email. Organization applications may ask for business verification.

### Step 2 — Create the app

1. **Manage apps → Connect an app**.
2. Fill in name, description, category. Be accurate — the description is read
   during audit, and a mismatch between it and what the reviewer sees is an
   immediate rejection.
3. Add a **redirect URI**. It must match your login call byte for byte,
   trailing slash included. `https://localhost:8080/callback` is fine for
   development.

### Step 3 — Request scopes

| Scope | Purpose | Audited? |
|---|---|---|
| `user.info.basic` | open_id, nickname | No |
| `video.upload` | Upload to drafts/inbox | No |
| `video.publish` | **Direct post** | **Yes** |
| `video.list` | Read your own posts | No |

`video.publish` is the one gated behind audit. `video.upload` sends a video to
the user's TikTok inbox for them to finish manually — that's the mechanism
behind the recommended fallback in §5.

### Step 4 — Credentials

Copy the client key and secret into `.env`:

```bash
cp .env.example .env
```

### Step 5 — Authorize and check status

```bash
pip install -r requirements.txt
python -m tiktok_pipeline.cli login --account main --redirect-uri https://localhost:8080/callback
python -m tiktok_pipeline.cli check --account main
```

`check` calls `creator_info` and prints exactly what you're permitted to post.
If `Privacy options: ['SELF_ONLY']` comes back, you're unaudited or the account
is private — this is the definitive answer to "did my audit go through," not
the portal's status field.

### Step 6 — The audit (only worth doing if you're building a real UI)

Submit from the app dashboard with a demo video and screenshots of your posting
UI. Community-reported turnaround is roughly **2–6 weeks**, often with a
rejection-and-resubmit cycle. TikTok publishes no SLA.

Before submitting, confirm you have every item from §1 finding 2. Missing any
one of them is the standard rejection.

### Rate limits

TikTok's public rate-limit table covers only `/v2/user/info/`,
`/v2/video/query/` and `/v2/video/list/` at **600 requests/min**. Posting
endpoint limits are **not published** — TikTok directs you to support for
quota questions.

So the limits in `config.py` are deliberately conservative defaults, not
scraped values: 6 requests/min, 5 posts/day, 45 min between posts. The daily
figure is an editorial cap for account health, not an API constraint.

---

## 3. Clip Prep for TikTok Specs

### Output format

| Property | Value |
|---|---|
| Container | MP4 (also accepts MOV, WEBM) |
| Video codec | H.264, High profile, level 4.1 |
| Pixel format | **yuv420p** — a frequent silent-rejection cause |
| Resolution | 1080×1920 (9:16) |
| Frame rate | source preserved by default (23–60 accepted) |
| Audio | AAC, 128 kbps, 44.1 kHz, stereo |
| `-movflags` | **+faststart** — required for streaming playback |
| Max size | 4 GB |
| Max duration | 10 min via API |

```bash
python -m tiktok_pipeline.cli prep --src raw.mp4 --dst out.mp4 --srt captions.srt
```

Vertical conversion uses a **blurred fill** rather than black bars by default.
Black bars read as lazily reposted content and cost retention in the first
second — exactly where it's most expensive.

Frame rate is **preserved**, not resampled — 60 fps gameplay footage stays at
60. Pass `--fps 30` if you specifically want to resample.

> **The caption trap.** ffmpeg converts SRT → ASS with `PlayResX: 384,
> PlayResY: 288`, and libass interprets *every* geometry value — `FontSize`,
> `MarginV`, `Outline` — in that coordinate space before scaling to the video.
> Passing pixel values through `force_style` therefore misses by 1920/288 ≈
> **6.67×**: a 58px font renders ~387px tall and captions land several screen
> heights off-frame. Worst of all, ffmpeg **exits 0** — you get a valid video
> with no captions and no error. `srt_to_styled_ass()` rewrites `PlayRes` to
> the real frame size so values mean pixels, and `tests/test_prep.py` checks
> burned-in captions by comparing pixels rather than exit codes.

### Safe zones (1080×1920)

TikTok's own UI draws over your frame. Keep captions and anything load-bearing
inside these margins:

```
 ┌────────────────────────────┐  y=0
 │   ▓▓▓ TOP 220px ▓▓▓        │  status bar, For You tabs, search
 ├────────────────────────────┤  y=220
 │                     ▓▓▓▓▓  │
 │                     ▓▓▓▓▓  │  RIGHT 200px:
 │      SAFE ZONE      ▓▓▓▓▓  │  avatar, like, comment,
 │   captions go here  ▓▓▓▓▓  │  share, spinning disc
 │                     ▓▓▓▓▓  │
 ├────────────────────────────┤  y=1420
 │  ▓▓▓ BOTTOM 500px ▓▓▓      │  username, caption, sound
 └────────────────────────────┘  y=1920
```

Values carry deliberate margin. The UI shifts between app versions and A/B
tests, so hugging the exact boundary is how captions end up under the share
button next quarter. Captions anchor at ~62% of the safe height — clear of the
caption overlay, not fighting the subject's face.

### Hook and caption style

The first **1–3 seconds** decide the video. TikTok weights completion and
rewatch rate heavily, so the hook's only job is buying second four.

**Structural rules:**
- **No intros.** No logo, no "hey guys." Start mid-action on frame one.
- **Text hook on frame 1**, readable in under a second. Six words or fewer.
- **Visual motion in the first 500ms.** A static opening frame reads as an ad
  and gets swiped.
- **Loop the ending to the start** where possible — a seamless loop inflates
  rewatch rate, one of the strongest ranking signals available.
- **Aim for >70% completion.** A tight 15–25s clip usually beats a padded 60s
  one, because completion rate matters more than watch time.

**Hook patterns that hold up:**

| Pattern | Example |
|---|---|
| Open loop | "I didn't expect the third one" |
| Contradiction | "Stop doing this. It's wrong." |
| Specific numbers | "$4,812 in 9 days" |
| Direct address | "If you're doing X, watch this" |
| Mid-sentence cold open | "...and that's when it broke" |

**Caption styling** (matching what `prep.py` burns in): heavy sans (Arial
Black / Montserrat Bold), ~58px at 1080 wide, white with a 4px black outline
plus shadow so it survives any background, 3–5 words per line, word-level
timing where you can. Most feed viewing is muted — burned-in captions are load
bearing, not decorative. Burn them in yourself rather than relying on TikTok's
auto-captions, which can land in the overlay zone and get covered.

**Caption text (the post title):** 1–2 lines, front-loaded. 3–5 relevant
hashtags beats 15 generic ones. Skip `#fyp` — it does nothing.

---

## 4. Scheduling & Queue

```bash
# Draft to your TikTok inbox -- no audit, can be public after you tap post
python -m tiktok_pipeline.cli enqueue --account main --video out.mp4 --title "hook here" --mode inbox

# Or direct post (needs the audit for anything but SELF_ONLY)
python -m tiktok_pipeline.cli enqueue --account main --video out.mp4 --title "hook here" --mode direct --privacy SELF_ONLY

python -m tiktok_pipeline.cli work --once     # process one job
python -m tiktok_pipeline.cli work            # run continuously
python -m tiktok_pipeline.cli status
```

### Two posting modes

| | `--mode inbox` | `--mode direct` |
|---|---|---|
| Endpoint | `/v2/post/publish/inbox/video/init/` | `/v2/post/publish/video/init/` |
| Scope | `video.upload` | `video.publish` |
| Audit needed | **No** | Yes, for anything but `SELF_ONLY` |
| Can be public | **Yes**, once you tap post | Only after audit |
| Caption | You write it in the app | Sent via API |
| Human step | One tap | None |

`inbox` is the recommended path and the reason it works is precisely the manual
step: because a human reviews every post, TikTok doesn't gate it behind the
audit. It sends no `post_info` — TikTok rejects that field here, and the
creator sets the caption and visibility in TikTok's own editor. The caption you
pass is kept on the job anyway so it travels with the video, ready to paste.

### Design notes

**SQLite-backed, not in-memory.** A queue that loses state on restart re-posts
videos it already published — embarrassing, and a fast route to a spam flag.

**Idempotency keys.** Derived from file content + caption + account, so a crash
mid-publish can't produce a duplicate. Enqueueing the same post twice is a
no-op.

**Orphaned jobs go to `BLOCKED`, not `PENDING`.** A job that was in flight when
the process died may already have published. Auto-retrying it risks a duplicate,
so it's parked for you to check the account and decide.

**Terminal vs retryable errors.** Retrying a terminal error (rejected privacy
level, spam flag) wastes quota and looks like abuse. `errors.py` classifies
TikTok's codes; only transient ones retry, with full-jitter exponential
backoff. Without jitter, a batch that fails together retries in lockstep and
reproduces the burst that caused the failure.

**`creator_info` is queried before every post.** Not optional — the
`privacy_level` you send must be one TikTok returned, and an unaudited client
gets no public option. Validating locally turns the most common API failure
into a clear error before any bytes upload.

### Library use

```python
from pathlib import Path
from tiktok_pipeline import Settings, TikTokClient, PostQueue

settings = Settings.from_env()
queue = PostQueue(settings)

# Let the account's real permissions pick the route: direct post if the audit
# has cleared, otherwise the inbox draft, which reaches public either way.
info = TikTokClient(settings).query_creator_info("main")

if info.can_post_publicly:
    queue.enqueue(
        account="main",
        video_path=Path("out/clip_01.mp4"),
        title="the hook goes here #niche",
        mode="direct",
        privacy_level="PUBLIC_TO_EVERYONE",
        is_aigc=True,      # disclose synthetic content
    )
else:
    queue.enqueue(
        account="main",
        video_path=Path("out/clip_01.mp4"),
        title="the hook goes here #niche",   # kept for you to paste in-app
        mode="inbox",
        is_aigc=True,
    )
```

### Posting times

Defaults are 07:00, 12:00, 17:00, 20:00, 22:00 local. Each slot opens at a
pseudo-random offset of up to 45 minutes into the hour, so posts land at
17:23 one day and 17:08 the next rather than on the hour every day — posting at
exactly `:00` daily is a bot signature. The offset is derived by hashing the
date and hour, so it is stable across restarts (a crash can't reroll it into
posting early) but differs day to day. Set `jitter_minutes=0` to disable.

These hours are generic starting points — **replace them with your own
analytics within two weeks.** Your audience's timezone distribution matters far
more than any published "best time to post" table. TikTok Studio → Analytics →
Followers shows when yours are active.

---

## 5. Fallback Plans

All three keep you inside the ToS. Ranked by what I'd actually do.

### Option A — Semi-automated: auto-prep, manual final tap ★ Recommended

Automate everything up to publishing; a human taps post.

**Route 1 — `video.upload` scope (no audit needed). Implemented here as
`--mode inbox`.** Uploads land in the creator's TikTok inbox as drafts. You open
the app, review, tap publish. Full public visibility, no audit, fully
ToS-compliant. This is the closest legitimate thing to what you asked for, and
it's the reason `video.upload` is worth requesting alongside `video.publish`.

**Route 2 — TikTok Studio native scheduler (free).** Schedule up to 10 days
ahead from [tiktokstudio.com](https://www.tiktok.com/tiktokstudio) on desktop
web. No API, no audit, no third party, zero cost. Your prep pipeline renders
the clips and you batch-upload weekly.

Cost: **$0**. Keeps the 90% of the work that's actually tedious (rendering,
captions, safe zones, queue ordering) automated.

### Option B — Approved third-party scheduler

These already hold audited API access, so you inherit it. All within budget:

| Tool | Free tier | Paid | Notes |
|---|---|---|---|
| **Buffer** | 20 posts/mo, 1 channel | ~$6/channel/mo | Official TikTok Marketing Partner |
| **Metricool** | 50 posts/mo, 1 brand | $22/mo unlimited | Best free tier; good analytics |
| **Later** | Limited | ~$25/mo | Official partner, strong visual planner |
| **Postiz** | Unlimited self-hosted | $0 | Open source; you supply the server |

**Metricool's free tier** (50 posts/month, ~1.6/day) fits the recommended
posting cadence with room to spare. Start there; it costs nothing to test.

Most expose an API or accept a watch-folder, so your prep pipeline can feed
them and they handle the publish leg.

### Option C — Build the real UI and pass the audit

Only worth it if you're building a product for other creators. Implement every
UX requirement from §1 finding 2, submit with a walkthrough video, expect 2–6
weeks and at least one rejection cycle. For a solo poster this is weeks of work
to replace one tap.

### Not an option

Browser automation (Selenium/Puppeteer driving tiktok.com), unofficial mobile
API clients, or bought "auto-poster" tools that don't name their API partner
status. All violate the ToS, all risk a permanent ban, and detection has gotten
much better. The time saved is not worth the account.

---

## Project Layout

```
tiktok_pipeline/
├── config.py       Settings, API URLs, transfer limits
├── errors.py       Retryable vs terminal classification
├── auth.py         OAuth tokens with rotation-safe refresh
├── client.py       Content Posting API — creator_info, init, chunked upload, poll
├── ratelimit.py    Token bucket + posting-window gates
├── retry.py        Full-jitter exponential backoff
├── queue.py        SQLite queue, idempotency, worker
├── prep.py         ffmpeg: 9:16, safe zones, burned captions
└── cli.py          login / check / prep / enqueue / work / status
tests/
├── test_pipeline.py   queue, rate limits, chunking, validation  (fast)
├── test_api.py        API wire format against a stubbed transport (fast)
└── test_prep.py       real ffmpeg renders, pixel inspection      (~100s)
```

**Requires ffmpeg** on PATH for the prep stage (`apt-get install ffmpeg` /
`brew install ffmpeg`). The prep tests skip automatically when it's absent.

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

`test_pipeline.py` and `test_api.py` are pure logic and run in ~1s.
`test_prep.py` shells out to real ffmpeg and takes ~100s, because the only
honest way to verify that stage is to render video and inspect pixels.

`test_api.py` drives the client through a stubbed transport and asserts on wire
format — request bodies, `Content-Range` headers, poll sequencing, token
rotation. Testing those against the live API would need real credentials, would
post actual videos to a real account, and would exhaust the unaudited
5-posts-per-24h cap on every run.

```bash
python -m pytest tests/ -q                      # everything
python -m pytest tests/ -q --ignore=tests/test_prep.py   # fast subset
```

Set `TIKTOK_DRY_RUN=1` to exercise the whole flow — auth, validation, chunk
planning — without uploading anything.

---

## Sources

- [Content Posting API — Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started/)
- [Content Sharing Guidelines / UX requirements](https://developers.tiktok.com/doc/content-sharing-guidelines)
- [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide)
- [API rate limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit)
- [Mixpost — TikTok Direct Post audit notes](https://docs.mixpost.app/services/social/tik-tok/direct-post-audit/)
- [Postiz issue #1362 — audit rejection for UX compliance](https://github.com/gitroomhq/postiz-app/issues/1362)

API behavior and third-party pricing change often — verify against the live
docs before relying on any specific limit here. Checked July 2026.
