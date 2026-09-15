"""Retro preset bank tests: shapes, resolution, override dressing."""
from conftest import presets, synth


def test_all_presets_have_valid_banks():
    for name in presets.list_names():
        p = presets.get(name)
        for tone in ("question", "done", "error"):
            recipe = presets.resolve_tone(p, tone, None)
            assert recipe.total_ms() > 0
            for n in recipe.notes:
                assert n.waveform in synth.WAVEFORMS
                assert n.envelope in synth.ENVELOPES
                assert 30.0 <= n.freq <= 10000.0
                assert 20 <= n.ms <= 1500


def test_unknown_preset_raises():
    try:
        presets.get("x68000")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_legacy_preset_is_old_sine_sound():
    p = presets.get("legacy")
    recipe = presets.resolve_tone(p, "done", None)
    assert [n.freq for n in recipe.notes] == [987.77]
    assert all(n.waveform == "sine" for n in recipe.notes)


def test_bare_freq_override_gets_preset_idiom():
    p = presets.get("nes")
    recipe = presets.resolve_tone(p, "done", [660.0, 880.0])
    assert [n.freq for n in recipe.notes] == [660.0, 880.0]
    # bare freqs are dressed with the preset's waveform/envelope
    assert all(n.waveform == "square" for n in recipe.notes)
    assert all(n.envelope == "decay" for n in recipe.notes)


def test_explicit_waveform_override_wins():
    p = presets.get("nes")
    recipe = presets.resolve_tone(p, "done", [[440.0, 100, "triangle", "held"]])
    assert recipe.notes[0].waveform == "triangle"
    assert recipe.notes[0].envelope == "held"


def test_garbage_override_falls_back_to_preset_bank():
    p = presets.get("nes")
    recipe = presets.resolve_tone(p, "error", "not-a-tone")
    bank = presets.resolve_tone(p, "error", None)
    assert recipe.notes == bank.notes


def test_presets_render_to_wav():
    for name in presets.list_names():
        p = presets.get(name)
        for tone in ("question", "done", "error"):
            data = synth.render_wav(presets.resolve_tone(p, tone, None), 0.6)
            assert data[:4] == b"RIFF"
            assert len(data) > 100


def test_sci_fi_banks_present_and_use_glide():
    names = presets.list_names()
    assert "transform" in names
    assert "cyber" in names
    # transform is built on pitch-bend sweeps
    t = presets.get("transform")
    q = presets.resolve_tone(t, "question", None)
    assert any(n.glide_to is not None for n in q.notes)
    # done has a rising power sweep
    d = presets.resolve_tone(t, "done", None)
    assert any(n.glide_to is not None and n.glide_to > n.freq for n in d.notes)


def test_cyber_bank_shapes():
    c = presets.get("cyber")
    q = presets.resolve_tone(c, "question", None)
    assert q.notes[0].freq > 1000  # piercing high alert
    assert all(n.waveform in synth.WAVEFORMS for n in q.notes)


def test_bumblebee_bank_shapes():
    b = presets.get("bumblebee")
    for tone in ("question", "done", "error"):
        recipe = presets.resolve_tone(b, tone, None)
        assert recipe.total_ms() > 0
    # drone notes are low buzz, figure notes are the fast scale
    q = presets.resolve_tone(b, "question", None)
    assert q.notes[0].freq < 150 and q.notes[0].waveform == "saw"
    assert all(n.waveform == "square" for n in q.notes[1:])
    d = presets.resolve_tone(b, "done", None)
    assert d.notes[0].freq < 150 and d.notes[0].waveform == "saw"
    # error is a descending "dying buzz": each note glides downward
    e = presets.resolve_tone(b, "error", None)
    glides = [n for n in e.notes if n.glide_to is not None]
    assert len(glides) == 3
    assert all(n.glide_to < n.freq for n in glides)
    # and the drone idiom dresses bare-freq overrides (saw/decay)
    r = presets.resolve_tone(b, "done", [440.0, 554.37])
    assert all(n.waveform == "saw" and n.envelope == "decay" for n in r.notes)
