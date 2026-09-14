"""voicy.* config resolution. See docs/adr/0003-configuration.md.

Precedence (highest first):
  1. HERMES_VOICY_* environment variables
  2. ``voicy:`` section of $HERMES_HOME/config.yaml
  3. built-in defaults

Config is read per event (Hermes' load_config_readonly is mtime-cached,
~microseconds) so mid-session edits apply without restart. The returned
dict is never mutated.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("hermes-voicy.config")

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "question": True,
    "done": True,
    "error": False,
    "volume": 0.6,
    "backend": "auto",
    "bell": True,
    # Sound bank (ADR-0006, rev 2026-09-14): bumblebee | nes | c64 | gba
    # | transform | cyber | legacy
    "presets": "bumblebee",
    "tones": {
        "question": [660.0, 880.0],
        "done": [987.77],
        "error": [330.0, 277.18],
    },
    # per-note duration (ms) for bare-freq `tones:` overrides
    "tone_ms": {"question": 110, "done": 150, "error": 130},
    "sounds": {},
}

# tone name -> (per-note duration ms, shared) — built-in fallback when
# neither config nor preset supplies one.
DEFAULT_TONE_MS = {"question": 110, "done": 150, "error": 130}

# Used when the configured preset name is unknown (config typo etc.).
DEFAULT_PRESET_FALLBACK = "bumblebee"

# tone name -> HERMES_VOICY_<X> env override for per-tone duration
_TONE_MS_ENV = {
    "question": "HERMES_VOICY_QUESTION_MS",
    "done": "HERMES_VOICY_DONE_MS",
    "error": "HERMES_VOICY_ERROR_MS",
}


@dataclass(frozen=True)
class VoicyConfig:
    enabled: bool
    question: bool
    done: bool
    error: bool
    volume: float
    backend: str
    bell: bool
    preset: str
    tones: Dict[str, Any]  # raw shape, validated lazily by synth.parse_recipe
    tone_ms: Dict[str, int]  # per-tone duration for bare-freq overrides
    sounds: Dict[str, str]  # tone name -> user audio file path (may be empty)
    # ADR-0007 (appended last: existing positional constructions stay valid)
    sound: bool = True  # audio master switch (top-level `sound:`)
    tray: Optional[Dict[str, Any]] = None  # top-level `tray:`, resolved


# ADR-0007: per-event defaults for the tray section
_DEFAULT_TRAY: Dict[str, Any] = {
    "enabled": False,
    "backend": "auto",
    "urgency": {"question": "critical", "done": "normal", "error": "normal"},
    "body_max": 200,
}
_URGENCIES = ("low", "normal", "critical")


def _env_bool(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    return raw.strip() if raw is not None and raw.strip() else None


def _cfg_section() -> Dict[str, Any]:
    """The ``voicy:`` section of the active Hermes config, or {}."""
    try:
        from hermes_cli.config import load_config_readonly
    except Exception:
        return {}  # outside a Hermes process (tests) — defaults only
    try:
        cfg = load_config_readonly() or {}
    except Exception as exc:  # pragma: no cover - config corruption paths
        logger.debug("hermes-voicy: config read failed: %s", exc)
        return {}
    section = cfg.get("voicy")
    return section if isinstance(section, dict) else {}


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return fallback


def _as_volume(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULTS["volume"]
    return max(0.0, min(1.0, v))


def _as_ms_dict(raw: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if isinstance(raw, dict):
        for name in ("question", "done", "error"):
            v = raw.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 20 <= v <= 1500:
                out[name] = int(v)
    return out


def _top_section(name: str) -> Dict[str, Any]:
    """A top-level section of the active Hermes config (ADR-0007)."""
    try:
        from hermes_cli.config import load_config_readonly
    except Exception:
        return {}  # outside a Hermes process (tests) — defaults only
    try:
        cfg = load_config_readonly() or {}
    except Exception as exc:  # pragma: no cover - config corruption paths
        logger.debug("hermes-voicy: config read failed: %s", exc)
        return {}
    section = cfg.get(name)
    return section if isinstance(section, dict) else {}


def _tray_section() -> Dict[str, Any]:
    """Resolve the top-level ``tray:`` section against defaults."""
    raw = _top_section("tray")
    urgency = {}
    raw_urg = raw.get("urgency")
    if isinstance(raw_urg, dict):
        for name in ("question", "done", "error"):
            v = raw_urg.get(name)
            if isinstance(v, str) and v.strip().lower() in _URGENCIES:
                urgency[name] = v.strip().lower()
    body_max = raw.get("body_max")
    if not (isinstance(body_max, (int, float)) and not isinstance(body_max, bool)
            and 20 <= body_max <= 2000):
        body_max = _DEFAULT_TRAY["body_max"]
    # NOTE: `desktop` (click-to-activate) was removed — the desktop-entry
    # hint activated the wrong terminal session on GNOME. A leftover
    # `tray.desktop` key in config.yaml is now ignored (harmless).
    return {
        "enabled": _env_bool("HERMES_VOICY_TRAY")
                   if _env_bool("HERMES_VOICY_TRAY") is not None
                   else _as_bool(raw.get("enabled"), _DEFAULT_TRAY["enabled"]),
        "backend": _env_str("HERMES_VOICY_TRAY_BACKEND")
                   or raw.get("backend") or "auto",
        "urgency": {**_DEFAULT_TRAY["urgency"], **urgency},
        "body_max": int(body_max),
    }


def _tone_ms(section: Dict[str, Any], name: str) -> int:
    """Per-tone duration for bare-freq overrides: env > config > built-in."""
    env = _env_str(_TONE_MS_ENV[name])
    if env is not None:
        try:
            v = int(float(env))
            if 20 <= v <= 1500:
                return v
        except ValueError:
            pass
    cfg_ms = _as_ms_dict(section.get("tone_ms")).get(name)
    if cfg_ms is not None:
        return cfg_ms
    return DEFAULT_TONE_MS.get(name, 120)


def load() -> VoicyConfig:
    """Resolve the effective config for one event."""
    from . import presets as presets_mod

    section = _cfg_section()

    enabled = _env_bool("HERMES_VOICY_ENABLED")
    if enabled is None:
        enabled = _as_bool(section.get("enabled"), DEFAULTS["enabled"])
    question = _env_bool("HERMES_VOICY_QUESTION")
    if question is None:
        question = _as_bool(section.get("question"), DEFAULTS["question"])
    done = _env_bool("HERMES_VOICY_DONE")
    if done is None:
        done = _as_bool(section.get("done"), DEFAULTS["done"])
    error = _env_bool("HERMES_VOICY_ERROR")
    if error is None:
        error = _as_bool(section.get("error"), DEFAULTS["error"])

    volume_env = _env_str("HERMES_VOICY_VOLUME")
    volume = _as_volume(volume_env if volume_env is not None else section.get("volume"))

    backend = _env_str("HERMES_VOICY_BACKEND") or section.get("backend") or "auto"
    if not isinstance(backend, str):
        backend = "auto"

    bell = _as_bool(section.get("bell"), DEFAULTS["bell"])

    # ADR-0007: audio master switch — top-level `sound:` section,
    # independent from per-event `question`/`done`/`error` toggles.
    sound = _env_bool("HERMES_VOICY_SOUND")
    if sound is None:
        sound = _as_bool(_top_section("sound").get("enabled"), True)

    preset_raw = _env_str("HERMES_VOICY_PRESETS") or section.get("presets") \
        or DEFAULTS["presets"]
    preset = str(preset_raw).strip().lower() or DEFAULTS["presets"]
    if preset not in presets_mod.list_names():
        logger.debug("hermes-voicy: unknown preset %r; using %r",
                     preset, DEFAULT_PRESET_FALLBACK)
        preset = DEFAULT_PRESET_FALLBACK

    # Start EMPTY on purpose: an absent `tones:` key means "use the
    # preset bank" (ADR-0006). Seeding with DEFAULTS["tones"] would let
    # the legacy bare-freq shape shadow every preset.
    tones: Dict[str, Any] = {}
    raw_tones = section.get("tones")
    if isinstance(raw_tones, dict):
        for name, value in raw_tones.items():
            if name in DEFAULTS["tones"]:
                tones[name] = value

    # User audio files (question/done/error -> path). Anything that is not
    # a string for a known tone is ignored — a broken key must never
    # silence the plugin.
    sounds: Dict[str, str] = {}
    raw_sounds = section.get("sounds")
    if isinstance(raw_sounds, dict):
        for name, value in raw_sounds.items():
            if name in ("question", "done", "error") and isinstance(value, str) \
                    and value.strip():
                sounds[name] = value.strip()

    return VoicyConfig(
        enabled=enabled,
        question=question,
        done=done,
        error=error,
        volume=volume,
        backend=backend.strip().lower() or "auto",
        bell=bell,
        preset=preset,
        tones=tones,
        tone_ms={n: _tone_ms(section, n) for n in ("question", "done", "error")},
        sounds=sounds,
        sound=sound,
        tray=_tray_section(),
    )


def sound_file(cfg: "VoicyConfig", name: str) -> Optional[Path]:
    """User-provided audio file for *name*, or None -> synthesize.

    Precedence: ``HERMES_VOICY_<NAME>_SOUND`` env > ``voicy.sounds``.
    ``~`` is expanded; missing files fall back to synthesis with a debug
    log (a stale path must not silence the tone).
    """
    raw = _env_str(f"HERMES_VOICY_{name.upper()}_SOUND") \
        or cfg.sounds.get(name)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        return path
    logger.debug("hermes-voicy: sound file for %r not found: %s "
                 "(falling back to synthesis)", name, path)
    return None


def tone_recipe(cfg: VoicyConfig, name: str):
    """Build a validated synth.Recipe for *name* from cfg.

    Resolution (ADR-0006): user ``tones:`` override (bare-freq lists get
    the preset's waveform/envelope) > preset bank > built-in default.
    """
    from . import presets as presets_mod

    try:
        preset = presets_mod.get(cfg.preset)
    except KeyError:
        preset = presets_mod.get(DEFAULT_PRESET_FALLBACK)

    raw = cfg.tones.get(name)
    if raw is not None:
        return presets_mod.resolve_tone(preset, name, raw, cfg.tone_ms.get(name, 120))
    return presets_mod.resolve_tone(preset, name, None)
