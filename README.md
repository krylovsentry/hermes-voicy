# hermes-voicy 🔔

Audible + desktop-tray feedback for Hermes Agent terminal sessions. The
agent stops being silent about the two things that matter: **it needs
you** (a question or approval prompt) and **it's done** (turn finished).
Optional third signal for terminal API failures. Events fire both
channels: synthesized tones and, when enabled, desktop tray
notifications.

No assets, no dependencies beyond the Python stdlib — tones are synthesized
as WAV on first use and played through the first available backend:
`paplay` → `afplay` → `play` (sox) → `aplay` → `ffplay` → terminal bell.
Tray notifications go through `notify-send` (Linux) or `osascript`
(macOS).

## What sounds when

| Event | Hook | Default |
|---|---|---|
| Agent asks you something (`clarify` tool) | `pre_tool_call` | **on** |
| Dangerous command approval prompt | `pre_approval_request` | **on** |
| A turn finishes (work is done) | `post_llm_call` | **on** |
| API failure that won't recover (retries exhausted) | `api_request_error` | off |

Rationale is documented in `docs/adr/0001-hook-selection.md`.

## Install

From a checkout of this repo (Python 3.10+):

```sh
scripts/install.sh
```

If your Hermes home is not the default (`$HERMES_HOME`, else `~/.hermes`),
point at it: `HERMES_HOME=/path/to/.hermes scripts/install.sh`.

This symlinks `$HERMES_HOME/plugins/hermes-voicy` to this repo and adds
`hermes-voicy` to `plugins.enabled` in `config.yaml`. Sounds go live on
the next Hermes event in already-running sessions; a new session always
picks the plugin up (plugin discovery is cached per process).

Verify:

```
$ hermes plugins list | grep voicy
```

Then inside a session:

```
/voicy test all     # hear all three tones
/voicy              # current config + active backend
```

Uninstall: `rm $HERMES_HOME/plugins/hermes-voicy` and remove
`- hermes-voicy` from `plugins.enabled`.

## Configuration

`config.yaml` (all keys optional):

```yaml
voicy:
  enabled: true        # master switch
  question: true
  done: true
  error: false         # opt-in
  volume: 0.6          # 0.0..1.0, applied at synthesis time
  backend: auto        # auto|paplay|afplay|play|aplay|ffplay|bell
  bell: true           # allow terminal-bell fallback
  presets: bumblebee   # sound bank: nes|c64|gba|transform|cyber|bumblebee|legacy
  tone_ms:             # per-note duration for bare-freq tone overrides
    question: 110
    done: 150
    error: 130
  tones:               # optional per-tone overrides (see below)
    question: [660, 880]                    # bare Hz -> preset's wave/env
    done: [[1046.5, 70, square, decay],     # full note shape:
           [1318.5, 130, square, held]]     # [freq, ms, waveform, envelope]
```

Top-level sections (siblings of `voicy:`):

```yaml
sound:
  enabled: true        # audio master switch: false = tray-only mode
tray:
  enabled: false       # desktop tray notifications, off by default
  backend: auto        # auto|notify-send|osascript
  urgency:             # per-event tray urgency
    question: critical
    done: normal
    error: normal
  body_max: 200        # notification body truncated to N chars + …
```

A leftover `tray.desktop` key (older versions, click-to-activate) is
ignored — the feature was removed because the desktop-entry hint
activated the wrong terminal session on GNOME. The tray is a plain,
notification-only channel now.

### Desktop tray notifications

Each event (question / done / error) fires a tray notification in
addition to its tone when `tray.enabled: true`. The body carries the
event's text — the clarify question, approval command, final response,
or API error — whitespace-collapsed and truncated to `tray.body_max`.
Backends: `notify-send` → `osascript` (first available; explicit
`tray.backend` wins). With no backend the channel is a silent no-op.
The per-event `voicy.question/done/error` toggles gate both channels;
`sound.enabled: false` keeps the tray while silencing all audio
(tray-only mode).

### Sound presets

Tones are synthesized from waveform/envelope/pitch-bend recipes of real
sound sources — retro consoles and sci-fi "power" motifs, not generic
beeps. Pick one with `voicy.presets` or in-session:

| Preset | Character |
|---|---|
| `nes` | NES / Famicom (2A03): square blips + noise percussion — punchy, sharp |
| `c64` | Commodore 64 (SID): bright square lead, warm triangle fanfare, gritty noise error |
| `gba` | Game Boy Color: soft triangle lead + square arp — gentle |
| `transform` | "Transformation sequence": rising saw power sweeps — the charge-up/morph sound |
| `cyber` | Cyberpunk / synthwave: piercing square alert, fast neon arp, glitch-cluster error |
| `bumblebee` (default) | Pachelbel's "Bumblebee": low buzzing G drone + fast square scale figure — the bee call |
| `legacy` | the original plain sine beeps |

