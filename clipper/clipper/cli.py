"""Command line interface.

`clip` runs the whole thing. `score` is the one you will actually live in while
tuning: it runs everything except the encodes, so you can see what would be cut
in seconds rather than minutes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from .config import Config
from .media.audio import envelope_for
from .media.ffmpeg import ffmpeg_path, ffprobe_path
from .media.probe import probe
from .models import Transcript
from .pipeline import analyze, run
from .score.scorer import pad_to_bounds

app = typer.Typer(add_completion=False, help=__doc__)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s" if verbose else "%(message)s",
    )


def _load_config(
    config_path: Path | None,
    *,
    top: int | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    reframe: str | None = None,
    music: str | None = None,
    logo: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    output: str | None = None,
    captions: bool | None = None,
) -> Config:
    return Config.load(
        config_path,
        **{
            "scoring.top_k": top,
            "scoring.min_duration": min_duration,
            "scoring.max_duration": max_duration,
            "render.reframe": reframe,
            "render.music_path": music,
            "render.logo_path": logo,
            "transcribe.backend": backend,
            "transcribe.model": model,
            "output.directory": output,
            "captions.enabled": captions,
        },
    )


@app.command()
def clip(
    source: Path = typer.Argument(..., exists=True, help="Source video file."),
    config: Path = typer.Option(None, "--config", "-c", help="JSON config file."),
    output: str = typer.Option(None, "--out", "-o", help="Output directory."),
    top: int = typer.Option(None, "--top", "-n", help="How many clips to produce."),
    min_duration: float = typer.Option(None, "--min", help="Minimum clip length in seconds."),
    max_duration: float = typer.Option(None, "--max", help="Maximum clip length in seconds."),
    reframe: str = typer.Option(None, "--reframe", help="center | blur_pad | track"),
    music: str = typer.Option(None, "--music", help="Background music file."),
    logo: str = typer.Option(None, "--logo", help="PNG overlay for branding."),
    backend: str = typer.Option(None, "--backend", help="faster-whisper | openai | json"),
    model: str = typer.Option(None, "--model", help="ASR model (or transcript path for 'json')."),
    no_captions: bool = typer.Option(False, "--no-captions", help="Skip burned-in captions."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do everything except encode."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Find the best moments in SOURCE and export them as vertical clips."""
    _setup_logging(verbose)
    cfg = _load_config(
        config,
        top=top,
        min_duration=min_duration,
        max_duration=max_duration,
        reframe=reframe,
        music=music,
        logo=logo,
        backend=backend,
        model=model,
        output=output,
        captions=False if no_captions else None,
    )
    result = run(source, cfg, dry_run=dry_run)
    if not result.clips:
        typer.echo("No candidate segments matched the duration bounds.")
        raise typer.Exit(code=1)
    typer.echo(f"\n{len(result.clips)} clip(s) written to {result.output_dir}")
    typer.echo(f"Review: {result.output_dir / 'index.md'}")


