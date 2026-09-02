"""Build JFugue chord tracks from generated chord progressions."""
from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from chord_gen import DURATION_BEATS
from instruments import (
    ARPEGGIO_PROFILES,
    CHORD_INSTRUMENTS,
    ArpeggioProfile,
    Instrument,
    validate_arpeggio_instrument_catalog,
)
from voicing.engine import Engine, VoicingImpossible
from voicing.types import ChordEvent, Song, VoicedChord
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


_ARPEGGIO_PATTERN_FAMILIES = ("up", "down", "up_down", "outside_in")
_ARPEGGIO_MOTIF_FAMILIES = (
    "straight",
    "bass_return",
    "top_return",
    "light_alternate",
)
_ARPEGGIO_GRID_STEPS = {"sixteenth": 1, "eighth": 2}
_ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS = 2


def _event_duration_token(event: ChordEvent | dict) -> str:
    if isinstance(event, ChordEvent):
        return event.duration_token
    if isinstance(event, dict):
        return event.get("duration_token", "w")
    raise TypeError("events must contain ChordEvent or dict values")


def _event_is_no_chord(event: ChordEvent | dict) -> bool:
    if isinstance(event, ChordEvent):
        return event.is_no_chord
    if isinstance(event, dict):
        return event.get("is_no_chord", event.get("harte") == "N")
    raise TypeError("events must contain ChordEvent or dict values")


def sixteenth_slot_count(event: ChordEvent | dict) -> int:
    """Return the sixteenth-note slots occupied by one source event."""
    duration = _event_duration_token(event)
    if not isinstance(duration, str):
        raise ValueError(f"Unknown duration token {duration!r}")
    try:
        beats = DURATION_BEATS[duration]
    except KeyError:
        raise ValueError(f"Unknown duration token {duration!r}") from None
    slots = beats * 4
    if not float(slots).is_integer():
        raise ValueError(
            f"Duration token {duration!r} cannot be represented on a "
            "sixteenth-note arpeggio grid"
        )
    return int(slots)


def _arpeggio_cycle(family: str, voice_count: int) -> list[int]:
    if (
        isinstance(voice_count, bool)
        or not isinstance(voice_count, int)
        or voice_count < 1
    ):
        raise ValueError("arpeggio voice_count must be a positive integer")
    if voice_count == 1:
        return [0]
    if family == "up":
        return list(range(voice_count))
    if family == "down":
        return list(range(voice_count - 1, -1, -1))
    if family == "up_down":
        return list(range(voice_count)) + list(range(voice_count - 2, 0, -1))
    if family == "outside_in":
        cycle = []
        left, right = 0, voice_count - 1
        while left <= right:
            cycle.append(left)
            if right != left:
                cycle.append(right)
            left += 1
            right -= 1
        return cycle
    raise ValueError(f"Unknown arpeggio pattern family {family!r}")


@dataclass(frozen=True)
class ArpeggioAttack:
    """One scheduled arpeggio note, measured in sixteenth-note units."""

    event_index: int
    midi: int
    onset_sixteenths: int
    duration_sixteenths: float
    velocity: int
    source_index: int


def _weighted_family(
    weights: Mapping[str, float] | Sequence[tuple[str, float]],
    rng: random.Random,
) -> str:
    if isinstance(weights, Mapping):
        choices = tuple(
            name for name, weight in weights.items() if float(weight) > 0
        )
        values = tuple(float(weights[name]) for name in choices)
    else:
        items = tuple(weights)
        choices = tuple(name for name, weight in items if float(weight) > 0)
        values = tuple(
            float(weight) for name, weight in items if float(weight) > 0
        )
    if not choices:
        raise ValueError("arpeggio weights must contain a positive value")
    return rng.choices(choices, weights=values, k=1)[0]


def _weighted_family_other(
    weights: dict[str, float],
    current: str,
    rng: random.Random,
) -> str:
    alternatives = tuple(
        (name, float(weight))
        for name, weight in weights.items()
        if name != current and float(weight) > 0
    )
    if not alternatives:
        return current
    return _weighted_family(alternatives, rng)


