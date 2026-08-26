"""Build JFugue chord tracks from generated chord progressions."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from voicing.engine import Engine, VoicingImpossible
from voicing.types import Song
from voicing.voicers import jazz_guitar, jazz_piano, jazz_synth
from voicing.voicers import pop_guitar, pop_piano, pop_synth


POLICIES = {
    ("jazz", "piano"): jazz_piano.POLICY,
    ("jazz", "guitar"): jazz_guitar.POLICY,
    ("jazz", "synth"): jazz_synth.POLICY,
    ("pop_rock", "piano"): pop_piano.POLICY,
    ("pop_rock", "guitar"): pop_guitar.POLICY,
    ("pop_rock", "synth"): pop_synth.POLICY,
}
INSTRUMENTS = {"piano": 0, "guitar": 24, "synth": 88}


def midi_to_jfugue(midi: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def chord_token(midis: list[int], duration: str) -> str:
    if not midis:
        return f"R{duration}"
    notes = [midi_to_jfugue(midi) for midi in midis]
    return "+".join(notes[:-1] + [notes[-1] + duration])


@dataclass
class ChordModule:
    """Voices one progression with one randomly selected genre instrument."""

    mode: str = "pads"
    seed: int | None = None
    last_voicer: str | None = field(default=None, init=False)
    last_instrument: str | None = field(default=None, init=False)

    def render(self, progression: dict | Song) -> str:
        if self.mode not in {"pads", "arpeggios"}:
            raise ValueError("mode must be 'pads' or 'arpeggios'")
        if self.mode == "arpeggios":
            raise NotImplementedError("Arpeggio rendering is reserved for the next module revision")

        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        rng = random.Random(self.seed)
        instruments = list(INSTRUMENTS)
        rng.shuffle(instruments)
        failures = []
        voiced = None
        instrument = None
        for candidate_instrument in instruments:
            policy = POLICIES[(song.genre, candidate_instrument)]
            engine = Engine(policy, {"seed": rng.randrange(2**32)})
            try:
                voiced = engine.run(song)
                instrument = candidate_instrument
                break
            except VoicingImpossible as error:
                failures.append(f"{candidate_instrument}: {error}")
        if voiced is None or instrument is None:
            details = "; ".join(failures)
            raise RuntimeError(
                f"No supported {song.genre} instrument could voice the progression: {details}"
            )
        self.last_voicer = policy.voicer_id
        self.last_instrument = instrument
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        tokens = [
            chord_token(chord.midi, event.get("duration_token", "w")
                        if isinstance(event, dict) else "w")
            for chord, event in zip(voiced, events)
        ]
        return f"T{song.bpm} I{INSTRUMENTS[instrument]} " + " ".join(tokens)
