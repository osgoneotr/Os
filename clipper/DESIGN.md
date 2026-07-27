# Short-form clip pipeline — design and build plan

Four stages: **Discovery → Clipping → Editing → Output**. Stages 2–4 are built
and working in this repo (`clipper/`). Stage 1 is specified here but not built,
because it is the stage where platform policy — not engineering — decides what
is possible.

---

## 0. The constraint that shapes the whole design

Discovery and clipping are usually imagined as one flow: *find a trending video
→ clip it → post it*. That flow is not one you can build on. Downloading another
account's video to re-edit and re-post is against the ToS of all three platforms
and is a copyright problem regardless of ToS; the official APIs deliberately
expose no video-file endpoint, so there is no legitimate path to the bytes.

So the pipeline splits the two ideas apart:

- **Discovery answers "what should I make?"** — which topics, formats, hooks,
  lengths and hashtags are working right now. It returns *signal*, not media.
- **Clipping operates on footage you own or have licensed** — your podcast,
  stream, webinar, interview, course recording.

That split is why this repo starts at stage 2: it is the stage that is both
tractable and unambiguously yours to build. It also means the discovery stage,
when built, feeds *config* (niche keywords, hashtag sets, target durations) into
the clipper rather than feeding it video.

---

## 1. Discovery — recommended stack, and where the APIs stop

| Platform | Official API for trending? | Reality | Recommendation |
|---|---|---|---|
| **YouTube Shorts** | **Yes** | Data API v3 `videos.list?chart=mostPopular` with `regionCode` + `videoCategoryId`. Free, 10k quota units/day. Shorts are not a filter — fetch `contentDetails` and keep `duration ≤ 60s`. `search.list`'s `videoDuration=short` means ≤4 min, not ≤60s, and costs 100 units per call. | **Build on this.** It is the only genuinely open trending feed of the three. |
| **TikTok** | **Restricted** | Three separate APIs, none of which fit: **Display API** returns only the authenticated user's own videos; **Research API** covers public video search but is gated to academic/non-profit researchers in the US/EU by application; **Commercial Content API** covers ads, not organic. The Creative Center trending pages are public but have no documented API. | Apply for the Research API if eligible. Otherwise use a licensed data vendor, or seed manually — see below. |
| **Instagram Reels** | **Restricted** | Instagram Graph API (Business/Creator account + linked Facebook Page) gives your own media and insights. The closest public signal is **hashtag search**: `ig_hashtag_search` → `top_media`/`recent_media`, **capped at 30 unique hashtags per rolling 7 days per user**. There is no trending-Reels endpoint. | Use hashtag `top_media` as a topic-level signal. Budget those 30 hashtag slots deliberately. |

**Stack:** Python, `httpx` (async, so three providers fan out concurrently),
`tenacity` for backoff, SQLite via SQLModel for the observation store, one
`TrendProvider` protocol per platform so a vendor API can drop in behind the
same interface.

**Design point that matters more than the API choice:** store *observations over
time*, not snapshots. A video at 2M views tells you nothing; a video that went
0 → 2M in 18 hours tells you everything. Poll on a schedule, store
`(video_id, observed_at, views, likes, comments)`, and rank on **velocity**
(`Δviews/Δt`, normalized by channel size) rather than absolute counts. This is
the whole value of the stage, and it is why a one-shot scrape is not a
substitute.

The 30-hashtag/7-day Instagram cap makes hashtags a scarce resource to be
allocated, not a query parameter. Plan for a rotating set tied to your niche.

**Honest fallback:** for a solo operator in one niche, a manually curated seed
list of 20–50 accounts, polled for their recent uploads via the APIs above,
outperforms a generic "trending" feed — you get signal from your actual
competitive set instead of from whatever is globally viral.

---

## 2. Clipping — built (`clipper/score/`)

| Concern | Choice | Why |
|---|---|---|
| Transcription | **faster-whisper** (CTranslate2), pluggable | Offline, free per-minute, and returns **word-level timestamps** — a hard requirement, since they drive both cut-point snapping and caption highlighting. `small` runs ~3–6× realtime on CPU; `large-v3` is realtime on a CUDA GPU. Hosted Whisper backend included for machines that can't host a model. |
| Audio features | **ffmpeg pipe → numpy** | RMS envelope is the only acoustic signal the scorer needs. Doesn't justify librosa's dependency tree. |
| Segmentation | Sentence/pause **units**, not fixed windows | Candidates only begin and end on unit boundaries, so no clip can start mid-word — the classic sliding-window failure. |
| Ranking | Weighted linear model over 9 normalized features | Transparent, tunable from a JSON file, and every score is explainable in the output sidecar. |
| De-duplication | Greedy NMS on IoU | Without it, the top-5 is five near-identical windows sliding one sentence at a time over the same moment. |

**The nine features** (`clipper/score/features.py`), all normalized to ~[0,1] so
the configured weights are directly comparable:

`hook` (strength of the first 3s — the only part that decides whether anyone
sees the rest) · `keyword` (hook lexicon + your niche terms, per 100 words) ·
`energy` and `dynamics` (loudness and its variation, **relative to the video's
own average**, so a quietly-mixed source scores the same as a loud one) ·
`pacing` (WPM against a natural band) · `density` (inverse dead-air) ·
`completeness` (starts/ends on real boundaries, weighted toward the ending) ·
`arc` (question early, payoff later) · `length` (soft preference for target).