def _motif_minimum_voices(motif_family: str) -> int:
    if motif_family in {"bass_return", "top_return"}:
        return 3
    if motif_family == "light_alternate":
        return 4
    if motif_family == "straight":
        return 1
    raise ValueError(f"Unknown arpeggio motif family {motif_family!r}")


def _motif_cycle(
    pattern_family: str,
    motif_family: str,
    voice_count: int,
    dct_position: int | None = None,
) -> list[int]:
    """Return a bounded traversal cycle of ordered voice positions."""
    base = []
    for position in _arpeggio_cycle(pattern_family, voice_count):
        if position not in base:
            base.append(position)
    if voice_count < _motif_minimum_voices(motif_family):
        return base
    if motif_family == "straight":
        return base
    if motif_family == "bass_return":
        return base
    if motif_family == "top_return":
        return base
    if motif_family == "light_alternate":
        return base
    raise ValueError(f"Unknown arpeggio motif family {motif_family!r}")


def _select_motif(
    weights: dict[str, float],
    voice_count: int,
    rng: random.Random,
) -> str:
    applicable = tuple(
        (name, float(weight))
        for name, weight in weights.items()
        if float(weight) > 0
        and voice_count >= _motif_minimum_voices(name)
    )
    if not applicable:
        return "straight"
    if len(applicable) == 1:
        return applicable[0][0]
    return _weighted_family(applicable, rng)


def _ordered_source_indices(
    chord: VoicedChord,
    profile: ArpeggioProfile,
    event_index: int,
) -> list[int]:
    midis = list(chord.midi)
    if not midis:
        raise ValueError(
            f"Playable event #{event_index} has no selected MIDI pitches"
        )
    if any(
        isinstance(pitch, bool) or not isinstance(pitch, int)
        for pitch in midis
    ):
        raise ValueError(
            f"Playable event #{event_index} has invalid selected MIDI pitches"
        )
    if profile.family != "guitar":
        return sorted(range(len(midis)), key=lambda index: (midis[index], index))

    diagnostics = chord.diagnostics
    strings = diagnostics.get("sounding_strings") if diagnostics else None
    if not isinstance(strings, (list, tuple)) or len(strings) != len(midis):
        raise ValueError(
            f"Guitar arpeggio event #{event_index} requires sounding_strings "
            "aligned with the selected MIDI pitches"
        )
    if any(
        isinstance(string, bool)
        or not isinstance(string, int)
        or string < 1
        or string > 6
        for string in strings
    ) or len(set(strings)) != len(strings):
        raise ValueError(
            f"Guitar arpeggio event #{event_index} has invalid sounding_strings"
        )
    # The voicing engine stores strings in pitch order. Physical ascending
    # traversal runs from string 6 toward string 1.
    return sorted(
        range(len(midis)),
        key=lambda index: (-strings[index], index),
    )


def _arpeggio_rng_streams(
    rng: random.Random,
    pattern_rng: random.Random | None,
    motif_rng: random.Random | None,
    change_rng: random.Random | None,
) -> tuple[random.Random, random.Random, random.Random]:
    if pattern_rng is not None and motif_rng is not None and change_rng is not None:
        return pattern_rng, motif_rng, change_rng
    if hasattr(rng, "getstate"):
        state_seed = int.from_bytes(
            hashlib.sha256(repr(rng.getstate()).encode("utf-8")).digest()[:16],
            "big",
        )
        derived = {
            label: _child_rng(state_seed, label)
            for label in (
                "arpeggio-profile-pattern",
                "arpeggio-profile-motif",
                "arpeggio-profile-change",
            )
        }
    else:
        derived = {}
    return (
        pattern_rng or derived.get("arpeggio-profile-pattern", rng),
        motif_rng or derived.get("arpeggio-profile-motif", rng),
        change_rng or derived.get("arpeggio-profile-change", rng),
    )


