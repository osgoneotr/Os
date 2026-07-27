"""Config loading. Everything tunable lives in config/settings.yaml."""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

# How old a `last_verified` date can get before we nag about it.
FEE_STALENESS_DAYS = 180


class Config:
    """Dict-backed config with dotted-path lookup: cfg.get('pricing.trim_fraction')."""

    def __init__(self, data: dict[str, Any], source: Path):
        self._data = data
        self.source = source

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        value = self.get(path, _MISSING)
        if value is _MISSING:
            raise KeyError(f"Missing required config key '{path}' in {self.source}")
        return value

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # -- convenience accessors used all over the codebase -----------------

    def channel(self, name: str) -> dict[str, Any]:
        ch = self.get(f"channels.{name}")
        if ch is None:
            raise KeyError(f"Unknown channel '{name}'. Known: {self.channel_names()}")
        return ch

    def channel_names(self) -> list[str]:
        channels = self.get("channels", {})
        return [k for k in channels if k != "priority"]

    def sell_channels(self) -> list[str]:
        """Enabled channels we actually list on, in priority order."""
        out = []
        for name in self.get("channels.priority", []):
            ch = self.get(f"channels.{name}", {})
            if ch.get("enabled") and not ch.get("sourcing_only"):
                out.append(name)
        return out

    def path(self, key: str) -> Path:
        raw = self.require(f"paths.{key}")
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p


_MISSING = object()
_cached: Config | None = None


def load(path: str | Path | None = None, *, force: bool = False) -> Config:
    """Load settings.yaml (cached). Pass force=True to re-read from disk."""
    global _cached
    if _cached is not None and not force and path is None:
        return _cached

    cfg_path = Path(path) if path else ROOT / "config" / "settings.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config not found at {cfg_path}. Copy config/settings.yaml from the repo, "
            "or pass --config /path/to/settings.yaml."
        )
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    cfg = Config(data, cfg_path)
    if path is None:
        _cached = cfg
    return cfg


def env(name: str, default: str | None = None) -> str | None:
    """Read an env var, loading .env on first use if present.

    Kept deliberately dumb: no external dotenv dependency.
    """
    _load_dotenv_once()
    return os.environ.get(name, default)


_dotenv_loaded = False


def _load_dotenv_once() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # Real environment always wins over the file.
        os.environ.setdefault(key, value)


def stale_fee_warnings(cfg: Config, today: dt.date | None = None) -> list[str]:
    """Return a warning per fee block whose `last_verified` date is old.

    Profit maths built on a fee number from two years ago is fiction, so we
    surface this on every run rather than burying it in the docs.
    """
    today = today or dt.date.today()
    warnings: list[str] = []

    def check(label: str, block: dict[str, Any] | None) -> None:
        if not isinstance(block, dict):
            return
        raw = block.get("last_verified")
        if not raw:
            warnings.append(f"{label}: no last_verified date set — fees unverified.")
            return
        try:
            when = dt.date.fromisoformat(str(raw))
        except ValueError:
            warnings.append(f"{label}: last_verified '{raw}' is not a YYYY-MM-DD date.")
            return
        age = (today - when).days
        if age > FEE_STALENESS_DAYS:
            warnings.append(
                f"{label}: fees last verified {age} days ago ({raw}). "
                "Re-check the platform's fee page and update config/settings.yaml."
            )

    for name in cfg.channel_names():
        block = cfg.get(f"channels.{name}")
        # Channels we don't list on have no fees to go stale.
        if not isinstance(block, dict) or not block.get("enabled") or block.get("sourcing_only"):
            continue
        check(f"channels.{name}", block)
    check("postage", cfg.get("postage"))
    return warnings


def print_stale_fee_warnings(cfg: Config, stream=sys.stderr) -> None:
    for w in stale_fee_warnings(cfg):
        print(f"[fee-check] {w}", file=stream)
