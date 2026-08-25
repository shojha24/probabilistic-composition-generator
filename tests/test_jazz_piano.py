import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voicing.engine import Engine
from voicing.types import ChordEvent, Song
from voicing.voicers import jazz_piano


def _engine():
    return Engine(
        jazz_piano.POLICY,
        {"bass_module_active": True, "section": "verse", "seed": 11},
        root_omission_gate=True,
    )


def test_jazz_piano_policy_register_and_hand_limits():
    assert (jazz_piano.POLICY.window_lo, jazz_piano.POLICY.window_hi) == (48, 84)
    assert jazz_piano.POLICY.drift_tol == 8
    assert jazz_piano.POLICY.min_voices == 3


def test_triads_without_thirds_keep_a_root_and_fifth_shell():
    chord = ChordEvent(root_interval=0, triad="5", bass_interval=0)
    song = Song("jazz", 0, 120, 1, (chord,))
    voiced = _engine().run(song)[0]
    assert {"root", "5th"} <= set(voiced.roles)
    assert voiced.hands["lh"]


def test_altered_sus_shell_uses_collision_merged_third():
    chord = ChordEvent(7, "sus4", 0, "b7", "b9", "11", "13")
    voiced = _engine().run(Song("jazz", 0, 120, 1, (chord,)))[0]
    assert "7th" in voiced.roles
    assert any(role in voiced.roles for role in ("3rd", "11th"))
    assert set(voiced.hands["lh"]) <= set(voiced.midi)


def test_jazz_piano_sample_songs_voice_within_windows():
    root = Path(__file__).parents[1] / "gen" / "jazz-labels"
    for path in sorted(root.glob("song_*.json"))[:3]:
        song = Song.from_dict(json.loads(path.read_text()))
        voiced = _engine().run(song)
        assert len(voiced) == song.num_chords
        for chord in voiced:
            for hand in chord.hands.values():
                if hand:
                    assert max(hand) - min(hand) <= 12
            assert not (chord.hands["lh"] and chord.hands["rh"]
                        and min(chord.hands["rh"]) - max(chord.hands["lh"]) > 16)
