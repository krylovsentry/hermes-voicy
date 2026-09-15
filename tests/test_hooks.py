"""Hook-level tests: event -> tone mapping with a stubbed player."""
import conftest

from conftest import FakePlayer
from conftest import hooks


def _install_fake(monkeypatch):
    fp = FakePlayer()
    monkeypatch.setattr(hooks, "get_player", lambda: fp)
    # force question/done/error all on, volume fixed
    fake_cfg = conftest.config.VoicyConfig(
        enabled=True, question=True, done=True, error=True,
        volume=0.5, backend="paplay", bell=True,
        preset="nes",
        tones={"question": [660, 880], "done": [988], "error": [330, 277]},
        tone_ms={"question": 110, "done": 150, "error": 130},
        sounds={},
    )
    monkeypatch.setattr(conftest.config, "load", lambda: fake_cfg)
    return fp


def test_clarify_fires_question(monkeypatch):
    fp = _install_fake(monkeypatch)
    hooks.on_pre_tool_call(tool_name="clarify", args={"question": "?"})
    assert len(fp.played) == 1 and "question" in fp.played[0]
    # other tools stay silent
    hooks.on_pre_tool_call(tool_name="terminal", args={"command": "ls"})
    assert len(fp.played) == 1


def test_approval_fires_question_but_smart_surface_does_not(monkeypatch):
    fp = _install_fake(monkeypatch)
    hooks.on_pre_approval_request(command="rm -rf /tmp/x", surface="cli")
    assert len(fp.played) == 1
    hooks.on_pre_approval_request(command="rm -rf /tmp/x", surface="smart")
    assert len(fp.played) == 1  # no human prompt on smart surface


def test_post_llm_call_fires_done(monkeypatch):
    fp = _install_fake(monkeypatch)
    hooks.on_post_llm_call(session_id="s1", assistant_response="Done!")
    assert len(fp.played) == 1 and "done" in fp.played[0]


def test_api_error_only_on_terminal_failure(monkeypatch):
    fp = _install_fake(monkeypatch)
    # retryable, retries left -> silent
    hooks.on_api_request_error(retryable=True, retry_count=1, max_retries=3)
    assert not fp.played
    # retries exhausted -> sound
    hooks.on_api_request_error(retryable=True, retry_count=3, max_retries=3)
    assert len(fp.played) == 1 and "error" in fp.played[0]
    # non-retryable -> sound immediately
    hooks.on_api_request_error(retryable=False, retry_count=0, max_retries=3)
    assert len(fp.played) == 2


def test_disabled_events_stay_silent(monkeypatch):
    fake_cfg = conftest.config.VoicyConfig(
        enabled=True, question=False, done=True, error=False,
        volume=0.5, backend="paplay", bell=True,
        preset="nes",
        tones={"question": [660, 880], "done": [988], "error": [330, 277]},
        tone_ms={"question": 110, "done": 150, "error": 130},
        sounds={},
    )
    monkeypatch.setattr(conftest.config, "load", lambda: fake_cfg)
    fp = FakePlayer()
    monkeypatch.setattr(hooks, "get_player", lambda: fp)
    hooks.on_pre_tool_call(tool_name="clarify", args={})
    hooks.on_api_request_error(retryable=False)
    assert not fp.played
    hooks.on_post_llm_call(session_id="s1")
    assert len(fp.played) == 1
