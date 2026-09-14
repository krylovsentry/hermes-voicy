"""Hook callbacks: map Hermes lifecycle events to output (sound + tray).

Event -> output mapping (ADR-0001, extended by ADR-0007):
  pre_tool_call (tool_name == "clarify")  -> question
  pre_approval_request                    -> question
  post_llm_call (turn finished, no error) -> done
  api_request_error (terminal failure)    -> error   [opt-in]

Each event fans out to both channels when enabled:
- audio  (top-level ``sound:`` master + per-event toggle)
- tray   (top-level ``tray:`` section, independent per event)

Every callback is defensive: any exception is swallowed (Hermes does too,
but we keep the log quiet on the happy path).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import config as cfgmod
from . import synth
from .player import get_player, CUSTOM_SONG_TIMEOUT, PLAY_TIMEOUT
from .tray import get_notifier

logger = logging.getLogger("hermes-voicy.hooks")

TONE_BY_EVENT = {
    "question": "question",
    "done": "done",
    "error": "error",
}

TITLE_BY_EVENT = {
    "question": "Hermes: вопрос",
    "done": "Hermes: готово",
    "error": "Hermes: ошибка",
}


def _body_text(event: str, cfg: cfgmod.VoicyConfig, text: str) -> str:
    """Notification body: trimmed to cfg.tray.body_max chars."""
    limit = (cfg.tray or {}).get("body_max", 200)
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _fire(event: str, cfg: cfgmod.VoicyConfig, text: str = "") -> None:
    """Emit one event through every enabled channel. Never raises."""
    try:
        if not cfg.enabled:
            return
        if event == "question" and not cfg.question:
            return
        if event == "done" and not cfg.done:
            return
        if event == "error" and not cfg.error:
            return
        tone_name = TONE_BY_EVENT[event]

        # Channel 1: audio (gated by the top-level `sound:` master switch)
        if cfg.sound:
            # User audio file wins over synthesis (config.sounds / env).
            wav = cfgmod.sound_file(cfg, tone_name)
            if wav is None:
                recipe = cfgmod.tone_recipe(cfg, tone_name)
                wav = synth.tone_path(tone_name, recipe, cfg.volume)
                timeout = PLAY_TIMEOUT
            else:
                timeout = CUSTOM_SONG_TIMEOUT  # user music may run longer
            get_player().play_tone(wav, cfg.backend, bell_allowed=cfg.bell,
                                   timeout=timeout)
            logger.debug("hermes-voicy: played %s (backend=%s)",
                         tone_name, cfg.backend)

        # Channel 2: desktop tray (independent per event)
        tray = cfg.tray or {}
        if tray.get("enabled"):
            body = _body_text(event, cfg, text)
            get_notifier().fire(
                TITLE_BY_EVENT[event],
                body,
                urgency=tray.get("urgency", {}).get(event, "normal"),
                backend_pref=tray.get("backend", "auto"),
            )
            logger.debug("hermes-voicy: tray %s (backend=%s)",
                         event, tray.get("backend"))
    except Exception as exc:  # never break the agent loop
        logger.debug("hermes-voicy: failed on %s: %s", event, exc)


# ---------------------------------------------------------------------------
# Hook entry points (signatures match invoke_hook kwargs, see ADR-0001)
# ---------------------------------------------------------------------------

def on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    """Output when the agent is about to block on a user question (clarify)."""
    if tool_name != "clarify":
        return
    text = ""
    if isinstance(args, dict):
        text = str(args.get("question") or "")
    _fire("question", cfgmod.load(), text)


def on_pre_approval_request(
    command: str = "",
    description: str = "",
    pattern_key: str = "",
    pattern_keys: list = None,
    session_key: str = "",
    surface: str = "",
    **_: Any,
) -> None:
    """Output when an approval prompt is about to be shown."""
    # 'smart' surface = auxiliary LLM decides, no human prompt is shown.
    if surface == "smart":
        return
    _fire("question", cfgmod.load(),
          description or f"approve: {command}")


def on_post_llm_call(
    session_id: str = "",
    task_id: str = "",
    turn_id: str = "",
    user_message: str = "",
    assistant_response: str = "",
    model: str = "",
    platform: str = "",
    **_: Any,
) -> None:
    """Output when a turn completed (work finished)."""
    _fire("done", cfgmod.load(), assistant_response)


def on_api_request_error(
    task_id: str = "",
    turn_id: str = "",
    api_request_id: str = "",
    error_type: str = "",
    error_message: str = "",
    status_code: int = None,
    retry_count: int = 0,
    max_retries: int = 0,
    retryable: bool = True,
    reason: str = "",
    **_: Any,
) -> None:
    """Output on terminal API failures only (retry exhausted / non-retryable)."""
    terminal = (not retryable) or (max_retries and retry_count >= max_retries)
    if not terminal:
        return
    _fire("error", cfgmod.load(), error_message or error_type or "API error")
