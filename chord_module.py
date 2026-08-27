"""Build JFugue chord tracks from generated chord progressions."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from instruments import CHORD_INSTRUMENTS, Instrument
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
CHORD_FAMILY_WEIGHTS = {"guitar": 0.46, "piano": 0.27, "synth": 0.27}
def midi_to_jfugue(midi: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def chord_token(midis: list[int], duration: str) -> str:
    if not midis:
        return f"R{duration}"
    notes = [midi_to_jfugue(midi) for midi in midis]
    return "+".join(f"{note}{duration}" for note in notes)


@dataclass
class ChordModule:
    """Voices one progression with one randomly selected genre instrument."""

    mode: str = "pads"
    seed: int | None = None
    last_voicer: str | None = field(default=None, init=False)
    last_instrument: str | None = field(default=None, init=False)
    last_instrument_program: int | None = field(default=None, init=False)
    selected_instrument: Instrument | None = field(default=None, init=False)
    last_voiced_midis: list[list[int]] = field(default_factory=list, init=False)

    def render(self, progression: dict | Song) -> str:
        if self.mode not in {"pads", "arpeggios"}:
            raise ValueError("mode must be 'pads' or 'arpeggios'")
        if self.mode == "arpeggios":
            raise NotImplementedError("Arpeggio rendering is reserved for the next module revision")

        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        rng = random.Random(self.seed)
        families = [
            family for family, instruments in CHORD_INSTRUMENTS.items()
            if any(self.mode in instrument.roles for instrument in instruments)
        ]
        primary = rng.choices(
            families,
            weights=[CHORD_FAMILY_WEIGHTS[family] for family in families],
            k=1,
        )[0]
        families.remove(primary)
        rng.shuffle(families)
        families.insert(0, primary)
        failures = []
        voiced = None
        family = None
        selected_instrument: Instrument | None = None
        for candidate_family in families:
            policy = POLICIES[(song.genre, candidate_family)]
            engine = Engine(policy, {"seed": rng.randrange(2**32)})
            try:
                voiced = engine.run(song)
                family = candidate_family
                eligible = [
                    item for item in CHORD_INSTRUMENTS[family]
                    if self.mode in item.roles
                ]
                selected_instrument = rng.choice(eligible)
                break
            except VoicingImpossible as error:
                failures.append(f"{candidate_family}: {error}")
        if voiced is None or family is None or selected_instrument is None:
            details = "; ".join(failures)
            raise RuntimeError(
                f"No supported {song.genre} instrument could voice the progression: {details}"
            )
        self.last_voicer = policy.voicer_id
        self.last_instrument = selected_instrument.name
        self.last_instrument_program = selected_instrument.program
        self.selected_instrument = selected_instrument
        self.last_voiced_midis = [list(chord.midi) for chord in voiced]
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        tokens = [
            chord_token(chord.midi, event.get("duration_token", "w")
                        if isinstance(event, dict) else "w")
            for chord, event in zip(voiced, events)
        ]
        return f"T{song.bpm} V0 I{selected_instrument.program} " + " ".join(tokens)
