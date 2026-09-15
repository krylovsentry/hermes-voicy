"""Pure-stdlib synthesis of retro-style sound-effect WAV files.

See docs/adr/0002-sound-delivery.md for why stdlib-only (no binary
assets, no sox) and docs/adr/0006-retro-presets.md for the waveform/
envelope model.

API:
    tone_path(name, recipe, volume) -> Path   # cached WAV for a tone
    recipe(name) -> Recipe                    # defaults / validated recipe

A Recipe is a sequence of notes. Each note is (freq_hz, duration_ms) or
(freq_hz, duration_ms, waveform, envelope, glide_to_hz) where waveform is
one of WAVEFORMS, envelope is one of ENVELOPES, and glide_to (optional)
linearly bends the pitch from freq to glide_to across the note — the
"power sweep" used by the transform/cyber banks. 16-bit mono PCM, 22050 Hz.

Waveforms (8-bit console style):
    sine, square, triangle, saw, noise (white, deterministic per note)

Envelopes:
    "decay"    attack to full, linear decay to ~0 over the note (blip)
    "held"     attack to full, sustain, release at the end (steady tone)
    "attack"   ramp up to full and cut (stinger)

Every note gets a few-ms raised-cosine anti-click fade on top of the
envelope so back-to-back notes don't click.
"""
from __future__ import annotations

import hashlib
import math
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

RATE = 22050
BITS = 16
FADE_MS = 5.0

WAVEFORMS = ("sine", "square", "triangle", "saw", "noise")
ENVELOPES = ("decay", "held", "attack")

# (freq_hz, duration_ms, waveform, envelope)
DEFAULT_RECIPES: dict[str, list[tuple]] = {
    # "ding-dong up" — needs attention
    "question": [(660.0, 100), (880.0, 120)],
    # soft single ding — work finished
    "done": [(987.77, 150)],
    # two low thuds — something failed terminally
    "error": [(330.0, 120), (277.18, 160)],
}

DEFAULT_TONE_MS = {"question": 110, "done": 150, "error": 130}

# Sanity bounds so a typo in config can't produce a 10-second drone or NaN.
MAX_NOTE_MS = 1500
MAX_FREQ_HZ = 10000.0
MIN_FREQ_HZ = 30.0


def _norm_wave(w) -> str:
    w = (w or "sine").strip().lower()
    if w not in WAVEFORMS:
        raise ValueError(f"unknown waveform {w!r}; known: {list(WAVEFORMS)}")
    return w


def _norm_env(e) -> str:
    e = (e or "decay").strip().lower()
    if e not in ENVELOPES:
        raise ValueError(f"unknown envelope {e!r}; known: {list(ENVELOPES)}")
    return e


@dataclass(frozen=True)
class Note:
    freq: float
    ms: int
    waveform: str = "sine"
    envelope: str = "decay"
    glide_to: Optional[float] = None  # linear pitch bend target; None = fixed

    def __post_init__(self) -> None:
        if not MIN_FREQ_HZ <= self.freq <= MAX_FREQ_HZ:
            raise ValueError(f"frequency {self.freq} Hz out of range")
        if not 20 <= self.ms <= MAX_NOTE_MS:
            raise ValueError(f"duration {self.ms} ms out of range")
        _norm_wave(self.waveform)
        _norm_env(self.envelope)
        if (self.glide_to is not None
                and not MIN_FREQ_HZ <= self.glide_to <= MAX_FREQ_HZ):
            raise ValueError(f"glide target {self.glide_to} Hz out of range")


def _coerce_note(item) -> Note:
    if isinstance(item, Note):
        return item
    if not isinstance(item, (list, tuple)) or not item:
        raise ValueError(f"note must be [freq, ms(, waveform, envelope, glide_to)], got {item!r}")
    freq, ms = float(item[0]), int(item[1])
    waveform = item[2] if len(item) > 2 else "sine"
    envelope = item[3] if len(item) > 3 else "decay"
    glide_to = item[4] if len(item) > 4 else None
    if glide_to is not None:
        glide_to = float(glide_to)
    if len(item) > 5:
        raise ValueError(f"note has {len(item)} fields, expected 2..5: {item!r}")
    return Note(freq, ms, _norm_wave(waveform), _norm_env(envelope), glide_to)


@dataclass(frozen=True)
class Recipe:
    notes: tuple = field()

    def __post_init__(self) -> None:
        if not self.notes:
            raise ValueError("recipe has no notes")

    @classmethod
    def from_list(cls, notes: list) -> "Recipe":
        return cls(tuple(_coerce_note(n) for n in notes))

    def total_ms(self) -> int:
        return sum(n.ms for n in self.notes)

    def fingerprint(self) -> str:
        raw = repr(tuple(
            (n.freq, n.ms, n.waveform, n.envelope, n.glide_to) for n in self.notes
        ))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse_recipe(raw: object) -> Recipe:
    """Build a Recipe from a config-shaped value.

    Accepts (per note, in this precedence):
      - [freq, ms] or [freq, ms, waveform, envelope]
      - bare freq number (waveform/envelope from the enclosing preset,
        duration from the per-tone default ms)
      - dict {"freqs": [...], "ms": N} (legacy bare-freq shape)
    """
    if isinstance(raw, dict):
        freqs = raw.get("freqs") or raw.get("frequencies")
        ms = raw.get("ms", 120)
        if not isinstance(freqs, (list, tuple)) or not freqs:
            raise ValueError(f"tone dict needs 'freqs' list, got {raw!r}")
        return Recipe.from_list([(f, ms) for f in freqs])
    if isinstance(raw, (list, tuple)) and raw:
        first = raw[0]
        if isinstance(first, (list, tuple)):
            return Recipe.from_list(list(raw))
        if isinstance(first, (int, float)):
            # Bare freq list: shared per-note duration, plain sine/decay.
            return Recipe.from_list([(float(f), 120) for f in raw])
        raise ValueError(f"tone list items must be [freq, ms...] or Hz, got {raw!r}")
    raise ValueError(f"unsupported tone value: {raw!r}")


