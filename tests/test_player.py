"""Player backend resolution, demotion and async dispatch."""
import time

from conftest import player


def test_detect_backend_prefers_paplay(monkeypatch):
    monkeypatch.setattr(
        player.shutil, "which",
        lambda b: "/usr/bin/" + b if b in {"paplay", "aplay", "tput"} else None,
    )
    assert player.detect_backend("auto") == "paplay"
    # explicit preference wins even if a "better" one exists
    assert player.detect_backend("aplay") == "aplay"
    # explicit but absent -> bell
    assert player.detect_backend("ffplay") == "bell"
    # no backends at all -> bell if TERM set
    monkeypatch.setattr(player.shutil, "which", lambda b: "/usr/bin/tput" if b == "tput" else None)
    monkeypatch.setenv("TERM", "xterm")
    assert player.detect_backend("auto") == "bell"


def test_no_backend_returns_none(monkeypatch):
    monkeypatch.setattr(player.shutil, "which", lambda b: None)
    monkeypatch.delenv("TERM", raising=False)
    assert player.detect_backend("auto") is None


def test_play_tone_dispatches_to_subprocess(monkeypatch):
    player.reset_player_for_tests()
    p = player.get_player()
    calls = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(player.shutil, "which", lambda b: "/usr/bin/paplay")
    monkeypatch.setattr(player.subprocess, "run", fake_run)
    p.resolve("auto")
    assert p.backend == "paplay"

    p.play_tone("/tmp/fake.wav", "auto")
    # wait for the worker thread
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls and calls[0][0] == "paplay" and calls[0][1] == "/tmp/fake.wav"


def test_failed_backend_demotes(monkeypatch):
    player.reset_player_for_tests()
    p = player.get_player()
    monkeypatch.setattr(player.shutil, "which", lambda b: "/usr/bin/" + b if b in {"paplay", "aplay"} else None)
    calls = []

    def fake_run(*a, **_):
        calls.append(a[0][0])
        raise TimeoutError("x")

    monkeypatch.setattr(player.subprocess, "run", fake_run)
    p.resolve("auto")
    assert p.backend == "paplay"
    # synchronous path: paplay fails -> demote -> retry aplay -> aplay fails
    p._play_sync(__import__("pathlib").Path("/tmp/fake.wav"), "paplay")
    assert calls == ["paplay", "aplay"]  # exactly one retry, no cycle
    assert p.backend is None  # all real backends dead -> mute, never recurse
