"""hermes-voicy — audible + desktop-tray feedback for Hermes sessions.

Sounds and optional tray notifications when the agent asks a question
(clarify / approval prompt) or finishes a turn; optional terminal-error
signal.

Loader contract: the Hermes plugin loader imports this module as
``hermes_plugins.hermes_voicy`` and calls :func:`register` with a
``PluginContext``. See docs/adr/0004-layout-and-workflow.md.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import hooks

logger = logging.getLogger("hermes-voicy")

_HELP = """\
/voicy — audible feedback status & manual test

Usage:
  /voicy                  show current config + active backend
  /voicy test [tone]      play a tone now (tone: question|done|error|all)
  /voicy presets          list retro sound presets
  /voicy use <preset>     switch preset, write to config.yaml, preview
  /voicy set question <freqs>   play a custom tone now (no config change)
  /voicy tray                   tray status
  /voicy tray test [event]      fire a test tray notification
  /voicy tray on | off          enable/disable tray, persist to config.yaml
  /voicy mute | unmute          toggle the master switch for this session

User music files: set `voicy.sounds` in config.yaml, e.g.
  sounds: {question: ~/Sounds/ding.wav, done: ~/Sounds/chime.wav}
(overrides synthesis per tone; env: HERMES_VOICY_<TONE>_SOUND).

