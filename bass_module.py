"""Build a bass track from generated chord progression events."""
from __future__ import annotations

import random
from dataclasses import dataclass

from instruments import BASS_INSTRUMENTS, Instrument
from voicing.types import ChordEvent, Song
from chord_module import midi_to_jfugue


@dataclass
class BassModule:
    instrument: int | None = None
    seed: int | None = None
    pad_collapse_probability: float = 0.25
    last_instrument: Instrument | None = None
    collapsed_to_pad: bool = False

    def render(
        self,
        progression: dict | Song,
        pad_instrument: Instrument | None = None,
        pad_mode: bool = False,
        chord_midis: list[list[int]] | None = None,
    ) -> str:
        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        if not 0.0 <= self.pad_collapse_probability <= 1.0:
            raise ValueError("pad_collapse_probability must be between 0 and 1")
        rng = random.Random(self.seed)
        self.collapsed_to_pad = (
            pad_mode
            and pad_instrument is not None
            and rng.random() < self.pad_collapse_probability
        )
        if self.collapsed_to_pad:
            self.last_instrument = pad_instrument
            program = pad_instrument.program
        elif self.instrument is None:
            self.last_instrument = rng.choice(BASS_INSTRUMENTS)
            program = self.last_instrument.program
        else:
            program = self.instrument
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        tokens = []
        for index, event in enumerate(events):
            is_no_chord = (
                event.is_no_chord if isinstance(event, ChordEvent)
                else event.get("is_no_chord", event.get("harte") == "N")
            )
            duration = event.get("duration_token", "w") if isinstance(event, dict) else event.duration_token
            if is_no_chord:
                tokens.append(f"R{duration}")
                continue
            root_interval = event["root_interval"] if isinstance(event, dict) else event.root_interval
            bass_interval = event.get("bass_interval", 0) if isinstance(event, dict) else event.bass_interval
            bass_pc = (root_interval + bass_interval + song.tonic_pc) % 12
            midi = 36 + bass_pc
            chord_pitches = chord_midis[index] if chord_midis and index < len(chord_midis) else ()
            if 40 <= midi <= 47 and any(40 <= pitch <= 47 for pitch in chord_pitches):
                midi -= 12
            tokens.append(f"{midi_to_jfugue(midi)}{duration}")
        return f"V1 I{program} " + " ".join(tokens)
