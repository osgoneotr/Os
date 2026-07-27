# clipper

Turn a long video you own into ranked, captioned, vertical short-form clips —
with suggested captions and hashtags for you to review and post manually.

Nothing is uploaded anywhere. The tool writes files.

See [DESIGN.md](DESIGN.md) for the full four-stage design (including the
discovery stage that is specified but not yet built) and the phased plan.

```
clipper clip podcast.mp4 --top 5
```

```
out/podcast/
├── index.md                     ← ranked review page, start here
├── 01-heres-why-most-people.mp4 ← 1080x1920, captions burned in
├── 01-heres-why-most-people.md  ← caption, hashtags, why it scored, transcript
├── 01-heres-why-most-people.json
├── 01-heres-why-most-people.ass ← restyle captions without re-transcribing
└── 02-...
```

## Install

```bash
pip install -e '.[whisper]'     # local transcription (recommended)
pip install -e '.[whisper,track,ffmpeg]'   # + subject tracking + bundled ffmpeg
clipper doctor                  # check what's available
```

ffmpeg must be on `PATH` (`apt install ffmpeg` / `brew install ffmpeg`), or
install the `ffmpeg` extra for a bundled static build, or set `FFMPEG_BINARY`.

Optional extras: `whisper` (local ASR) · `openai` (hosted ASR) · `track`
(OpenCV subject tracking) · `llm` (Claude-written copy) · `ffmpeg` (bundled
binary) · `dev` (tests).

## Commands

| Command | What it does |
|---|---|
| `clipper clip SOURCE` | The whole pipeline: transcribe → score → render → write sidecars. |
| `clipper score SOURCE` | Rank segments **without rendering**. Seconds instead of minutes — this is the loop you tune in. |
| `clipper transcribe SOURCE` | Transcribe only; writes reusable JSON. |
| `clipper render SOURCE --start --end` | Render one hand-picked range, for when the scorer gets a cut wrong. |
| `clipper doctor` | Report which binaries and optional extras are present. |
| `clipper init-config` | Write a config file with every default filled in. |

Useful flags: `--top/-n`, `--min`/`--max` (seconds), `--reframe`, `--music`,
`--logo`, `--no-captions`, `--dry-run` (prints the exact ffmpeg command),
`--config/-c`, `--verbose/-v`.

## How segments are chosen

The transcript is split into **units** at sentence ends and pauses; every
contiguous run of units that fits the duration bounds becomes a candidate. Since
candidates only start and end on unit boundaries, no clip can begin mid-word.

Each candidate is scored on nine normalized features, combined with the weights
in your config:

| feature | what it measures |
|---|---|
| `hook` | strength of the first ~3 seconds — the part that decides whether anyone sees the rest |
| `keyword` | hook-lexicon and niche-term density, per 100 words |
| `energy` | loudness **relative to this video's own average** |
| `dynamics` | variation in loudness — flat delivery scores low |
| `pacing` | words per minute against a natural band |
| `density` | inverse of dead air |
| `completeness` | starts and ends on real boundaries, weighted toward the ending |
| `arc` | question or tension early, payoff later |
| `length` | closeness to your target duration |

Overlapping candidates are then suppressed so the top-N are genuinely different
moments, not one moment sampled five times.

Every score is explained in the clip's `.md` card. To change the ranking, edit
the weights — `clipper init-config`, then iterate with `clipper score`.

This is a tunable heuristic, not a model of virality. It beats cutting on a
fixed interval, and it is built to be replaced by a learned ranker once you have
retention data (see DESIGN.md, v3).

## Reframing

| mode | use for |
|---|---|
| `track` **(default)** | talking heads and interviews; follows the face (or motion) with a smoothed crop path |
| `blur_pad` | screen shares, b-roll, two-shots — keeps the whole frame over a blurred background |
| `center` | anything already framed centrally; cheapest |

The default is `track`, which needs the `track` extra — **install it**
(`pip install -e '.[whisper,track]'`) or every render warns and falls back to
`center`. In a two-shot it follows the largest face, which is usually but not
always the active speaker.

## Throughput

Defaults suit a few hours of footage a week on a laptop. For several hours a
day, set `transcribe.device` to a CUDA GPU (`auto` already detects one and
picks `float16`) and raise `transcribe.model` to `large-v3` — realtime on a
GPU, and more accurate on proper nouns, which matters because ASR errors land
directly in burned-in captions. See DESIGN.md v2 for the queue/worker plan.

## Configuration

```bash
clipper init-config -o clipper.json
clipper clip podcast.mp4 -c clipper.json
```

Worth setting for your niche:

- `scoring.weights` — the ranking, and the main thing you will tune
- `scoring.niche_keywords` — terms that matter in your subject area
- `scoring.min_duration` / `max_duration` / `target_duration`
- `captions.*` — font, size, colours, `margin_v` (keeps captions clear of the
  platform UI), `uppercase`, `karaoke`
- `output.base_hashtags` — always-on tags for your account
- `render.music_path`, `render.logo_path`

## Notes

- **Transcription is cached** against the source file's size, mtime and ASR
  settings, so re-running after a weight change is fast. Cache lives in
  `.cache/`.
- **Bring your own footage.** This is built for long-form content you own or
  have licensed — podcasts, streams, interviews, webinars.
- Loudness is normalized to −14 LUFS, roughly where the platforms normalize.
- `--dry-run` does everything except encode and prints the ffmpeg command.

## Tests

```bash
pip install -e '.[dev]' && pytest
```

83 tests. Most are pure-logic and fast; 11 render real video through real ffmpeg
end to end (skipped automatically if ffmpeg is unavailable).
