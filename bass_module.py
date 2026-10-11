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
        collapse_draw = (
            rng.random() if pad_instrument is not None else None
        )
        self.collapsed_to_pad = (
            pad_mode
            and pad_instrument is not None
            and collapse_draw is not None
            and collapse_draw < self.pad_collapse_probability
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

            has_next = (
                not self.collapsed_to_pad
                and index + 1 < len(events)
            )
            next_event = events[index + 1] if has_next else None
            next_is_no_chord = (
                (
                    next_event.is_no_chord if isinstance(next_event, ChordEvent)
                    else next_event.get("is_no_chord", next_event.get("harte") == "N")
                )
                if next_event is not None
                else True
            )

            if has_next and not next_is_no_chord and next_event is not None:
                next_root = next_event["root_interval"] if isinstance(next_event, dict) else next_event.root_interval
                next_bass = next_event.get("bass_interval", 0) if isinstance(next_event, dict) else next_event.bass_interval
                next_bass_pc = (next_root + next_bass + song.tonic_pc) % 12
                next_midi = 36 + next_bass_pc
                approach_midi = rng.choice([next_midi - 1, next_midi + 1])
                fifth_midi = midi + 7 if midi + 7 <= 48 else midi - 5
                third_midi = midi + 4 if midi + 4 <= 48 else midi - 8

                pattern_roll = rng.random()
                if duration == "w":
                    if pattern_roll < 0.25:
                        # Chromatic approach on beat 4
                        tokens.append(f"{midi_to_jfugue(midi)}h. {midi_to_jfugue(approach_midi)}q")
                    elif pattern_roll < 0.50:
                        # Scalar walking line across 4 beats
                        tokens.append(
                            f"{midi_to_jfugue(midi)}q {midi_to_jfugue(third_midi)}q "
                            f"{midi_to_jfugue(fifth_midi)}q {midi_to_jfugue(approach_midi)}q"
                        )
                    elif pattern_roll < 0.70:
                        # Root-fifth alternation
                        tokens.append(f"{midi_to_jfugue(midi)}h {midi_to_jfugue(fifth_midi)}h")
                    else:
                        tokens.append(f"{midi_to_jfugue(midi)}w")
                elif duration == "h.":
                    if pattern_roll < 0.30:
                        tokens.append(f"{midi_to_jfugue(midi)}h {midi_to_jfugue(approach_midi)}q")
                    else:
                        tokens.append(f"{midi_to_jfugue(midi)}h.")
                elif duration == "h":
                    if pattern_roll < 0.25:
                        tokens.append(f"{midi_to_jfugue(midi)}q {midi_to_jfugue(approach_midi)}q")
                    else:
                        tokens.append(f"{midi_to_jfugue(midi)}h")
                else:
                    tokens.append(f"{midi_to_jfugue(midi)}{duration}")
            else:
                tokens.append(f"{midi_to_jfugue(midi)}{duration}")
        return f"V1 I{program} " + " ".join(tokens)