This is a **defensible heuristic, not a model of virality.** It reliably beats
"cut every 30 seconds," and it is designed to be replaced: once you have
retention data on ~100 posted clips, these nine features become the feature
vector for a learned ranker (v3 below).

---

## 3. Editing — built (`clipper/edit/`)

**ffmpeg via subprocess with explicit filter graphs — deliberately not MoviePy.**
One invocation per clip means one encode instead of MoviePy's decode/re-encode
round trips, and the failing command is printable and pasteable when a render
misbehaves. `--dry-run` prints it.

Order is load-bearing:

1. **Trim on the input side** (`-ss`/`-t` before `-i`) so ffmpeg seeks instead
   of decoding from zero — minutes per clip on a 2-hour source.
2. **Reframe to 1080×1920 before burning captions**, so the ASS `PlayRes`
   matches the final canvas and font sizes mean what they say.
3. **Branding overlay last**, above the captions.
4. **Loudness-normalize the voice before mixing music**, so the ducking
   threshold behaves identically on every source.

**Captions: ASS, not SRT.** ASS gives heavy outlines, exact placement in the
safe area, and per-word colour changes — the "TikTok-native" look. Karaoke mode
emits one event per word: the full line stays up while the active word
recolours. Default `margin_v` clears the platform bottom UI.

**Reframe modes:** `center` (static crop) · `blur_pad` (full frame over a
blurred zoomed copy — correct for screen shares and two-shots) · `track`
(OpenCV face/motion tracking → smoothed path → a **flat sum of gated ramps** as
the crop's `x` expression, rather than nested `if()`, which has no nesting depth
to blow up on a 60s clip). `track` degrades to `center` with a warning if
OpenCV is absent, rather than failing a render.

**Audio:** `loudnorm` to −14 LUFS (roughly where the platforms normalize, so
clips aren't turned down on playback); optional music bed looped with
`-stream_loop` and ducked under the voice via `sidechaincompress`; `amix` with
`normalize=0` so mixing music doesn't halve the voice.

---

## 4. Output — built (`clipper/output/`)

Per clip: `NN-slug.mp4`, a `.json` sidecar (candidate, all nine feature values,
metadata), a `.md` review card (caption, hashtags, **why it scored well**,
transcript), the `.ass` for restyling without re-transcribing, and a top-level
`index.md` ranking every clip.

Copy generation is heuristic by default (keyword extraction + the hook line as
caption opener) so the tool runs at $0. An opt-in Claude generator writes better
copy; if it fails, it falls back to the heuristic rather than losing a finished
render.

**No auto-posting, by design** — and note that this isn't only a preference:
neither TikTok nor Instagram exposes a general-purpose organic publishing API to
ordinary developers, so "review then post manually" is also the only reliable
path.

---

## Phased build plan

### MVP — *shipped in this repo*

Simplest end-to-end version: **one local video file → N ranked, captioned,
vertical clips + suggested copy.** No accounts, no API keys, no network.

```
clipper clip talk.mp4 --top 5
```

Everything in stages 2–4 above: transcription with caching, unit-based candidate
generation, the 9-feature scorer with NMS, three reframe modes, word-highlight
captions, music/branding, review sidecars. 83 tests, 11 of which render real
video through real ffmpeg.

**Deliberately deferred:** discovery, any platform account, any hosted service.

### v2 — discovery feeds the clipper

1. **YouTube first** (the only open trending API). `TrendProvider` protocol +
   SQLite observation store + velocity ranking.
2. **Instagram hashtag `top_media`** as a topic signal, with a deliberate
   rotation plan for the 30-hashtag/7-day budget.
3. **TikTok**: Research API if eligible, else a licensed vendor, else the
   curated-seed-accounts fallback.
4. **Close the loop:** a trend report emits niche keywords and hashtag sets
   *into the clipper's config*, so scoring and copy follow what is currently
   working. This is the actual integration point between stages 1 and 2 — not
   video.
5. Ergonomics: batch mode over a folder, a proper preview UI (a local web page
   beats scrubbing mp4s), and `yt-dlp` ingest **restricted to your own channel**.

### v3 — learn from results

1. **Outcome logging.** Record what you posted and its retention/views. This is
   the unlock; nothing else in v3 works without it.
2. **Learned ranker.** The nine features become a feature vector; train a
   gradient-boosted ranker on your own retention data. Keep the heuristic as the
   cold-start path and as a baseline to beat — if the learned model can't beat
   it on held-out clips, it isn't ready.
3. **Better reframing.** Replace Haar cascades with a proper detector
   (YOLO/MediaPipe) plus active-speaker detection for multi-person footage.
4. **Semantic segmentation.** Embed sentences and cut on topic boundaries rather
   than pauses; use an LLM to pick the strongest self-contained thought and to
   write per-platform copy variants.
5. **Scale-out.** A job queue and GPU worker if volume justifies it — this is
   the last thing to build, not the first.

---

## Open questions that would change these recommendations

- **Platform priority.** YouTube-only makes discovery ~5× simpler (real API vs.
  application or vendor). TikTok-first means budgeting for the Research API
  application or a vendor from day one.
- **Volume.** A handful of long videos a week is a laptop CPU job. Dozens a day
  changes transcription (GPU `large-v3`), storage, and the case for a queue.
- **Niche.** Drives the keyword lexicon and hashtag sets, and it changes reframe
  defaults: talking-head → `track`; screen-share/tutorial → `blur_pad`;
  centred-studio → `center`.
- **Source footage.** Whether it's your own long-form content (the assumption
  here) or licensed/stock material changes the ingest path.