def _profile_subdivision(profile: ArpeggioProfile, bpm: int | float) -> str:
    if (
        isinstance(bpm, bool)
        or not isinstance(bpm, (int, float))
        or not math.isfinite(float(bpm))
        or bpm <= 0
    ):
        raise ValueError("arpeggio bpm must be positive")
    return (
        profile.fast_subdivision
        if bpm > profile.fast_bpm_threshold
        else profile.normal_subdivision
    )


def _accent_adjustment(onset_sixteenths: int) -> int:
    position = onset_sixteenths % 16
    if position == 0:
        return 10
    if position in {4, 8, 12}:
        return 4
    if position in {2, 6, 10, 14}:
        return 0
    return -4


def _arpeggio_required_source_units(voice_count: int, step: int) -> int:
    minimum_groups = math.ceil(voice_count / 2)
    return (
        (minimum_groups - 1) * step
        + _ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS
    )


def _select_arpeggio_subdivision(
    profile: ArpeggioProfile,
    bpm: int | float,
    source_units: int,
    voice_count: int,
    rng: random.Random,
) -> tuple[str | None, bool]:
    """Choose a grid that leaves an eighth-note tail after the pass."""
    preferred = _profile_subdivision(profile, bpm)
    eighth_possible = source_units >= _arpeggio_required_source_units(
        voice_count,
        _ARPEGGIO_GRID_STEPS["eighth"],
    )
    sixteenth_possible = source_units >= _arpeggio_required_source_units(
        voice_count,
        _ARPEGGIO_GRID_STEPS["sixteenth"],
    )
    if preferred == "eighth" and eighth_possible:
        subdivision = "eighth"
    elif (
        eighth_possible
        and rng.random() < profile.eighth_probability
    ):
        subdivision = "eighth"
    elif sixteenth_possible:
        subdivision = "sixteenth"
    else:
        subdivision = None
    return subdivision, eighth_possible


def _group_arpeggio_positions(
    positions: Sequence[int],
    slot_capacity: int,
    pair_probability: float,
    rng: random.Random,
) -> list[list[int]]:
    """Group a one-pass pitch order into single or simultaneous attacks."""
    groups: list[list[int]] = []
    cursor = 0
    while cursor < len(positions):
        remaining = len(positions) - cursor
        must_pair = (
            cursor + 1 < len(positions)
            and len(groups) + 1 + math.ceil((remaining - 1) / 2)
            > slot_capacity
        )
        pair = (
            cursor + 1 < len(positions)
            and (
                must_pair
                or rng.random() < pair_probability
            )
        )
        width = 2 if pair else 1
        groups.append(list(positions[cursor:cursor + width]))
        cursor += width
    if len(groups) > slot_capacity:
        raise ValueError(
            "Arpeggio source event is too short for its voicing and grid"
        )
    return groups


