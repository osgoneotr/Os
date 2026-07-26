"""Command-line interface.

    python -m tiktok_pipeline.cli login    --account main
    python -m tiktok_pipeline.cli check    --account main
    python -m tiktok_pipeline.cli prep     --src raw.mp4 --dst out.mp4 --srt caps.srt
    python -m tiktok_pipeline.cli enqueue  --account main --video out.mp4 --title "..."
    python -m tiktok_pipeline.cli work     --once
    python -m tiktok_pipeline.cli status
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

from .auth import TokenStore, exchange_code
from .client import TikTokClient
from .config import REQUIRED_SCOPES, Settings
from .prep import DEFAULT_SAFE_ZONE, prepare_clip, validate_for_tiktok
from .queue import PostMode, PostQueue, QueueWorker

AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize/"


def cmd_login(args, settings: Settings) -> int:
    """Print the consent URL, then exchange the pasted code for tokens."""
    redirect_uri = args.redirect_uri
    state = secrets.token_urlsafe(16)
    params = {
        "client_key": settings.client_key,
        "scope": ",".join(REQUIRED_SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    print("\n1. Open this URL and approve access:\n")
    print(f"   {AUTH_BASE}?{urllib.parse.urlencode(params)}\n")
    print("2. You'll be redirected to your redirect_uri with ?code=... in the URL.")
    print("   Paste the value of that `code` parameter below.\n")

    code = input("code: ").strip()
    if not code:
        print("No code entered.", file=sys.stderr)
        return 1
    # The code arrives URL-encoded; a stray %2A on the end is a common gotcha.
    code = urllib.parse.unquote(code)

    tokens = exchange_code(settings, code, redirect_uri)
    TokenStore(settings).save(args.account, tokens)
    print(f"\nSaved tokens for account {args.account!r}.")
    print(f"Granted scopes: {tokens.scope}")
    missing = [s for s in REQUIRED_SCOPES if not tokens.has_scope(s)]
    if missing:
        print(f"WARNING: missing scopes {missing} -- posting will fail.")
    return 0


def cmd_check(args, settings: Settings) -> int:
    """Report what this account is actually permitted to post right now."""
    client = TikTokClient(settings)
    info = client.query_creator_info(args.account)

    print(f"\nCreator:            {info.nickname}")
    print(f"Privacy options:    {info.privacy_level_options}")
    print(f"Max duration:       {info.max_video_post_duration_sec}s")
    print(f"Comments disabled:  {info.comment_disabled}")
    print(f"Duet disabled:      {info.duet_disabled}")
    print(f"Stitch disabled:    {info.stitch_disabled}")

    print("\n--- Audit status ---")
    if info.can_post_publicly:
        print("PUBLIC POSTING AVAILABLE. Your client appears audited for this account.")
    else:
        print("SELF_ONLY ONLY. Either your app is unaudited, or this account is private.")
        print("Posts will be visible to you alone until the audit clears.")
    return 0


def cmd_prep(args, settings: Settings) -> int:
    out = prepare_clip(
        Path(args.src),
        Path(args.dst),
        srt=Path(args.srt) if args.srt else None,
        blur_pad=not args.black_bars,
        max_duration=args.max_duration,
        fps=args.fps,
    )
    print(f"Rendered {out}")
    problems = validate_for_tiktok(out)
    if problems:
        print("\nSpec problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Passes TikTok spec checks.")
    print(
        f"Safe zone: keep text between y={DEFAULT_SAFE_ZONE.text_top} and "
        f"y={DEFAULT_SAFE_ZONE.text_bottom}, and {DEFAULT_SAFE_ZONE.right}px clear of the right edge."
    )
    return 0


def cmd_enqueue(args, settings: Settings) -> int:
    queue = PostQueue(settings)
    job_id = queue.enqueue(
        account=args.account,
        video_path=Path(args.video),
        title=args.title,
        privacy_level=args.privacy,
        mode=args.mode,
        is_aigc=args.aigc,
    )
    if job_id is None:
        print("Already queued (duplicate detected); nothing added.")
        return 0

    if args.mode == PostMode.INBOX.value:
        print(f"Queued job {job_id} for the TikTok inbox (draft).")
        print("It will appear as a notification in the app; you write the caption")
        print("and tap post. No audit needed, and it can be public.")
    else:
        print(f"Queued job {job_id} for direct post with privacy_level={args.privacy}")
    return 0


def cmd_work(args, settings: Settings) -> int:
    worker = QueueWorker(settings)
    if args.once:
        did = worker.run_once()
        print("Processed one job." if did else "Nothing to do.")
        return 0
    worker.run_forever(poll_interval=args.interval)
    return 0


def cmd_status(args, settings: Settings) -> int:
    queue = PostQueue(settings)
    rows = list(queue.list_jobs(args.filter))
    if not rows:
        print("Queue is empty.")
        return 0
    print(f"{'ID':<5} {'STATUS':<11} {'TRY':<4} {'MODE':<7} {'PRIVACY':<20} FILE")
    for job in rows:
        # Privacy is meaningless for inbox jobs -- the creator picks it in-app.
        privacy = "-" if job.mode == PostMode.INBOX.value else job.privacy_level
        print(
            f"{job.id:<5} {job.status:<11} {job.attempts:<4} "
            f"{job.mode:<7} {privacy:<20} {Path(job.video_path).name}"
        )
        if job.last_error:
            print(f"      last error: {job.last_error[:150]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tiktok_pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="authorize a TikTok account")
    p.add_argument("--account", required=True, help="local nickname for this account")
    p.add_argument(
        "--redirect-uri",
        default=os.environ.get("TIKTOK_REDIRECT_URI"),
        required="TIKTOK_REDIRECT_URI" not in os.environ,
        help="must match the portal exactly; defaults to $TIKTOK_REDIRECT_URI",
    )
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("check", help="show posting permissions and audit status")
    p.add_argument("--account", required=True)
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("prep", help="render a clip to TikTok specs")
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    p.add_argument("--srt", help="optional subtitle file to burn in")
    p.add_argument("--black-bars", action="store_true", help="use black bars, not blur pad")
    p.add_argument("--max-duration", type=float)
    p.add_argument("--fps", type=int, help="resample frame rate; default preserves source")
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("enqueue", help="add a video to the posting queue")
    p.add_argument("--account", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--privacy", default="SELF_ONLY", help="direct mode only")
    p.add_argument(
        "--mode",
        choices=[m.value for m in PostMode],
        default=PostMode.DIRECT.value,
        help="direct = publish now (needs audit for public); "
             "inbox = draft for you to finish in the app (no audit)",
    )
    p.add_argument("--aigc", action="store_true", help="mark as AI-generated content")
    p.set_defaults(fn=cmd_enqueue)

    p = sub.add_parser("work", help="drain the posting queue")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    p.set_defaults(fn=cmd_work)

    p = sub.add_parser("status", help="show queue contents")
    p.add_argument("--filter", help="filter by status, e.g. PENDING")
    p.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