def default_recipe(name: str) -> Recipe:
    try:
        return Recipe.from_list(DEFAULT_RECIPES[name])
    except KeyError as exc:
        raise KeyError(f"unknown tone {name!r}; known: {sorted(DEFAULT_RECIPES)}") from exc


# ---------------------------------------------------------------------------
# Sample generation
# ---------------------------------------------------------------------------

def _wave_sample(kind: str, phase: float, rng: random.Random | None) -> float:
    """One sample in [-1, 1] for *kind* at oscillator *phase* (0..1)."""
    if kind == "sine":
        return math.sin(2.0 * math.pi * phase)
    if kind == "square":
        return 1.0 if phase < 0.5 else -1.0
    if kind == "triangle":
        if phase < 0.25:
            return 4.0 * phase - 0.5
        if phase < 0.75:
            return 1.5 - 4.0 * phase
        return 4.0 * (phase - 1.0) + 0.5
    if kind == "saw":
        return 2.0 * phase - 1.0
    if kind == "noise":
        return rng.uniform(-1.0, 1.0)
    raise ValueError(f"unknown waveform {kind!r}")


def _envelope_value(env: str, frac: float) -> float:
    """Gain multiplier at *frac* (0..1) through the note."""
    if env == "decay":
        a = 0.05  # 5% quick attack, then linear release to ~0
        if frac < a:
            return frac / a
        return max(0.0, 1.0 - (frac - a) / (1.0 - a))
    if env == "held":
        a, r = 0.10, 0.30  # 10% attack, 30% release
        if frac < a:
            return frac / a
        if frac < 1.0 - r:
            return 1.0
        return max(0.0, (1.0 - frac) / r)
    if env == "attack":
        return min(1.0, frac / 0.85)
    raise ValueError(f"unknown envelope {env!r}")


def _note_samples(note: Note, volume: float, fade_samples: int) -> list[int]:
    n = max(1, int(RATE * note.ms / 1000.0))
    rng = random.Random(0xC0FFEE) if note.waveform == "noise" else None
    out = []
    if note.waveform == "noise":
        phase: Optional[float] = None
    else:
        phase = 0.0
        if note.glide_to is None:
            f0 = f1 = note.freq
        else:
            # Linear pitch bend: integrate f(t) per sample for continuity.
            f0, f1 = note.freq, note.glide_to
        dt = 1.0 / RATE
        slope = (f1 - f0) / n
        for i in range(n):
            f = f0 + slope * i
            phase = (phase + f * dt) % 1.0
            s = _wave_sample(note.waveform, phase, None)
            g = _envelope_value(note.envelope, i / n)
            # raised-cosine anti-click fades at both ends
            if i < fade_samples:
                g *= 0.5 - 0.5 * math.cos(math.pi * i / max(1, fade_samples))
            elif i >= n - fade_samples:
                k = n - 1 - i
                g *= 0.5 - 0.5 * math.cos(math.pi * k / max(1, fade_samples))
            s = max(-1.0, min(1.0, s * g * volume))
            out.append(int(s * 32767))
        return out

    for i in range(n):
        t = i / RATE
        s = _wave_sample("noise", t, rng)
        g = _envelope_value(note.envelope, i / n)
        # raised-cosine anti-click fades at both ends
        if i < fade_samples:
            g *= 0.5 - 0.5 * math.cos(math.pi * i / max(1, fade_samples))
        elif i >= n - fade_samples:
            k = n - 1 - i
            g *= 0.5 - 0.5 * math.cos(math.pi * k / max(1, fade_samples))
        s = max(-1.0, min(1.0, s * g * volume))
        out.append(int(s * 32767))
    return out


def render_wav(recipe: Recipe, volume: float) -> bytes:
    """Render a recipe to an in-memory 16-bit mono PCM WAV."""
    volume = max(0.0, min(1.0, float(volume)))
    if volume <= 0:
        volume = 0.001  # keep the file valid; silence is a config problem, not a synth one

    fade_samples = int(RATE * FADE_MS / 1000.0)
    samples: list[int] = []
    for note in recipe.notes:
        samples.extend(_note_samples(note, volume, fade_samples))

    data = struct.pack(f"<{len(samples)}h", *samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 1, 1, RATE, RATE * 2, 2, BITS,
        b"data", len(data),
    )
    return header + data


def _cache_dir() -> Path:
    base = Path(__import__("os").environ.get("XDG_CACHE_HOME") or "~/.cache")
    d = base.expanduser() / "hermes-voicy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tone_path(name: str, recipe: Recipe, volume: float) -> Path:
    """Return the path of a cached WAV for this tone (synthesized on miss)."""
    key = f"{name}-{recipe.fingerprint()}-{int(volume * 1000):04d}"
    out = _cache_dir() / f"tone-{key}.wav"
    if not out.exists():
        tmp = out.with_suffix(".wav.tmp")
        tmp.write_bytes(render_wav(recipe, volume))
        tmp.replace(out)
    return out