def schedule_arpeggios(
    voiced: Sequence[VoicedChord | None],
    events: Sequence[ChordEvent | dict],
    bpm: int | float,
    profile: ArpeggioProfile,
    rng: random.Random,
    *,
    pattern_rng: random.Random | None = None,
    motif_rng: random.Random | None = None,
    change_rng: random.Random | None = None,
) -> tuple[list[ArpeggioAttack], list[dict]]:
    """Schedule instrument-aware arpeggio attacks without mutating voicings."""
    if len(voiced) != len(events):
        raise ValueError(
            "voiced and events must contain the same number of source events"
        )
    if not isinstance(profile, ArpeggioProfile):
        raise TypeError("profile must be an ArpeggioProfile")
    pattern_rng, motif_rng, change_rng = _arpeggio_rng_streams(
        rng, pattern_rng, motif_rng, change_rng
    )

    attacks: list[ArpeggioAttack] = []
    diagnostics: list[dict] = []
    absolute_position = 0
    phrase_family: str | None = None
    phrase_motif: str | None = None
    phrase_step = 0
    next_change_position: int | None = None

    for event_index, (chord, event) in enumerate(zip(voiced, events)):
        source_units = sixteenth_slot_count(event)
        event_start = absolute_position
        event_end = event_start + source_units
        absolute_position = event_end
        no_chord = _event_is_no_chord(event)
        if no_chord:
            if chord is not None:
                raise ValueError(
                    f"No-chord event #{event_index} has a selected voicing"
                )
            diagnostics.append({
                "event_index": event_index,
                "pattern_family": None,
                "motif_family": None,
                "start_phase": None,
                "render_mode": "rest",
                "pad_fallback": False,
                "subdivision": None,
                "onset_count": 0,
                "slot_count": 0,
                "attack_count": 0,
                "pair_count": 0,
                "eighth_eligible": False,
                "event_start_sixteenths": event_start,
                "event_end_sixteenths": event_end,
                "source_voicing_midis": [],
                "source_sounding_strings": [],
                "no_chord": True,
            })
            phrase_family = None
            phrase_motif = None
            phrase_step = 0
            next_change_position = None
            continue

        if chord is None:
            raise ValueError(
                f"Playable event #{event_index} has no selected voicing"
            )
        ordered_indices = _ordered_source_indices(chord, profile, event_index)
        source_midis = list(chord.midi)
        subdivision, eighth_possible = _select_arpeggio_subdivision(
            profile,
            bpm,
            source_units,
            len(ordered_indices),
            change_rng,
        )
        if subdivision is None:
            diagnostics.append({
                "event_index": event_index,
                "pattern_family": None,
                "motif_family": None,
                "start_phase": None,
                "render_mode": "pad",
                "pad_fallback": True,
                "subdivision": None,
                "onset_count": 0,
                "slot_count": 0,
                "event_start_sixteenths": event_start,
                "event_end_sixteenths": event_end,
                "source_voicing_midis": list(source_midis),
                "source_sounding_strings": list(
                    (chord.diagnostics or {}).get("sounding_strings", ())
                ),
                "source_index_cycle": [],
                "supercycle_length": None,
                "attack_count": 0,
                "pair_count": 0,
                "eighth_eligible": eighth_possible,
                "no_chord": False,
                "fallback_reason": "insufficient_eighth_note_sustain",
            })
            continue
        dct_position = None
        if chord.dct_pitch is not None:
            dct_position = next(
                (
                    position
                    for position, source_index in enumerate(ordered_indices)
                    if source_midis[source_index] == chord.dct_pitch
                ),
                None,
            )

        if phrase_family is None:
            phrase_family = _weighted_family(profile.pattern_weights, pattern_rng)
            phrase_motif = _select_motif(
                profile.motif_weights, len(ordered_indices), motif_rng
            )
            motif_cycle = _motif_cycle(
                phrase_family,
                phrase_motif,
                len(ordered_indices),
                dct_position,
            )
            phrase_step = pattern_rng.randrange(len(motif_cycle))
            interval_bars = change_rng.choice(profile.change_interval_bars)
            next_change_position = event_start + interval_bars * 16
        elif (
            next_change_position is not None
            and event_start >= next_change_position
        ):
            phrase_family = _weighted_family_other(
                profile.pattern_weights,
                phrase_family,
                change_rng,
            )
            phrase_motif = _select_motif(
                profile.motif_weights, len(ordered_indices), change_rng
            )
            interval_bars = change_rng.choice(profile.change_interval_bars)
            next_change_position = event_start + interval_bars * 16

        assert phrase_family is not None
        assert phrase_motif is not None
        motif_cycle = _motif_cycle(
            phrase_family,
            phrase_motif,
            len(ordered_indices),
            dct_position,
        )
        event_start_phase = phrase_step % len(motif_cycle)
        positions = [
            motif_cycle[
                (event_start_phase + position) % len(motif_cycle)
            ]
            for position in range(len(motif_cycle))
        ]
        step = _ARPEGGIO_GRID_STEPS[subdivision]
        slot_capacity = max(
            0,
            (
                source_units - _ARPEGGIO_MIN_SUSTAIN_SIXTEENTHS
            ) // step + 1,
        )
        onset_groups = _group_arpeggio_positions(
            positions,
            slot_capacity,
            profile.pair_probability,
            motif_rng,
        )
        event_attacks_start = len(attacks)
        for slot, group in enumerate(onset_groups):
            onset = event_start + slot * step
            duration_sixteenths = float(event_end - onset)
            if duration_sixteenths <= 0:
                raise ValueError(
                    f"Arpeggio event #{event_index} produced a non-positive duration"
                )
            velocity = max(
                1,
                min(127, profile.base_velocity + _accent_adjustment(onset)),
            )
            for source_position in group:
                source_index = ordered_indices[source_position]
                attacks.append(ArpeggioAttack(
                    event_index=event_index,
                    midi=source_midis[source_index],
                    onset_sixteenths=onset,
                    duration_sixteenths=duration_sixteenths,
                    velocity=velocity,
                    source_index=source_index,
                ))
            phrase_step += 1
        onset_count = len(onset_groups)
        diagnostics.append({
            "event_index": event_index,
            "pattern_family": phrase_family,
            "motif_family": phrase_motif,
            "start_phase": event_start_phase,
            "render_mode": "arpeggio",
            "pad_fallback": False,
            "subdivision": subdivision,
            "onset_count": onset_count,
            "slot_count": onset_count,
            "event_start_sixteenths": event_start,
            "event_end_sixteenths": event_end,
            "source_voicing_midis": list(source_midis),
            "source_sounding_strings": list(
                (chord.diagnostics or {}).get("sounding_strings", ())
            ),
            "source_index_cycle": [
                ordered_indices[position] for position in motif_cycle
            ],
            "supercycle_length": len(motif_cycle),
            "attack_count": len(attacks) - event_attacks_start,
            "pair_count": sum(
                1 for group in onset_groups if len(group) == 2
            ),
            "eighth_eligible": eighth_possible,
            "no_chord": False,
        })

    return attacks, diagnostics


