from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clipper.media.ffmpeg import ffmpeg_path
from clipper.models import Segment, Transcript, Word


def _ffmpeg_available() -> bool:
    try:
        return bool(ffmpeg_path())
    except FileNotFoundError:
        return False


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not available")


def make_words(spec: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(text, start, end) for text, start, end in spec]


def words_from_sentence(sentence: str, start: float, wps: float = 3.0) -> list[Word]:
    """Lay a sentence out on a regular grid — enough for timing-sensitive tests
    without hand-writing every timestamp."""
    step = 1.0 / wps
    return [
        Word(token, start + i * step, start + (i + 1) * step - 0.02)
        for i, token in enumerate(sentence.split())
    ]


@pytest.fixture
def transcript() -> Transcript:
    """Two minutes of speech where one stretch is clearly the best clip: it
    opens with a hook phrase, contains numbers and a payoff, and ends on a
    sentence boundary."""
    sentences = [
        "So anyway we were just kind of talking about the weather that day.",
        "It was fine I guess nothing really happened for a while after that.",
        "Here's why most people get onboarding completely wrong.",
        "They measure signups instead of the first real action a user takes.",
        "We changed one thing and activation went up 40 percent in three weeks.",
        "The reason is that signups measure intent but activation measures value.",
        "So track the first action and everything downstream gets easier.",
        "Anyway that was the thing I wanted to mention about it.",
        "We can probably talk about the other stuff some other time honestly.",
    ]
    segments: list[Segment] = []
    cursor = 0.0
    for sentence in sentences:
        words = words_from_sentence(sentence, cursor)
        segments.append(Segment(start=words[0].start, end=words[-1].end, text=sentence, words=words))
        cursor = words[-1].end + 0.7  # deliberate pause between sentences
    return Transcript(language="en", duration=cursor, segments=segments, model="test")


@pytest.fixture
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 40s 1280x720 clip with a tone, generated once per session."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not available")
    directory = tmp_path_factory.mktemp("media")
    path = directory / "source.mp4"
    subprocess.run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=44100",
            "-t",
            "40",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Keep generated out/ and .cache/ directories inside the test sandbox."""
    monkeypatch.chdir(tmp_path)
    yield
    shutil.rmtree(tmp_path / "out", ignore_errors=True)
