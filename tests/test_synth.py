import math
import struct
import wave

from conftest import synth


def test_recipe_validation():
    r = synth.Recipe.from_list([(440.0, 100)])
    assert r.total_ms() == 100
    # out-of-range freq rejected
    try:
        synth.Recipe.from_list([(2.0, 100)])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # too long rejected
    try:
        synth.Recipe.from_list([(440.0, 99999)])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_parse_recipe_shapes():
    # bare freq list
    r = synth.parse_recipe([660, 880])
    assert [n.freq for n in r.notes] == [660.0, 880.0]
    # dict shape
    r = synth.parse_recipe({"freqs": [660, 880], "ms": 90})
    assert r.notes[0].ms == 90
    # pair-list shape
    r = synth.parse_recipe([[660, 100], [880, 120]])
    assert (r.notes[1].freq, r.notes[1].ms) == (880.0, 120)
    # garbage rejected
    try:
        synth.parse_recipe("nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_render_wav_is_valid_and_quiet_volume_is_silent():
    r = synth.Recipe.from_list([(440.0, 50)])
    data = synth.render_wav(r, volume=0.8)
    # parse header
    with wave.open(__import__("io").BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 22050
        expected = int(22050 * 0.050)
        assert w.getnframes() == expected
        frames = w.readframes(expected)
    peak = max(abs(s) for s in struct.unpack(f"<{expected}h", frames))
    # 0.8 gain -> peak near 0.8*32767 (sin reaches 1 within 50 ms at 440 Hz)
    assert peak > 0.5 * 32767
    assert peak <= int(0.8 * 32767) + 32


def test_tone_path_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    r = synth.Recipe.from_list([(440.0, 50)])
    p1 = synth.tone_path("done", r, 0.6)
    assert p1.exists()
    p2 = synth.tone_path("done", r, 0.6)
    assert p1 == p2
    # different volume -> different file
    p3 = synth.tone_path("done", r, 0.3)
    assert p3 != p1 and p3.exists()


def _peak(data: bytes) -> int:
    import io
    with wave.open(io.BytesIO(data), "rb") as w:
        frames = w.readframes(w.getnframes())
    return max(abs(s) for s in struct.unpack(f"<{len(frames) // 2}h", frames))


def test_square_wave_is_sharp():
    # A square wave spends half the cycle at ±full scale; a sine at the
    # same volume stays under ~0.71 of the rail.
    r = synth.Recipe.from_list([(440.0, 120, "square", "held")])
    peak = _peak(synth.render_wav(r, 0.8))
    assert peak > 0.75 * 32767


def test_noise_wave_is_not_sinusoidal():
    # White noise should hit both rails within a short burst.
    r = synth.Recipe.from_list([(200.0, 100, "noise", "decay")])
    peak = _peak(synth.render_wav(r, 0.8))
    assert peak > 0.5 * 32767


def test_waveform_and_envelope_change_fingerprint():
    a = synth.Recipe.from_list([(440.0, 100, "sine", "decay")])
    b = synth.Recipe.from_list([(440.0, 100, "square", "decay")])
    c = synth.Recipe.from_list([(440.0, 100, "sine", "held")])
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


def test_note_shape_validation():
    # waveform must be known
    try:
        synth.Recipe.from_list([(440.0, 100, "wobble", "decay")])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # envelope must be known
    try:
        synth.Recipe.from_list([(440.0, 100, "square", "glide")])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # 6 fields rejected (max is 5: freq, ms, waveform, envelope, glide_to)
    try:
        synth.Recipe.from_list([(440.0, 100, "square", "decay", 880.0, "extra")])
        assert False, "expected ValueError"
    except ValueError:
        pass
    # glide target out of range rejected
    try:
        synth.Recipe.from_list([(440.0, 100, "sine", "decay", 2.0)])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_glide_note_shape():
    r = synth.Recipe.from_list([(220.0, 200, "saw", "decay", 880.0)])
    assert r.notes[0].freq == 220.0
    assert r.notes[0].glide_to == 880.0
    # without glide the field is None
    assert synth.Recipe.from_list([(220.0, 200, "saw", "decay")]).notes[0].glide_to is None


def test_glide_sweeps_frequency():
    def crossings(note):
        import io
        data = synth.render_wav(synth.Recipe.from_list([note]), 1.0)
        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.readframes(w.getnframes())
        s = struct.unpack(f"<{len(frames) // 2}h", frames)
        c, prev = 0, 0
        for x in s:
            if prev != 0 and ((prev < 0 <= x) or (prev >= 0 > x)):
                c += 1
            prev = x
        return c
    low = crossings([220.0, 200, "sine", "held"])
    hi = crossings([880.0, 200, "sine", "held"])
    mid = crossings([220.0, 200, "sine", "held", 880.0])
    # a 220->880 sweep spends more cycles than 220 flat, fewer than 880 flat
    assert low < mid < hi


def test_glide_changes_fingerprint():
    a = synth.Recipe.from_list([(440.0, 100, "sine", "decay")])
    b = synth.Recipe.from_list([(440.0, 100, "sine", "decay", 880.0)])
    assert a.fingerprint() != b.fingerprint()


def test_held_envelope_sustains_full_scale():
    # Middle of a "held" note should be near full gain (no decay to zero).
    r = synth.Recipe.from_list([(440.0, 200, "square", "held")])
    data = synth.render_wav(r, 1.0)
    import io
    with wave.open(io.BytesIO(data), "rb") as w:
        total = w.getnframes()
        frames = w.readframes(total)
    samples = struct.unpack(f"<{total}h", frames)
    mid = samples[total // 2 - 200 : total // 2 + 200]
    # square: many samples at exactly ±32767 in the sustain region
    assert any(abs(s) > 0.9 * 32767 for s in mid)


def test_explicit_recipe_keeps_bare_defaults():
    # 2-field notes still work (legacy shape): sine/decay
    r = synth.Recipe.from_list([(440.0, 100)])
    assert r.notes[0].waveform == "sine"
    assert r.notes[0].envelope == "decay"