def _score_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def serialize_scheduled_arpeggios(
    attacks: Sequence[ArpeggioAttack],
    events: Sequence[ChordEvent | dict],
    diagnostics: Sequence[dict],
) -> list[str]:
    """Serialize scheduled attacks as absolute-time JFugue tokens."""
    if len(events) != len(diagnostics):
        raise ValueError("events and diagnostics must have the same length")
    by_event: dict[int, list[ArpeggioAttack]] = {}
    for attack in attacks:
        by_event.setdefault(attack.event_index, []).append(attack)
    tokens: list[str] = []
    for event_index, (event, diagnostic) in enumerate(zip(events, diagnostics)):
        event_start = int(diagnostic["event_start_sixteenths"])
        if _event_is_no_chord(event):
            tokens.extend((
                f"@{_score_number(event_start / 16)}",
                f"R{_event_duration_token(event)}",
            ))
            continue
        if diagnostic.get("pad_fallback") is True:
            source_midis = diagnostic.get("source_voicing_midis")
            if (
                not isinstance(source_midis, list)
                or not all(
                    isinstance(midi, int) and not isinstance(midi, bool)
                    for midi in source_midis
                )
                or not source_midis
            ):
                raise ValueError(
                    f"Pad fallback for event #{event_index} has no valid "
                    "source voicing"
                )
            tokens.extend((
                f"@{_score_number(event_start / 16)}",
                chord_token(source_midis, _event_duration_token(event)),
            ))
            continue
        event_attacks = by_event.get(event_index, ())
        attack_cursor = 0
        while attack_cursor < len(event_attacks):
            onset = event_attacks[attack_cursor].onset_sixteenths
            note_tokens = []
            while (
                attack_cursor < len(event_attacks)
                and event_attacks[attack_cursor].onset_sixteenths == onset
            ):
                attack = event_attacks[attack_cursor]
                note_tokens.append(
                    (
                        f"{midi_to_jfugue(attack.midi)}/"
                        f"{_score_number(attack.duration_sixteenths / 16)}"
                        f"A{attack.velocity}"
                    )
                )
                attack_cursor += 1
            tokens.extend((
                f"@{_score_number(onset / 16)}",
                "+".join(note_tokens),
            ))
    # Marker metadata gives the Java renderer the source-event boundaries.
    # It also extends a short final gate to the complete source timeline
    # without adding a musical rest or changing any attack.
    for event_index, diagnostic in enumerate(diagnostics):
        event_end = diagnostic.get("event_end_sixteenths")
        if event_end is None:
            continue
        tokens.extend((
            f"@{_score_number(float(event_end) / 16)}",
            f"#ARPEVENT{event_index}",
        ))
    return tokens