@app.command()
def score(
    source: Path = typer.Argument(..., exists=True),
    config: Path = typer.Option(None, "--config", "-c"),
    top: int = typer.Option(None, "--top", "-n"),
    backend: str = typer.Option(None, "--backend"),
    model: str = typer.Option(None, "--model"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Rank segments without rendering — the fast loop for tuning weights."""
    _setup_logging(verbose)
    cfg = _load_config(config, top=top, backend=backend, model=model)
    info, _, _, candidates = analyze(source, cfg)

    if as_json:
        typer.echo(json.dumps([c.to_dict() for c in candidates], indent=2))
        return

    if not candidates:
        typer.echo("No candidates. Try widening --min/--max.")
        raise typer.Exit(code=1)

    for i, cand in enumerate(candidates, start=1):
        start, end = pad_to_bounds(cand, info.duration)
        top_features = sorted(cand.features.items(), key=lambda kv: -kv[1])[:4]
        typer.echo(
            f"\n{i}. score {cand.score:.3f}  {_tc(start)} → {_tc(end)}  ({end - start:.0f}s)"
        )
        typer.echo("   " + "  ".join(f"{k}={v:.2f}" for k, v in top_features))
        typer.echo(f"   {cand.text[:160]}{'…' if len(cand.text) > 160 else ''}")


@app.command()
def transcribe(
    source: Path = typer.Argument(..., exists=True),
    config: Path = typer.Option(None, "--config", "-c"),
    backend: str = typer.Option(None, "--backend"),
    model: str = typer.Option(None, "--model"),
    out: Path = typer.Option(None, "--out", "-o", help="Where to write the transcript JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Transcribe only, and write the result as reusable JSON."""
    _setup_logging(verbose)
    cfg = _load_config(config, backend=backend, model=model)
    from .pipeline import _transcribe_cached

    transcript = _transcribe_cached(source, cfg)
    destination = out or source.with_suffix(".transcript.json")
    transcript.save(destination)
    typer.echo(f"{len(transcript.words)} words -> {destination}")


@app.command()
def render(
    source: Path = typer.Argument(..., exists=True),
    start: float = typer.Option(..., "--start", help="Clip start in seconds."),
    end: float = typer.Option(..., "--end", help="Clip end in seconds."),
    out: Path = typer.Option(Path("out/manual.mp4"), "--out", "-o"),
    config: Path = typer.Option(None, "--config", "-c"),
    reframe: str = typer.Option(None, "--reframe"),
    transcript: Path = typer.Option(None, "--transcript", help="Transcript JSON for captions."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Render one hand-picked range — for fixing a cut the scorer got wrong."""
    _setup_logging(verbose)
    cfg = _load_config(config, reframe=reframe)
    info = probe(source)

    ass_path = None
    if transcript and cfg.captions.enabled:
        from .edit.captions import build_caption_lines, render_ass
        from .transcribe.importer import load_transcript_file

        loaded = load_transcript_file(transcript)
        lines = build_caption_lines(loaded.words_between(start, end), cfg.captions)
        ass_path = out.with_suffix(".ass")
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(
            render_ass(lines, cfg.captions, cfg.render.width, cfg.render.height, start),
            encoding="utf-8",
        )

    from .edit.render import render_clip

    plan = render_clip(info, start, end, out, cfg.render, ass_path, None, dry_run=dry_run)
    if dry_run:
        typer.echo(" ".join(plan.args))
    else:
        typer.echo(f"wrote {out}")


@app.command()
def doctor() -> None:
    """Check that the external pieces this tool depends on are present."""
    ok = True

    try:
        typer.echo(f"{'ffmpeg:':<18}{ffmpeg_path()}")
    except FileNotFoundError as exc:
        ok = False
        typer.echo(f"{'ffmpeg:':<18}MISSING — {exc}")

    fallback = "not found (falling back to ffmpeg parsing)"
    typer.echo(f"{'ffprobe:':<18}{ffprobe_path() or fallback}")

    for label, module, extra in (
        ("faster-whisper", "faster_whisper", "whisper"),
        ("openai", "openai", "openai"),
        ("opencv (track)", "cv2", "track"),
        ("anthropic (llm)", "anthropic", "llm"),
    ):
        try:
            __import__(module)
            typer.echo(f"{label + ':':<18}available")
        except ImportError:
            typer.echo(f"{label + ':':<18}not installed — pip install 'clipper[{extra}]'")

    raise typer.Exit(code=0 if ok else 1)


@app.command("init-config")
def init_config(
    path: Path = typer.Option(Path("clipper.json"), "--out", "-o"),
) -> None:
    """Write a config file with every default filled in, ready to edit."""
    if path.exists():
        typer.echo(f"{path} already exists; not overwriting.")
        raise typer.Exit(code=1)
    Config().save(path)
    typer.echo(f"wrote {path}")


def _tc(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


if __name__ == "__main__":
    app()