Presets: nes | c64 | gba | transform | cyber | bumblebee | legacy
  (retro-console 8-bit banks + sci-fi 'transformation'/'cyberpunk' banks
  + Pachelbel's "Bumblebee" drone).
"""

_KNOWN_TONES = ("question", "done", "error")


def _tray_status_lines() -> str:
    from . import config as cfgmod
    from .tray import get_notifier

    cfg = cfgmod.load()
    tray = cfg.tray or {}
    backend = get_notifier().resolve(tray.get("backend", "auto"))
    lines = [
        "hermes-voicy tray:",
        f"  enabled : {tray.get('enabled')}",
        f"  backend : {backend or 'none'}   (config: {tray.get('backend')})",
        f"  urgency : {', '.join(f'{k}={v}' for k, v in tray.get('urgency', {}).items())}",
        f"  body    : max {tray.get('body_max')} chars",
        "  config  : `tray:` section in config.yaml "
        "(or HERMES_VOICY_TRAY / HERMES_VOICY_TRAY_BACKEND)",
    ]
    return "\n".join(lines)


def _write_tray_enabled(enabled: bool) -> str:
    """Persist tray.enabled=<bool>; returns an error string or ''."""
    try:
        import yaml  # noqa: F401
        from hermes_cli.config import (
            atomic_config_write,
            get_config_path,
        )

        path = get_config_path()
        data = {}
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return f"config.yaml at {path} is not a mapping; refusing to touch it"
        section = data.get("tray")
        if not isinstance(section, dict):
            section = {}
        section["enabled"] = bool(enabled)
        data["tray"] = section
        atomic_config_write(path, data, sort_keys=False)
        return ""
    except Exception as exc:
        return f"could not write tray.enabled: {exc}"


def _status_line() -> str:
    from . import config as cfgmod
    from . import presets as presets_mod
    from .player import get_player

    cfg = cfgmod.load()
    backend = get_player().resolve(cfg.backend)
    preset = presets_mod.get(cfg.preset)
    lines = [
        "hermes-voicy status:",
        f"  enabled : {cfg.enabled}",
        f"  question: {cfg.question}   done: {cfg.done}   error: {cfg.error}",
        f"  volume  : {cfg.volume:.2f}   backend: {backend or 'none'}",
        f"  preset  : {cfg.preset} — {preset.blurb}",
        f"  tones   : {', '.join(n for n in _KNOWN_TONES if cfg.tones.get(n)) or 'preset bank'}",
        f"  sounds  : {', '.join(f'{n}={cfg.sounds[n]}' for n in _KNOWN_TONES if n in cfg.sounds) or '—'}",
        f"  sound   : {cfg.sound} (master)   tray: {(cfg.tray or {}).get('enabled')}",
        "  config  : `voicy:` section in config.yaml "
        "(or HERMES_VOICY_* env vars)",
    ]
    return "\n".join(lines)


def _presets_lines() -> str:
    from . import config as cfgmod
    from . import presets as presets_mod

    cfg = cfgmod.load()
    lines = ["Retro presets:"]
    for name in presets_mod.list_names():
        p = presets_mod.get(name)
        mark = "*" if name == cfg.preset else " "
        lines.append(f" {mark} {name:6s} — {p.blurb}")
    lines.append("\nSwitch with: /voicy use <name>   (writes voicy.presets)")
    return "\n".join(lines)


def _write_preset(name: str) -> str:
    """Persist voicy.presets=<name>; returns an error string or ''."""
    try:
        import yaml  # noqa: F401
        from hermes_cli.config import (
            atomic_config_write,
            get_config_path,
        )

        path = get_config_path()
        data = {}
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return f"config.yaml at {path} is not a mapping; refusing to touch it"
        section = data.get("voicy")
        if not isinstance(section, dict):
            section = {}
        section["presets"] = name
        data["voicy"] = section
        atomic_config_write(path, data, sort_keys=False)
        return ""
    except Exception as exc:
        return f"could not write voicy.presets: {exc}"


def _parse_freqs(text: str):
    """'660 880' or '660,880' -> [660.0, 880.0]; None on garbage."""
    import re

    parts = re.split(r"[,\s]+", text.strip())
    if not parts or parts == [""]:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def _handle_slash(raw_args: str) -> Optional[str]:
    from . import config as cfgmod
    from . import presets as presets_mod
    from . import synth
    from . import player as player_mod
    from .player import get_player

    args = raw_args.strip().split()
    if not args or args[0] in {"help", "-h", "--help"}:
        return _HELP
    cmd = args[0]

    if cmd == "test":
        cfg = cfgmod.load()
        names = list(_KNOWN_TONES) if (len(args) > 1 and args[1] == "all") \
            else [args[1]] if len(args) > 1 else ["question"]
        for name in names:
            if name not in _KNOWN_TONES:
                return f"Unknown tone {name!r}. Use: question | done | error | all"
            cfg = cfgmod.load()
            custom = cfgmod.sound_file(cfg, name)
            try:
                if custom is not None:
                    get_player().play_tone(
                        custom, cfg.backend, bell_allowed=cfg.bell,
                        timeout=player_mod.CUSTOM_SONG_TIMEOUT,
                    )
                    return f"Played custom sound for '{name}': {custom}"
                recipe = cfgmod.tone_recipe(cfg, name)
                wav = synth.tone_path(name, recipe, cfg.volume)
            except Exception as exc:
                return f"Could not build tone {name!r}: {exc}"
            get_player().play_tone(wav, cfg.backend, bell_allowed=cfg.bell)
            backend = get_player().backend
            return f"Played '{name}' via backend '{backend}'."
        return _status_line()

    if cmd == "presets":
        return _presets_lines()

    if cmd == "use":
        if len(args) != 2:
            return "Usage: /voicy use <preset>   (see /voicy presets)"
        name = args[1].strip().lower()
        try:
            preset = presets_mod.get(name)
        except KeyError:
            return f"Unknown preset {args[1]!r}. Use: {', '.join(presets_mod.list_names())}"
        err = _write_preset(name)
        if err:
            return err
        # Preview the new preset's question tone so the change is audible.
        cfg = cfgmod.load()
        try:
            recipe = presets_mod.resolve_tone(preset, "question", None)
            wav = synth.tone_path("question", recipe, cfg.volume)
            get_player().play_tone(wav, cfg.backend, bell_allowed=cfg.bell)
        except Exception as exc:
            logger.debug("hermes-voicy: preview failed: %s", exc)
        return (f"Preset set to '{name}' — {preset.blurb}\n"
                f"(written to voicy.presets in config.yaml; previewed 'question')")

    if cmd == "set":
        # /voicy set <tone> <freqs...> — audible now, does NOT persist.
        if len(args) < 3:
            return "Usage: /voicy set <question|done|error> <freq1> [freq2 ...]\n" \
                   "(plays a one-off tone in the current preset style; " \
                   "to persist, edit `voicy.tones` in config.yaml)"
        name, freqs_text = args[1].lower(), " ".join(args[2:])
        if name not in _KNOWN_TONES:
            return f"Unknown tone {args[1]!r}. Use: question | done | error"
        freqs = _parse_freqs(freqs_text)
        if not freqs:
            return f"Could not parse frequencies: {freqs_text!r}"
        try:
            cfg = cfgmod.load()
            preset = presets_mod.get(cfg.preset)
            recipe = presets_mod.resolve_tone(preset, name, freqs)
            wav = synth.tone_path(name, recipe, cfg.volume)
        except Exception as exc:
            return f"Could not build tone {name!r}: {exc}"
        get_player().play_tone(wav, cfg.backend, bell_allowed=cfg.bell)
        return f"Played one-off {name} {[int(f) for f in freqs]} " \
               f"(preset '{cfg.preset}' style). Not saved."

    if cmd == "tray":
        # /voicy tray | tray test [event] | tray on | tray off
        sub = args[1].lower() if len(args) > 1 else ""
        if not sub:
            return _tray_status_lines()
        if sub == "test":
            from .tray import get_notifier
            event = args[2].lower() if len(args) > 2 else "done"
            if event not in ("question", "done", "error"):
                return f"Unknown event {args[2]!r}. Use: question | done | error"
            cfg = cfgmod.load()
            tray = cfg.tray or {}
            text = {"question": "Тестовый вопрос",
                    "done": "Тестовое завершение",
                    "error": "Тестовая ошибка"}[event]
            get_notifier().fire(
                hooks.TITLE_BY_EVENT[event],
                text,
                urgency=tray.get("urgency", {}).get(event, "normal"),
                backend_pref=tray.get("backend", "auto"),
            )
            backend = get_notifier().resolve(tray.get("backend", "auto"))
            return (f"Fired test tray '{event}' via '{backend or 'none'}'. "
                    f"Note: tray.enabled is {tray.get('enabled')}.")
        if sub in ("on", "off"):
            err = _write_tray_enabled(sub == "on")
            if err:
                return err
            state = "enabled" if sub == "on" else "disabled"
            return (f"Tray {state} (written to tray.enabled in config.yaml; "
                    f"takes effect immediately).")
        return "Usage: /voicy tray [test [question|done|error] | on | off]"

    if cmd in {"mute", "unmute"}:
        # Session-only toggle: env var override for the current process.
        import os
        os.environ["HERMES_VOICY_ENABLED"] = "false" if cmd == "mute" else "true"
        state = "muted for this session" if cmd == "mute" else "unmuted for this session"
        return f"hermes-voicy {state}. (Persistent: set voicy.enabled in config.yaml.)"

    return _status_line()


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin loader."""
    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("pre_approval_request", hooks.on_pre_approval_request)
    ctx.register_hook("post_llm_call", hooks.on_post_llm_call)
    ctx.register_hook("api_request_error", hooks.on_api_request_error)
    ctx.register_command(
        "voicy",
        handler=_handle_slash,
        description="Audible feedback: status, tone test, retro presets, mute.",
        args_hint="[test [question|done|error|all]] | presets | use <preset> "
                  "| set <tone> <freqs...> | tray [test|on|off] | mute | unmute",
    )
    logger.debug("hermes-voicy registered (question/done/error sounds)")