def serialize_arpeggios(
    voiced: Sequence[VoicedChord | None],
    events: Sequence[ChordEvent | dict],
    rng: random.Random,
    *,
    bpm: int | float = 120,
    profile: ArpeggioProfile | str | None = None,
) -> tuple[list[str], list[dict]]:
    """Serialize legacy sixteenths or a profile-driven timed performance."""
    if profile is not None:
        if isinstance(profile, str):
            try:
                profile = ARPEGGIO_PROFILES[profile]
            except KeyError:
                raise ValueError(
                    f"Unknown arpeggio performance profile {profile!r}"
                ) from None
        attacks, diagnostics = schedule_arpeggios(
            voiced, events, bpm, profile, rng
        )
        return (
            serialize_scheduled_arpeggios(attacks, events, diagnostics),
            diagnostics,
        )

    # Keep the spec 12 API stable for callers that do not select a profile.
    if len(voiced) != len(events):
        raise ValueError(
            "voiced and events must contain the same number of source events"
        )

    tokens: list[str] = []
    diagnostics: list[dict] = []
    phrase_family: str | None = None
    for event_index, (chord, event) in enumerate(zip(voiced, events)):
        duration = _event_duration_token(event)
        source_slots = sixteenth_slot_count(event)
        if _event_is_no_chord(event):
            tokens.append(f"R{duration}")
            diagnostics.append({
                "event_index": event_index,
                "pattern_family": None,
                "start_phase": None,
                "slot_count": 0,
                "source_voicing_midis": [],
                "no_chord": True,
            })
            phrase_family = None
            continue

        if chord is None:
            raise ValueError(
                f"Playable event #{event_index} has no selected voicing"
            )
        source_midis = sorted(chord.midi)
        if not source_midis:
            raise ValueError(
                f"Playable event #{event_index} has no selected MIDI pitches"
            )
        if phrase_family is None:
            phrase_family = rng.choice(_ARPEGGIO_PATTERN_FAMILIES)
        cycle = _arpeggio_cycle(phrase_family, len(source_midis))
        start_phase = rng.randrange(len(cycle))
        for slot in range(source_slots):
            pitch = source_midis[cycle[(start_phase + slot) % len(cycle)]]
            tokens.append(f"{midi_to_jfugue(pitch)}s")
        diagnostics.append({
            "event_index": event_index,
            "pattern_family": phrase_family,
            "start_phase": start_phase,
            "slot_count": source_slots,
            "source_voicing_midis": list(source_midis),
            "no_chord": False,
        })
    return tokens, diagnostics


