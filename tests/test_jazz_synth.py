import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voicing.engine import Engine
from voicing.types import ChordEvent, Song
from voicing.voicers import jazz_synth


def _engine():
    return Engine(
        jazz_synth.POLICY,
        {"bass_module_active": True, "section": "verse", "seed": 7},
        root_omission_gate=True,
        branch_b_requires_octave_dct=True,
    )


def test_jazz_synth_policy_and_shell_integrity():
    assert (jazz_synth.POLICY.window_lo, jazz_synth.POLICY.window_hi) == (40, 91)
    chord = ChordEvent(0, "minor", 0, "b7")
    voiced = _engine().run(Song("jazz", 0, 120, 1, (chord,)))[0]
    assert {"3rd", "7th"} <= set(voiced.roles) or "root" in voiced.roles


def test_jazz_synth_extended_pad_is_wide():
    chord = ChordEvent(7, "major", 0, "b7", "9")
    voiced = _engine().run(Song("jazz", 0, 120, 1, (chord,)))[0]
    assert max(voiced.midi) - min(voiced.midi) >= 7


def test_jazz_synth_sample_corpus():
    root = Path(__file__).parents[1] / "gen" / "jazz-labels"
    for path in sorted(root.glob("song_*.json"))[:3]:
        song = Song.from_dict(json.loads(path.read_text()))
        assert len(_engine().run(song)) == song.num_chords
