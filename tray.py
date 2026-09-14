"""Desktop-tray notification delivery (ADR-0007).

Second output channel alongside audio: a system notification via the
freedesktop spec (notify-send on Linux, osascript display notification
on macOS). Same design rules as the audio player:

- Delivery runs on a daemon thread pool; hook threads never block.
- A backend that fails once is demoted for the rest of the process
  (monotonic dead-set, no retry cycles).
- Every subprocess is bounded (10 s) and nothing ever raises out of
  fire(); failures are logged at debug at most.

The tray channel is opt-in per event via config (``tray.question`` etc.),
independent of whether audio also plays. Click-to-activate was dropped
(the desktop-entry hint activated the wrong session on GNOME); the tray
is a plain, notification-only channel now.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("hermes-voicy.tray")

TRAY_TIMEOUT = 10.0  # seconds; bounds a wedged notification backend
APP_NAME = "Hermes"

_BACKENDS = ("notify-send", "osascript")


def _tray_cmd(backend: str, title: str, body: str, urgency: str) -> list[str]:
    if backend == "notify-send":
        return ["notify-send", "-a", APP_NAME, "-u", urgency, title, body]
    # osascript: title + body in one display notification (no click
    # activation on macOS; the arg is ignored)
    script = f'display notification "{body}" with title "{title}"'
    return ["osascript", "-e", script]


def detect_backend(preference: str = "auto") -> Optional[str]:
    """Resolve the tray backend. Returns a name from _BACKENDS or None."""
    pref = (preference or "auto").strip().lower()
    if pref in _BACKENDS:
        return pref if shutil.which(pref) else None  # explicit but absent -> none
    for b in _BACKENDS:
        if shutil.which(b):
            return b
    return None


class TrayNotifier:
    """Process-lifetime tray state: resolved backend + worker pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend: Optional[str] = None
        self._dead: set[str] = set()
        self._executor: Optional[ThreadPoolExecutor] = None

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    def resolve(self, preference: str = "auto") -> Optional[str]:
        with self._lock:
            if self._backend is None:
                self._backend = detect_backend(preference)
                if self._backend is None:
                    logger.debug("hermes-voicy: no tray backend available")
            return self._backend

    def _demote(self, dead: str) -> None:
        with self._lock:
            self._dead.add(dead)
            order = [b for b in _BACKENDS if b not in self._dead and shutil.which(b)]
            self._backend = order[0] if order else None
            logger.debug(
                "hermes-voicy: tray backend %s failed (dead=%s), now %s",
                dead, sorted(self._dead), self._backend,
            )

    def fire(self, title: str, body: str,
             urgency: str = "normal",
             backend_pref: str = "auto") -> None:
        """Fire-and-forget notification. Never raises."""
        backend = self.resolve(backend_pref)
        if backend is None:
            return
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="voicy-tray"
            )
        self._executor.submit(self._send_sync, title, body, urgency, backend)

    def _send_sync(self, title: str, body: str, urgency: str,
                   backend: str) -> None:
        try:
            subprocess.run(
                _tray_cmd(backend, title, body, urgency),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=TRAY_TIMEOUT,
                check=False,
            )
        except Exception as exc:
            logger.debug("hermes-voicy: %s tray send failed: %s", backend, exc)
            self._demote(backend)


_notifier = TrayNotifier()


def get_notifier() -> TrayNotifier:
    return _notifier


def reset_notifier_for_tests() -> None:
    global _notifier
    _notifier = TrayNotifier()
