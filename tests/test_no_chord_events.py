import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from bass_module import BassModule
from chord_gen import apply_no_chord_filter, no_chord_summary
from chord_module import ChordModule
from percussion_module import PercussionModule
from voicing.engine import Engine
from voicing.types import ChordEvent, Song, VoicedChord, resolve_degrees
from voicing.voicers import pop_piano


def _event(root_interval=0, duration="w"):
    return {
        "is_no_chord": False,
        "root_interval": root_interval,
        "triad": "major",
        "bass_interval": 0,
        "seventh": "N",
        "ninth": "N",
        "eleventh": "N",
        "thirteenth": "N",
        "root": "C",
        "bass": "C",
        "harte": "C:maj",
        "duration_token": duration,
        "duration_seconds": 2.0,
        "time_start": 0.0,
        "time_end": 2.0,
    }


def test_exact_filter_is_deterministic_and_preserves_timing():
    songs = [[_event(0), _event(5, "h")], [_event(7)]]
    replaced = apply_no_chord_filter(songs, rate=2 / 3, seed=3)
    again = apply_no_chord_filter(songs, rate=2 / 3, seed=3)
    assert replaced == again
    selected = [
        (song_index, event_index, event)
        for song_index, song in enumerate(replaced)
        for event_index, event in enumerate(song)
        if event["is_no_chord"]
    ]
    assert len(selected) == 2
    for song_index, event_index, event in selected:
        original = songs[song_index][event_index]
        assert event["harte"] == "N"
        assert event["root_interval"] is event["triad"] is event["bass_interval"] is None
        assert event["root"] is event["bass"] is None
        assert event["duration_token"] == original["duration_token"]
        assert event["time_start"] == original["time_start"]
        assert event["time_end"] == original["time_end"]
    assert no_chord_summary(replaced)["event_count"] == 2


def test_no_chord_schema_rejects_harmonic_operations():
    chord = ChordEvent.from_dict({
        **_event(),
        "is_no_chord": True,
        "root_interval": None,
        "triad": None,
        "bass_interval": None,
        "root": None,
        "bass": None,
        "harte": "N",
    })
    with pytest.raises(ValueError, match="no-chord"):
        resolve_degrees(chord)
    with pytest.raises(ValueError, match="requires"):
        ChordEvent.from_dict({**_event(), "is_no_chord": False, "harte": "N"})


def test_engine_preserves_silence_slot_and_resets_continuity(monkeypatch):
    playable = ChordEvent.from_dict(_event())
    silence = ChordEvent.from_dict({
        **_event(), "is_no_chord": True, "root_interval": None, "triad": None,
        "bass_interval": None, "root": None, "bass": None, "harte": "N",
    })
    seen = []

    def step(self, index, chord, previous, previous_chord, center):
        seen.append((previous, previous_chord))
        return VoicedChord(index, "test", [60], ["root"], None, None, None, 0.0, 60.0), object()

    monkeypatch.setattr(Engine, "step", step)
    engine = Engine(pop_piano.POLICY, {"seed": 1})
    result = engine.run(Song("pop_rock", 0, 120, 3, (playable, silence, playable)))
    assert result[1] is None
    assert seen == [(None, None), (None, None)]
    assert engine.total_count == 0
    assert engine.silence_count == 1


def test_harmonic_tracks_rest_while_percussion_is_unchanged():
    playable = _event()
    silence = {
        **playable, "is_no_chord": True, "root_interval": None, "triad": None,
        "bass_interval": None, "root": None, "bass": None, "harte": "N",
    }
    progression = {"genre": "pop_rock", "tonic_pc": 0, "bpm": 120, "chords": [silence]}
    chord_track = ChordModule(seed=2).render(progression)
    assert chord_track.endswith("Rw")
    assert BassModule(seed=2).render(progression).endswith("Rw")
    assert PercussionModule(seed=2, omission_probability=0).render(
        {**progression, "chords": [playable]}
    ) == PercussionModule(seed=2, omission_probability=0).render(progression)
