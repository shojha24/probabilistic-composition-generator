import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voicing.engine import Engine
from voicing.types import ChordEvent, Song
from voicing.voicers import jazz_guitar


def _engine():
    return Engine(
        jazz_guitar.POLICY,
        {"bass_module_active": True, "section": "verse", "seed": 7},
        root_omission_gate=True,
    )


def test_jazz_guitar_policy_and_rootless_shell():
    assert (jazz_guitar.POLICY.window_lo, jazz_guitar.POLICY.window_hi) == (45, 79)
    chord = ChordEvent(0, "minor", 0, "b7")
    voiced = _engine().run(Song("jazz", 0, 120, 1, (chord,)))[0]
    assert {"3rd", "7th"} <= set(voiced.roles) or "root" in voiced.roles


def test_jazz_guitar_extended_shape_is_playable():
    chord = ChordEvent(7, "major", 0, "b7", "#9")
    voiced = _engine().run(Song("jazz", 0, 120, 1, (chord,)))[0]
    assert len(voiced.midi) <= 5
    assert all(0 <= fret <= 15 for fret in voiced.diagnostics.get("frets", []))


def test_jazz_guitar_sample_corpus():
    root = Path(__file__).parents[1] / "gen" / "jazz-labels"
    for path in sorted(root.glob("song_*.json"))[:10]:
        song = Song.from_dict(json.loads(path.read_text()))
        assert len(_engine().run(song)) == song.num_chords