def _child_rng(module_seed: int, label: str) -> random.Random:
    digest = hashlib.sha256(
        f"probabilistic-composition-generator:{module_seed}:{label}".encode(
            "utf-8"
        )
    ).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def validate_chord_instrument_catalog(
    mode: str,
    families: Sequence[str] | None = None,
) -> None:
    """Reject a mode/family combination without a selectable instrument."""
    if mode not in {"pads", "arpeggios"}:
        raise ValueError("mode must be 'pads' or 'arpeggios'")
    if mode == "arpeggios":
        validate_arpeggio_instrument_catalog()
    selected_families = tuple(
        CHORD_INSTRUMENTS if families is None else families
    )
    missing = [
        family for family in selected_families
        if not any(
            mode in instrument.roles
            for instrument in CHORD_INSTRUMENTS.get(family, ())
        )
    ]
    if missing:
        raise ValueError(
            f"No chord instrument is configured for mode {mode!r} "
            f"in family(s): {', '.join(missing)}"
        )


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
    last_arpeggio_diagnostics: list[dict] = field(
        default_factory=list, init=False
    )
    last_arpeggio_attacks: list[ArpeggioAttack] = field(
        default_factory=list, init=False
    )
    last_arpeggio_profile: str | None = field(default=None, init=False)

    def render(
        self,
        progression: dict | Song,
        preferred_family: str | None = None,
        bass_module_active: bool = False,
        voicer_order: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        if self.mode not in {"pads", "arpeggios"}:
            raise ValueError("mode must be 'pads' or 'arpeggios'")
        validate_chord_instrument_catalog(self.mode)

        song = progression if isinstance(progression, Song) else Song.from_dict(progression)
        module_seed = (
            self.seed
            if self.seed is not None
            else random.Random().getrandbits(128)
        )
        voicing_rng = random.Random(module_seed)
        instrument_rng = _child_rng(module_seed, f"instrument:{self.mode}")
        families = list(CHORD_INSTRUMENTS)
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
                voicing_rng.shuffle(families)
                families.insert(0, preferred_family)
            else:
                # Non-target songs may not carry a voicer hint; malformed hints
                # should have the same safe behavior as an absent one.
                primary = voicing_rng.choices(
                    families,
                    weights=[CHORD_FAMILY_WEIGHTS[family] for family in families],
                    k=1,
                )[0]
                families.remove(primary)
                voicing_rng.shuffle(families)
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
                    "seed": voicing_rng.randrange(2**32),
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
                if not eligible:
                    raise ValueError(
                        f"No chord instrument is configured for mode "
                        f"{self.mode!r} in family {family!r}"
                    )
                selected_instrument = instrument_rng.choice(eligible)
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
        self.last_voiced_midis = [list(chord.midi) if chord is not None else [] for chord in voiced]
        self.last_voiced_roles = [list(chord.roles) if chord is not None else [] for chord in voiced]
        self.last_voicing_diagnostics = [
            dict(chord.diagnostics) if chord is not None else {"no_chord": True}
            for chord in voiced
        ]
        events = progression.chords if isinstance(progression, Song) else progression["chords"]
        if self.mode == "arpeggios":
            profile_id = selected_instrument.arpeggio_profile
            if profile_id is None or profile_id not in ARPEGGIO_PROFILES:
                raise ValueError(
                    f"Arpeggio instrument {selected_instrument.name!r} "
                    "does not have a valid performance profile"
                )
            profile = ARPEGGIO_PROFILES[profile_id]
            pattern_rng = _child_rng(
                module_seed, "arpeggio-profile-pattern"
            )
            motif_rng = _child_rng(module_seed, "arpeggio-profile-motif")
            change_rng = _child_rng(module_seed, "arpeggio-profile-change")
            (
                self.last_arpeggio_attacks,
                self.last_arpeggio_diagnostics,
            ) = schedule_arpeggios(
                voiced,
                events,
                song.bpm,
                profile,
                pattern_rng,
                pattern_rng=pattern_rng,
                motif_rng=motif_rng,
                change_rng=change_rng,
            )
            tokens = serialize_scheduled_arpeggios(
                self.last_arpeggio_attacks,
                events,
                self.last_arpeggio_diagnostics,
            )
            self.last_arpeggio_profile = profile_id
        else:
            self.last_arpeggio_diagnostics = []
            self.last_arpeggio_attacks = []
            self.last_arpeggio_profile = None
            tokens = [
                chord_token(
                    chord.midi if chord is not None else [],
                    event.get("duration_token", "w")
                    if isinstance(event, dict) else event.duration_token,
                )
                for chord, event in zip(voiced, events)
            ]
        return f"T{song.bpm} V0 I{selected_instrument.program} " + " ".join(tokens)
