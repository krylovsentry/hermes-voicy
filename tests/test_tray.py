"""Tray channel tests: config resolution, notifier, hook fan-out."""
from conftest import config as cfgmod
from conftest import hooks, tray


class FakeNotifier:
    def __init__(self):
        self.fired = []

    def fire(self, title, body, urgency="normal", backend_pref="auto"):
        self.fired.append((title, body, urgency, backend_pref))

    def resolve(self, preference="auto"):
        return "notify-send"


def _cfg(tray_dict=None, sound=True, question=True, done=True, error=True):
    return cfgmod.VoicyConfig(
        enabled=True, question=question, done=done, error=error,
        volume=0.5, backend="paplay", bell=True, preset="nes",
        tones={"question": [660, 880], "done": [988], "error": [330, 277]},
        tone_ms={"question": 110, "done": 150, "error": 130},
        sounds={},
        sound=sound,
        tray=tray_dict,
    )


def _install(monkeypatch, cfg, fake_player=None):
    monkeypatch.setattr(cfgmod, "load", lambda: cfg)
    if fake_player is None:
        class FP:
            played = []

            def play_tone(self, wav, backend="auto", bell_allowed=True, **kwargs):
                self.played.append(str(wav))
        fake_player = FP()
    monkeypatch.setattr(hooks, "get_player", lambda: fake_player)
    fn = FakeNotifier()
    monkeypatch.setattr(hooks, "get_notifier", lambda: fn)
    return fn, fake_player


TRAY_ON = {
    "enabled": True, "backend": "auto",
    "urgency": {"question": "critical", "done": "normal", "error": "normal"},
    "body_max": 200,
}


def test_clarify_fires_tray_with_question_text(monkeypatch):
    fn, fp = _install(monkeypatch, _cfg(tray_dict=TRAY_ON))
    hooks.on_pre_tool_call(
        tool_name="clarify", args={"question": "Какой вариант?"})
    assert len(fn.fired) == 1
    title, body, urgency, _ = fn.fired[0]
    assert title == "Hermes: вопрос"
    assert body == "Какой вариант?"
    assert urgency == "critical"
    assert len(fp.played) == 1  # sound channel also fired


def test_done_fires_tray_with_response(monkeypatch):
    fn, _ = _install(monkeypatch, _cfg(tray_dict=TRAY_ON))
    hooks.on_post_llm_call(assistant_response="Всё сделано.")
    assert fn.fired and fn.fired[0][1] == "Всё сделано."
    assert fn.fired[0][0] == "Hermes: готово"


def test_sound_master_off_still_trays(monkeypatch):
    fn, fp = _install(monkeypatch, _cfg(tray_dict=TRAY_ON, sound=False))
    hooks.on_post_llm_call(assistant_response="Готово.")
    assert fn.fired, "tray must fire even when sound master is off"
    assert not fp.played


def test_tray_off_no_notifications(monkeypatch):
    fn, fp = _install(monkeypatch, _cfg(tray_dict=None))
    hooks.on_pre_tool_call(tool_name="clarify", args={"question": "?"})
    assert not fn.fired
    assert len(fp.played) == 1  # sound still plays


def test_leftover_desktop_key_ignored(monkeypatch):
    # Regression: `tray.desktop` was removed (click-to-activate activated
    # the wrong session on GNOME). A leftover key in config.yaml must not
    # leak into the resolved tray dict.
    monkeypatch.setattr(cfgmod, "_top_section", lambda name: {
        "desktop": "org.gnome.Terminal",
    } if name == "tray" else {})
    assert "desktop" not in cfgmod._tray_section()


def test_disabled_event_no_tray(monkeypatch):
    fn, fp = _install(monkeypatch,
                      _cfg(tray_dict=TRAY_ON, done=False))
    hooks.on_post_llm_call(assistant_response="x")
    assert not fn.fired
    assert not fp.played


def test_body_truncated_to_body_max(monkeypatch):
    cfg = _cfg(tray_dict={**TRAY_ON, "body_max": 10})
    fn, _ = _install(monkeypatch, cfg)
    hooks.on_post_llm_call(assistant_response="А" * 50)
    assert fn.fired[0][1] == "А" * 10 + "…"


def test_body_whitespaces_collapsed(monkeypatch):
    fn, _ = _install(monkeypatch, _cfg(tray_dict=TRAY_ON))
    hooks.on_post_llm_call(assistant_response="a\n  b\tc")
    assert fn.fired[0][1] == "a b c"


def test_error_event_tray(monkeypatch):
    fn, _ = _install(monkeypatch, _cfg(tray_dict=TRAY_ON))
    hooks.on_api_request_error(retryable=False, error_message="boom")
    assert fn.fired and fn.fired[0][0] == "Hermes: ошибка"
    assert fn.fired[0][1] == "boom"
    # retryable with retries left: silent on both channels
    fn.fired.clear()
    hooks.on_api_request_error(retryable=True, retry_count=1, max_retries=3)
    assert not fn.fired


# ---------------------------------------------------------------------------
# TrayNotifier unit behavior
# ---------------------------------------------------------------------------

def test_detect_backend_explicit_absent_returns_none(monkeypatch):
    monkeypatch.setattr(tray.shutil, "which", lambda p: None)
    assert tray.detect_backend("notify-send") is None
    assert tray.detect_backend("auto") is None


def test_detect_backend_auto_finds_first(monkeypatch):
    monkeypatch.setattr(tray.shutil, "which",
                        lambda p: "/bin/" + p if p == "osascript" else None)
    assert tray.detect_backend("auto") == "osascript"


def test_tray_cmd_shapes():
    cmd = tray._tray_cmd("notify-send", "T", "B", "critical")
    assert cmd == ["notify-send", "-a", "Hermes", "-u", "critical", "T", "B"]
    cmd = tray._tray_cmd("osascript", "T", "B", "normal")
    assert cmd[0] == "osascript"
    assert 'display notification "B" with title "T"' in cmd[2]


def test_fire_never_raises_without_backend(monkeypatch):
    monkeypatch.setattr(tray.shutil, "which", lambda p: None)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)  # must NOT spawn a real notify-send (the test
        # process inherits DISPLAY and would pop a live "Т B" bubble)
        return object()

    monkeypatch.setattr(tray.subprocess, "run", fake_run)
    n = tray.TrayNotifier()
    n.fire("T", "B")  # must be a silent no-op (no backend resolved)
    assert not calls
    n._send_sync("T", "B", "normal", "notify-send")  # wedged: no raise
    assert len(calls) == 1  # reached subprocess, fake consumed it
