# Quickstart

Get from nothing to a queued video. One-time setup is steps 1–5; after that
your daily use is step 6.

See [README.md](README.md) for why this uses the draft route rather than fully
automatic posting.

---

## 1. Get the code running

```bash
git clone <your-repo-url>
cd Os
git checkout claude/tiktok-posting-pipeline-2hkg7f

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Install ffmpeg** — the clip prep won't run without it:

| OS | Command |
|---|---|
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt update && sudo apt install ffmpeg` |
| Windows | `winget install ffmpeg` |

Check it worked:

```bash
ffmpeg -version
python -m pytest tests/ -q --ignore=tests/test_prep.py
```

## 2. Create your TikTok app

1. Go to [developers.tiktok.com](https://developers.tiktok.com/) and log in with
   **the TikTok account you'll post from**.
2. **Manage apps → Connect an app**. Name it, describe it honestly.
3. Under **Products**, add **Content Posting API**.
4. Under **Scopes**, add:
   - `user.info.basic`
   - `video.upload`

   Do **not** add `video.publish` yet. It's the audited one, and requesting a
   scope your app doesn't hold makes login fail outright.
5. Under **Redirect URI**, add exactly:

   ```
   https://localhost:8080/callback
   ```

   Nothing needs to run there — see step 4 for why.

Your account can stay a **Personal/Creator account**. Don't switch to Business:
it loses access to trending sounds.

## 3. Add your credentials

From your app's **Credentials** page, copy the client key and secret:

```bash
cp .env.example .env
```

Edit `.env`:

```
TIKTOK_CLIENT_KEY=aw...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=https://localhost:8080/callback
```

Then load it into your shell:

```bash
export $(grep -v '^#' .env | xargs)
```

`.env` is gitignored. Don't commit it — it can post to your account.

## 4. Authorize

```bash
python -m tiktok_pipeline.cli login --account main
```

It prints a URL. Open it, approve access, and TikTok redirects you to
`https://localhost:8080/callback?code=...`

**Your browser will show an error page. That's expected** — nothing is running
on port 8080. The part you need is in the address bar. Copy the value between
`code=` and the next `&`, paste it at the prompt.

Then confirm what you're allowed to do:

```bash
python -m tiktok_pipeline.cli check --account main
```

This asks TikTok directly, which is more reliable than the portal's status
field. Expect `direct: SELF_ONLY only` and `inbox: always available` — that's
normal and is exactly the situation this tool is built for.

## 5. Test with one clip

```bash
mkdir -p raw out
cp ~/somewhere/yourclip.mp4 raw/

python -m tiktok_pipeline.cli prep --src raw/yourclip.mp4 --dst out/yourclip.mp4
```

Open `out/yourclip.mp4` and check it looks right: 9:16, nothing important
hidden behind where TikTok's buttons sit.

Do a dry run before anything real is uploaded:

```bash
TIKTOK_DRY_RUN=1 python -m tiktok_pipeline.cli enqueue \
  --account main --video out/yourclip.mp4 --title "test"
TIKTOK_DRY_RUN=1 python -m tiktok_pipeline.cli work --once
```

## 6. Daily use

Put your clips in `raw/`. Optionally alongside each one:

| File | Does |
|---|---|
| `clipA.srt` | captions, burned in inside the safe zone |
| `clipA.txt` | the caption text; otherwise the filename is used |

Then:

```bash
python -m tiktok_pipeline.cli batch --account main --src-dir raw/ --out-dir out/
python -m tiktok_pipeline.cli work
```

`batch` preps and queues everything. `work` uploads on schedule — by default at
07:00, 12:00, 17:00, 20:00 and 22:00 local, jittered, max 5 a day, at least 45
minutes apart.

Each upload arrives as a **notification in your TikTok app**. Open it, paste
your caption, tap post. That tap is what makes this legitimate without an audit.

Leave `work` running (or run `work --once` when you want a single post). Check
progress any time:

```bash
python -m tiktok_pipeline.cli status
```

Re-running `batch` is safe: prepped clips are reused and queued ones skipped, so
nothing is rendered or posted twice.

---

## Adjusting the schedule

Edit `Settings` in `tiktok_pipeline/config.py`:

```python
posting_hours = (7, 12, 17, 20, 22)   # local hours
```

```python
posts_per_day = 5                      # keep this low; volume is a spam signal
min_seconds_between_posts = 45 * 60
jitter_minutes = 45                    # 0 posts exactly on the hour
```

**Replace these hours with your own numbers within two weeks.** TikTok Studio →
Analytics → Followers shows when your audience is actually active, which beats
any generic table.

## When something breaks

| Symptom | Cause |
|---|---|
| `ffmpeg not found on PATH` | Step 1 — install ffmpeg |
| `TIKTOK_CLIENT_KEY ... must be set` | Re-run the `export` in step 3 |
| Login fails immediately | `redirect_uri` doesn't match the portal exactly — trailing slashes count |
| `scope_not_authorized` | Your app lacks `video.upload`; add it in the portal, log in again |
| `access_token_invalid` | Token expired past refresh — run `login` again |
| Job stuck `BLOCKED` | It failed for a reason retrying won't fix. `status` shows why |
| `Duration ... under the 3s minimum` | The clip is too short for TikTok |
| Nothing posts, no errors | Outside posting hours. `work --once` shows the reason |

Job states: `PENDING` → `IN_FLIGHT` → `PUBLISHED`. `FAILED` means retries were
exhausted; `BLOCKED` means it needs you to look at it.

## Before your first real upload

You've been through the dry run, so the remaining unknown is TikTok's live
response. Do the first real one with `work --once` and watch the output rather
than leaving `work` running unattended.
