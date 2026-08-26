"""Build a bass track from generated chord progression events."""
from __future__ import annotations

from dataclasses import dataclass

from voicing.types import Song
from chord_module import midi_to_jfugue


@dataclass
class BassModule:
    instrument: int = 33

    def render(self, progression: dict | Song) -> str:
        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        tokens = []
        for event in events:
            bass_pc = (event["root_interval"] + event.get("bass_interval", 0) + song.tonic_pc) % 12
            midi = 36 + bass_pc
            duration = event.get("duration_token", "w")
            tokens.append(f"{midi_to_jfugue(midi)}{duration}")
        return f"T{song.bpm} I{self.instrument} " + " ".join(tokens)