- `/voicy presets` — list them; `/voicy use <preset>` — switch (writes
  `voicy.presets` and plays the new question tone);
- `/voicy set done 783.99 1046.5 1318.5` — audition custom frequencies
  now in the current preset style (one-off, not saved).

Per-tone `tones:` overrides beat the preset; bare-Hz overrides inherit
the active preset's waveform/envelope, so a one-liner still sounds
like the chosen console. Note shape: `[freq_hz, ms]`,
`[freq_hz, ms, waveform, envelope]`, or the 5-field form with an
optional **glide** — `[freq_hz, ms, waveform, envelope, glide_to_hz]` —
a linear pitch bend across the note (the sweeps in the `transform` and
`cyber` banks). Waveform: `sine|square|triangle|saw|noise`;
envelope: `decay|held|attack`. The verbose
`{freqs: [...], ms: N}` dict form still works.

### User sound files (your own music)

Prefer a real recording over a synthesized tone? Point a tone at a
file — it replaces synthesis for that tone only:

```yaml
voicy:
  sounds:
    question: ~/Sounds/ding.wav
    done: ~/Sounds/chime.wav
    # error left out -> synthesized as usual
```

- Put the files anywhere you like (`~` is expanded); `~/Sounds/` is a
  sensible home. WAV is the safe format for every backend (paplay,
  aplay, afplay, ffplay); OGG/FLAC also work on `paplay`.
- User files play **as-is** — `voicy.volume` does not scale them, so
  keep the level baked in (aim for roughly the loudness of the
  synthesized tones at your configured `voicy.volume`).
- A missing file silently falls back to synthesis (debug log).
- Files get a 30 s play timeout (beeps: 10 s), so a longer clip is
  not cut off. Keep them short anyway — they fire on every turn.
- Env override per tone: `HERMES_VOICY_QUESTION_SOUND=/path/...`
  (wins over `voicy.sounds`).

Environment overrides (highest precedence, useful for cron/CI):
`HERMES_VOICY_ENABLED`, `HERMES_VOICY_QUESTION`, `HERMES_VOICY_DONE`,
`HERMES_VOICY_ERROR`, `HERMES_VOICY_VOLUME`, `HERMES_VOICY_BACKEND`,
`HERMES_VOICY_PRESETS`, `HERMES_VOICY_QUESTION_MS`,
`HERMES_VOICY_DONE_MS`, `HERMES_VOICY_ERROR_MS`,
`HERMES_VOICY_QUESTION_SOUND`, `HERMES_VOICY_DONE_SOUND`,
`HERMES_VOICY_ERROR_SOUND`, `HERMES_VOICY_SOUND` (audio master),
`HERMES_VOICY_TRAY`, `HERMES_VOICY_TRAY_BACKEND`.

## Audio format

There is no audio format to choose: every tone is synthesized at runtime
to **16-bit mono PCM WAV at 22050 Hz** (pure stdlib, no binary assets, no
sox) and cached under `$XDG_CACHE_HOME/hermes-voicy/` (default
`~/.cache/hermes-voicy/`). Playback goes through the first available of
`paplay` → `afplay` → `play` → `aplay` → `ffplay` → terminal bell
(configurable via `voicy.backend`).

## In-session commands

- `/voicy` — status
- `/voicy test [question|done|error|all]` — play a tone now
- `/voicy presets` — list sound presets
- `/voicy use <preset>` — switch preset (persists + previews)
- `/voicy set <tone> <freqs...>` — audition custom frequencies
- `/voicy tray` — tray status
- `/voicy tray test [question|done|error]` — fire a test notification
- `/voicy tray on | off` — enable/disable tray (persists to config.yaml)
- `/voicy mute` / `/voicy unmute` — session-only master toggle

## Development

```sh
# any python 3.10+ with pytest; no other dependencies
python -m pytest tests/ -v
```

Layout: the repo root *is* the plugin directory (see
`docs/adr/0004-layout-and-workflow.md`); edit `*.py` files in place, sounds
are live on the next event.

- `docs/adr/0001-hook-selection.md` — which hooks, why
- `docs/adr/0002-sound-delivery.md` — synthesis + backend strategy
- `docs/adr/0003-configuration.md` — config surface & precedence
- `docs/adr/0004-layout-and-workflow.md` — loader mechanics & dev loop
- `docs/adr/0005-bounded-demotion-and-pytest-root-package.md` — backend
  demotion bounds + pytest root-package workaround
- `docs/adr/0006-retro-presets.md` — waveform/envelope synth + sound banks
  (nes/c64/gba/transform/cyber/bumblebee/legacy)
- `docs/adr/0008-user-sound-files.md` — `voicy.sounds` user audio files
- `docs/adr/0007-tray-notifications.md` — tray channel + sound master
  switch (notify-send / osascript)
