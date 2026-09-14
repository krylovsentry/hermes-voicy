"""Sound-file (user music) resolution: voicy.sounds + env override."""
import pathlib
import tempfile

from conftest import FakePlayer
from conftest import config as cfgmod
from conftest import hooks
from conftest import player as player_mod


def _cfg(tmp_path, sounds):
    return cfgmod.VoicyConfig(
        enabled=True, question=True, done=True, error=True,
        volume=0.5, backend="paplay", bell=True,
        preset="nes",
        tones={"question": [660, 880], "done": [988], "error": [330, 277]},
        tone_ms={"question": 110, "done": 150, "error": 130},
        sounds=sounds,
    )


def test_no_sounds_means_synthesis(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, {})
    monkeypatch.delenv("HERMES_VOICY_QUESTION_SOUND", raising=False)
    assert cfgmod.sound_file(cfg, "question") is None


def test_sound_file_resolves_expanded_path(tmp_path, monkeypatch):
    f = tmp_path / "ding.wav"
    f.write_bytes(b"RIFFxxxxWAVEfmt ")
    cfg = _cfg(tmp_path, {"question": str(f)})
    monkeypatch.delenv("HERMES_VOICY_QUESTION_SOUND", raising=False)
    assert cfgmod.sound_file(cfg, "question") == f


def test_missing_file_falls_back_to_synthesis(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, {"done": str(tmp_path / "nope.wav")})
    monkeypatch.delenv("HERMES_VOICY_DONE_SOUND", raising=False)
    assert cfgmod.sound_file(cfg, "done") is None


def test_env_overrides_config_file(tmp_path, monkeypatch):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(b"RIFF")
    b.write_bytes(b"RIFF")
    cfg = _cfg(tmp_path, {"question": str(a)})
    monkeypatch.setenv("HERMES_VOICY_QUESTION_SOUND", str(b))
    assert cfgmod.sound_file(cfg, "question") == b
    # env pointing at a missing file does NOT fall back to config:
    # env is an explicit (albeit stale) user choice -> synthesis
    monkeypatch.setenv("HERMES_VOICY_QUESTION_SOUND", str(tmp_path / "gone.wav"))
    assert cfgmod.sound_file(cfg, "question") is None


def test_load_parses_sounds_section(monkeypatch):
    """`load()` picks up voicy.sounds from config (here via a fake section)."""
    monkeypatch.setattr(
        cfgmod, "_cfg_section",
        lambda: {
            "sounds": {
                "question": "~/Sounds/ding.wav",
                "done": 123,            # not a string -> ignored
                "bogus": "~/x.wav",     # unknown tone -> ignored
            }
        },
    )
    cfg = cfgmod.load()
    assert cfg.sounds == {"question": "~/Sounds/ding.wav"}


def test_tone_path_uses_custom_timeout(monkeypatch):
    """Hooks play user files with the longer custom-song timeout."""
    import conftest

    fp = FakePlayer()
    monkeypatch.setattr(hooks, "get_player", lambda: fp)

    tmp = pathlib.Path(tempfile.mkdtemp())
    f = tmp / "song.wav"
    f.write_bytes(b"RIFFxxxxWAVEfmt ")
    cfg = cfgmod.VoicyConfig(
        enabled=True, question=True, done=True, error=True,
        volume=0.5, backend="paplay", bell=True,
        preset="nes",
        tones={}, tone_ms={"question": 110, "done": 150, "error": 130},
        sounds={"done": str(f)},
    )
    monkeypatch.setattr(conftest.config, "load", lambda: cfg)
    monkeypatch.delenv("HERMES_VOICY_DONE_SOUND", raising=False)

    hooks.on_post_llm_call(session_id="s1")
    assert len(fp.played) == 1
    assert fp.played[0] == str(f)
    assert fp.timeouts == [player_mod.CUSTOM_SONG_TIMEOUT]

    # synthesized tones keep the short timeout
    cfg2 = cfgmod.VoicyConfig(
        enabled=True, question=True, done=True, error=True,
        volume=0.5, backend="paplay", bell=True,
        preset="nes",
        tones={}, tone_ms={"question": 110, "done": 150, "error": 130},
        sounds={},
    )
    monkeypatch.setattr(conftest.config, "load", lambda: cfg2)
    hooks.on_post_llm_call(session_id="s2")
    assert fp.timeouts[-1] == player_mod.PLAY_TIMEOUT
