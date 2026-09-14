"""Retro-console tone presets (the "90s game" sound) + modern sci-fi banks.

See docs/adr/0006-retro-presets.md. Each preset is a small bank of three
effects (question / done / error) rendered in the idiom of a real
sound source:

    NES       — 2A03 square blips + noise: sharp, punchy, 50% duty
    c64       — SID-style: bright square, warm triangle, gritty noise
    gba       — Game Boy Color: soft triangle lead + square arp, no noise
    transform — "transformation sequence": rising saw power sweeps
    cyber     — cyberpunk / synthwave: piercing square alerts, fast
                square arps, glitch clusters
    bumblebee — Pachelbel's "Bumblebee": low buzzing G drone + fast
                rolling scale figure

Tone shapes use [freq_hz, ms, waveform, envelope, glide_to_hz] (see
synth.py); glide_to is an optional linear pitch bend across the note.

The ``legacy`` preset is the pre-preset behaviour: plain sine beeps with
whatever ``tones:`` override the user already has in config.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from . import synth

PRESET_NAMES = ("nes", "c64", "gba", "transform", "cyber", "bumblebee", "legacy")
DEFAULT_PRESET = "bumblebee"


@dataclass(frozen=True)
class Preset:
    name: str
    blurb: str
    wave: str   # waveform used for bare-freq notes in user overrides
    env: str    # envelope used for bare-freq notes in user overrides
    tones: Dict[str, List[tuple]]  # question/done/error recipes


_PRESETS: Dict[str, Preset] = {
    "nes": Preset(
        "nes",
        "NES / Famicom (2A03): square blips + noise percussion",
        "square",
        "decay",
        {
            # attention grab: C5→E5 square stabs, then a rising square flourish
            "question": [
                [523.25, 90, "square", "decay"],
                [659.25, 90, "square", "decay"],
                [1046.5, 140, "square", "held"],
            ],
            # work finished: G5-C6-E6 square arpeggio (the "it worked" riff)
            "done": [
                [783.99, 70, "square", "decay"],
                [1046.5, 70, "square", "decay"],
                [1318.5, 130, "square", "held"],
            ],
            # terminal failure: falling saw "power-down" + gritty noise burst
            "error": [
                [392.0, 110, "saw", "decay"],
                [261.63, 110, "saw", "decay"],
                [174.61, 150, "saw", "decay"],
                [200.0, 130, "noise", "decay"],
            ],
        },
    ),
    "c64": Preset(
        "c64",
        "Commodore 64 (SID): bright square, warm triangle, gritty noise",
        "square",
        "decay",
        {
            # attention grab: B5→D#6→F#6 square climb
            "question": [
                [987.77, 80, "square", "decay"],
                [1174.66, 80, "square", "decay"],
                [1396.91, 130, "square", "held"],
            ],
            # work finished: warm triangle fanfare C6-E6-G6
            "done": [
                [1046.5, 90, "triangle", "held"],
                [1318.5, 90, "triangle", "held"],
                [1567.98, 160, "triangle", "held"],
            ],
            # terminal failure: low saw thud followed by a noise "explosion"
            "error": [
                [196.0, 140, "saw", "decay"],
                [185.0, 200, "noise", "decay"],
            ],
        },
    ),
    "gba": Preset(
        "gba",
        "Game Boy Color: soft triangle lead + square arp, gentle",
        "triangle",
        "decay",
        {
            # attention grab: triangle question hook (C6→G5)
            "question": [
                [1046.5, 110, "triangle", "attack"],
                [783.99, 150, "triangle", "held"],
            ],
            # work finished: square arpeggio over a triangle pad note
            "done": [
                [523.25, 220, "triangle", "held"],
                [783.99, 70, "square", "decay"],
                [1046.5, 70, "square", "decay"],
                [1318.5, 120, "square", "held"],
            ],
            # terminal failure: minor-fall triangle slide + square wince
            "error": [
                [392.0, 120, "triangle", "decay"],
                [349.23, 120, "triangle", "decay"],
                [277.18, 160, "triangle", "decay"],
                [110.0, 120, "square", "decay"],
            ],
        },
    ),
    "transform": Preset(
        "transform",
        "Transformation sequence: rising saw 'power morph' sweeps",
        "saw",
        "decay",
        {
            # attention grab: rising saw morph + confirmation tone,
            # like the charge-up before a form shift
            "question": [
                [220.0, 240, "saw", "decay", 880.0],
                [880.0, 160, "triangle", "held"],
            ],
            # work finished: big rising power sweep into a held fanfare
            "done": [
                [110.0, 300, "saw", "decay", 660.0],
                [660.0, 80, "square", "decay"],
                [880.0, 80, "square", "decay"],
                [1318.5, 180, "sine", "held"],
            ],
            # terminal failure: downward power drain + gritty glitch
            "error": [
                [440.0, 220, "saw", "decay", 82.41],
                [66.0, 180, "noise", "decay"],
            ],
        },
    ),
    "cyber": Preset(
        "cyber",
        "Cyberpunk / synthwave: piercing square alert, fast arp, glitch cluster",
        "square",
        "decay",
        {
            # attention grab: piercing high square, then two quick stabs
            "question": [
                [1567.98, 120, "square", "attack"],
                [1046.5, 70, "square", "decay"],
                [1567.98, 100, "square", "decay"],
            ],
            # work finished: fast neon arpeggio (A5-C#6-E6) over a sine pad
            "done": [
                [440.0, 260, "sine", "held"],
                [880.0, 60, "square", "decay"],
                [1108.73, 60, "square", "decay"],
                [1318.5, 140, "square", "held"],
            ],
            # terminal failure: descending minor square glitch + noise crackle
            "error": [
                [659.25, 80, "square", "decay"],
                [622.25, 80, "square", "decay"],
                [493.88, 120, "saw", "decay"],
                [150.0, 120, "noise", "decay"],
            ],
        },
    ),
    "bumblebee": Preset(
        "bumblebee",
        'Pachelbel\'s "Bumblebee": low G drone + fast buzzing scale figure',
        "saw",
        "decay",
        {
            # attention grab: a "where are you?" call — a low buzzing G
            # drone, then a fast ascending scale (C5→E5→G5→B5) that ends
            # on a held note
            "question": [
                [98.0, 900, "saw", "held"],
                [523.25, 150, "square", "decay"],
                [659.25, 150, "square", "decay"],
                [783.99, 150, "square", "decay"],
                [987.77, 300, "square", "held"],
            ],
            # work finished: happy buzz — low drone intro, then a fast
            # descending figure that settles on C5 (E5→G5→E5→C5)
            "done": [
                [98.0, 1000, "saw", "held"],
                [1046.5, 120, "square", "decay"],
                [783.99, 120, "square", "decay"],
                [1046.5, 120, "square", "decay"],
                [523.25, 320, "square", "held"],
            ],
            # terminal failure: the bee spirals down and the buzz dies
            # (G5→E5→C5→G4, 15% detune = "dying buzz", then noise thud)
            "error": [
                [783.99, 180, "saw", "decay", 659.25],
                [659.25, 180, "saw", "decay", 523.25],
                [523.25, 240, "saw", "decay", 392.0],
                [98.0, 200, "noise", "decay"],
            ],
        },
    ),
    "legacy": Preset(
        "legacy",
        "Classic sine beeps (pre-preset hermes-voicy sound)",
        "sine",
        "decay",
        {
            "question": synth.DEFAULT_RECIPES["question"],
            "done": synth.DEFAULT_RECIPES["done"],
            "error": synth.DEFAULT_RECIPES["error"],
        },
    ),
}


def presets() -> Dict[str, Preset]:
    return dict(_PRESETS)


def get(name: str) -> Preset:
    key = (name or "").strip().lower()
    if key not in _PRESETS:
        raise KeyError(f"unknown preset {name!r}; known: {list(_PRESETS)}")
    return _PRESETS[key]


def list_names() -> List[str]:
    return list(_PRESETS)


def resolve_tone(
    preset: Preset,
    tone_name: str,
    override: Any,
    default_ms: int = 120,
) -> synth.Recipe:
    """Effective recipe for *tone_name*: the user's ``tones:`` override
    wins if present; bare-freq overrides are dressed with the preset's
    default waveform/envelope and per-tone duration. Falls back to the
    preset's own bank."""
    if override is not None:
        # Bare freq list: shared per-tone duration + preset idiom.
        if isinstance(override, (list, tuple)) and override and \
                isinstance(override[0], (int, float)):
            return synth.Recipe.from_list(
                [(float(f), default_ms, preset.wave, preset.env) for f in override]
            )
        try:
            recipe = synth.parse_recipe(override)
        except Exception:
            recipe = None
        if recipe is not None:
            return recipe
    return synth.Recipe.from_list(preset.tones[tone_name])
