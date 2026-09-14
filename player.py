"""Audio backend resolution and async playback.

See docs/adr/0002-sound-delivery.md.

Design rules:
- Backend resolution happens once per process (sticky). An explicit
  ``backend`` config skips auto-detection.
- A backend that fails at play-time is demoted once for the rest of the
  process, so a dead PulseAudio socket doesn't cost a timeout per event.
- Playback runs on a daemon thread pool so hook threads never block on
  audio. The bell fallback is inline (cheap).
- Nothing here ever raises out of play_tone(); failures are logged at
  debug level at most.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes-voicy.player")

PLAY_TIMEOUT = 10.0  # seconds; bounds a wedged player
CUSTOM_SONG_TIMEOUT = 30.0  # user-provided music files may be longer

_BACKENDS = ("paplay", "afplay", "play", "aplay", "ffplay")


def _play_cmd(backend: str, wav: Path) -> list[str]:
    if backend == "ffplay":
        # -autoexit: quit when audio ends; -nodisp/-loglevel quiet: headless
        return ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(wav)]
    return [backend, str(wav)]


def detect_backend(preference: str = "auto") -> Optional[str]:
    """Resolve the play backend. Returns a name from _BACKENDS or 'bell'."""
    pref = (preference or "auto").strip().lower()
    if pref == "bell":
        return "bell"
    if pref in _BACKENDS:
        return pref if shutil.which(pref) else "bell"  # explicit but absent -> bell
    for b in _BACKENDS:
        if shutil.which(b):
            return b
    return "bell" if _bell_available() else None


def _bell_available() -> bool:
    if shutil.which("tput"):
        return True
    # Last resort: raw BEL to stderr works in any tty.
    return bool(os.environ.get("TERM"))


def _ring_bell() -> None:
    try:
        if shutil.which("tput"):
            subprocess.run(
                ["tput", "bel"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            return
    except Exception:
        pass
    try:
        os.write(2, b"\a")
    except Exception:
        pass


class Player:
    """Process-lifetime playback state: resolved backend + worker pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend: Optional[str] = None
        self._dead: set[str] = set()  # backends that failed in this process
        self._executor: Optional[ThreadPoolExecutor] = None

    # -- backend state ---------------------------------------------------
    @property
    def backend(self) -> Optional[str]:
        return self._backend

    def resolve(self, preference: str = "auto") -> Optional[str]:
        with self._lock:
            if self._backend is None:
                self._backend = detect_backend(preference)
                if self._backend is None:
                    logger.debug("hermes-voicy: no audio backend available")
            return self._backend

    def _demote(self, dead: str) -> None:
        """Mark *dead* as failed (sticky) and pick the next live backend.

        The dead-set is monotonic per process: a backend that failed once
        is never retried, which bounds the demotion chain and makes it
        impossible to cycle A -> B -> A -> ...
        """
        with self._lock:
            self._dead.add(dead)
            order = [b for b in _BACKENDS if b not in self._dead and shutil.which(b)]
            if dead == "bell" or not order:
                self._backend = None
            else:
                self._backend = order[0]
            logger.debug(
                "hermes-voicy: backend %s failed (dead=%s), now %s",
                dead, sorted(self._dead), self._backend,
            )

    # -- playback ---------------------------------------------------------
    def play_tone(self, wav: Path, backend_pref: str = "auto", bell_allowed: bool = True,
                  timeout: float = PLAY_TIMEOUT) -> None:
        """Fire-and-forget tone playback. Never raises.

        *timeout* bounds a wedged player; synthesized beeps are short and
        use PLAY_TIMEOUT, while user-provided music files get
        CUSTOM_SONG_TIMEOUT (they may legitimately run longer).
        """
        backend = self.resolve(backend_pref)
        if backend is None:
            return
        if backend == "bell":
            if bell_allowed:
                _ring_bell()
            else:
                self._demote("bell")
            return
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="voicy"
            )
        self._executor.submit(self._play_sync, wav, backend, bell_allowed, timeout)

    def _play_sync(self, wav: Path, backend: str, bell_allowed: bool = True,
                   timeout: float = PLAY_TIMEOUT) -> None:
        try:
            subprocess.run(
                _play_cmd(backend, wav),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except Exception as exc:
            logger.debug("hermes-voicy: %s playback failed: %s", backend, exc)
            self._demote(backend)
            # One retry on the demoted backend so this event isn't lost.
            with self._lock:
                retry = self._backend
            if retry is not None and retry != backend:
                if retry == "bell":
                    if bell_allowed:
                        _ring_bell()
                    else:
                        self._demote("bell")
                else:
                    self._play_sync(wav, retry, bell_allowed, timeout)


# Module-level singleton so all hook callbacks share backend state.
_player = Player()


def get_player() -> Player:
    return _player


def reset_player_for_tests() -> None:
    global _player
    _player = Player()
