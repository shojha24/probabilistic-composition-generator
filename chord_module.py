"""Build JFugue chord tracks from generated chord progressions."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from instruments import CHORD_INSTRUMENTS, Instrument
from voicing.engine import Engine, VoicingImpossible
from voicing.types import Song
from voicing.voicers import jazz_guitar, jazz_piano, jazz_synth
from voicing.voicers import pop_guitar, pop_piano, pop_synth


logger = logging.getLogger(__name__)


POLICIES = {
    ("jazz", "piano"): jazz_piano.POLICY,
    ("jazz", "guitar"): jazz_guitar.POLICY,
    ("jazz", "synth"): jazz_synth.POLICY,
    ("pop_rock", "piano"): pop_piano.POLICY,
    ("pop_rock", "guitar"): pop_guitar.POLICY,
    ("pop_rock", "synth"): pop_synth.POLICY,
}
CHORD_FAMILY_WEIGHTS = {"guitar": 0.46, "piano": 0.27, "synth": 0.27}

VOICER_SPECS = {
    policy.voicer_id: (genre, family, policy)
    for (genre, family), policy in POLICIES.items()
}


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
    last_voicer_genre: str | None = field(default=None, init=False)
    last_voicer_family: str | None = field(default=None, init=False)
    last_instrument: str | None = field(default=None, init=False)
    last_instrument_program: int | None = field(default=None, init=False)
    selected_instrument: Instrument | None = field(default=None, init=False)
    last_voiced_midis: list[list[int]] = field(default_factory=list, init=False)
    last_voiced_roles: list[list[str]] = field(default_factory=list, init=False)
    last_voicing_diagnostics: list[dict] = field(default_factory=list, init=False)

    def render(
        self,
        progression: dict | Song,
        preferred_family: str | None = None,
        bass_module_active: bool = False,
        voicer_order: list[str] | tuple[str, ...] | None = None,
    ) -> str:
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
        if voicer_order is not None:
            if not isinstance(voicer_order, (list, tuple)) or not voicer_order:
                raise ValueError("voicer_order must be a non-empty list or tuple")
            if any(not isinstance(voicer, str) for voicer in voicer_order):
                raise ValueError("voicer_order entries must be strings")
            if len(set(voicer_order)) != len(voicer_order):
                raise ValueError("voicer_order must not contain duplicate voicers")
            unknown_voicers = [
                voicer for voicer in voicer_order
                if voicer not in VOICER_SPECS
            ]
            if unknown_voicers:
                raise ValueError(
                    f"Unknown voicer(s) in voicer_order: {unknown_voicers}"
                )
            candidate_specs = [
                VOICER_SPECS[voicer] for voicer in voicer_order
            ]
        else:
            if preferred_family in families:
                families.remove(preferred_family)
                rng.shuffle(families)
                families.insert(0, preferred_family)
            else:
                # Non-target songs may not carry a voicer hint; malformed hints
                # should have the same safe behavior as an absent one.
                primary = rng.choices(
                    families,
                    weights=[CHORD_FAMILY_WEIGHTS[family] for family in families],
                    k=1,
                )[0]
                families.remove(primary)
                rng.shuffle(families)
                families.insert(0, primary)
            candidate_specs = [
                (song.genre, family, POLICIES[(song.genre, family)])
                for family in families
            ]
        failures = []
        voiced = None
        family = None
        selected_policy = None
        selected_instrument: Instrument | None = None
        for candidate_genre, candidate_family, policy in candidate_specs:
            engine = Engine(
                policy,
                {
                    "seed": rng.randrange(2**32),
                    "bass_module_active": bool(bass_module_active),
                },
                root_omission_gate=(
                    song.genre == "jazz" and bool(bass_module_active)
                ),
            )
            try:
                voiced = engine.run(song)
                family = candidate_family
                selected_policy = policy
                eligible = [
                    item for item in CHORD_INSTRUMENTS[family]
                    if self.mode in item.roles
                ]
                selected_instrument = rng.choice(eligible)
                break
            except VoicingImpossible as error:
                failure = f"{policy.voicer_id}: {error}"
                failures.append(failure)
                logger.warning(
                    "Voicer fallback failed for %s progression: %s",
                    song.genre,
                    failure,
                )
        if (
            voiced is None
            or family is None
            or selected_policy is None
            or selected_instrument is None
        ):
            details = "\n".join(f"  - {failure}" for failure in failures)
            raise RuntimeError(
                f"No supported {song.genre} instrument could voice the progression:\n"
                f"{details}"
            )
        self.last_voicer = selected_policy.voicer_id
        self.last_voicer_genre = selected_policy.genre
        self.last_voicer_family = family
        self.last_instrument = selected_instrument.name
        self.last_instrument_program = selected_instrument.program
        self.selected_instrument = selected_instrument
        self.last_voiced_midis = [list(chord.midi) for chord in voiced]
        self.last_voiced_roles = [list(chord.roles) for chord in voiced]
        self.last_voicing_diagnostics = [
            dict(chord.diagnostics) for chord in voiced
        ]
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        tokens = [
            chord_token(chord.midi, event.get("duration_token", "w")
                        if isinstance(event, dict) else "w")
            for chord, event in zip(voiced, events)
        ]
        return f"T{song.bpm} V0 I{selected_instrument.program} " + " ".join(tokens)
