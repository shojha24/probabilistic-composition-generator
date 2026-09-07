"""Chord-conditioned symbolic melody generation.

The melody layer is deliberately downstream of the chord and voicing layers.
It consumes the immutable :class:`ChordEvent` sequence, uses ``resolve_degrees``
for its harmonic vocabulary, and produces a monophonic V2 score plus an
auditable event map.  The implementation contains both the transparent
chord-centered random walk and a bounded phrase beam decoder.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import math
import random
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from chord_gen import DURATION_BEATS
from instruments import (
    MELODY_INSTRUMENTS,
    MELODY_PROFILE_PREFERRED_INSTRUMENTS,
)
from voicing.types import ChordEvent, Song, resolve_degrees


MELODY_ROLES = frozenset({
    "chord_tone",
    "extension",
    "scale_tone",
    "approach",
    "passing",
    "neighbor",
    "enclosure",
    "suspension",
    "appoggiatura",
    "held",
    "rest",
    "fallback",
})
MELODY_PITCH_SOURCES = frozenset({
    "source_degree",
    "realized_voicing",
    "global_scale",
    "chromatic_neighbor",
    "previous_melody",
    "rest",
})
METRIC_STRENGTHS = frozenset({"strong", "medium", "weak", "offbeat"})
DEGREE_ROLES = frozenset({"root", "3rd", "5th", "7th", "9th", "11th", "13th"})
SEED_DERIVATION_VERSION = "sha256-domain-v1"


SCALE_MODES: Mapping[str, tuple[int, ...]] = MappingProxyType({
    "major": (0, 2, 4, 5, 7, 9, 11),
    "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "natural_minor": (0, 2, 3, 5, 7, 8, 10),
    "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
})


@dataclass(frozen=True)
class MelodyEvent:
    """One immutable symbolic melody event on the sixteenth-note grid."""

    source_event_indices: tuple[int, ...]
    onset_sixteenths: int
    duration_sixteenths: float
    midi: int | None
    velocity: int | None
    role: str
    degree_role: str | None
    pitch_source: str
    metric_strength: str
    arrival_interval: int | None = None
    departure_interval: int | None = None
    resolution_target: int | None = None
    merged_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        indices = tuple(self.source_event_indices)
        if not indices or any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in indices
        ):
            raise ValueError("source_event_indices must contain non-negative integers")
        object.__setattr__(self, "source_event_indices", indices)
        if (
            isinstance(self.onset_sixteenths, bool)
            or not isinstance(self.onset_sixteenths, int)
            or self.onset_sixteenths < 0
        ):
            raise ValueError("onset_sixteenths must be a non-negative integer")
        if (
            isinstance(self.duration_sixteenths, bool)
            or not isinstance(self.duration_sixteenths, (int, float))
            or not math.isfinite(float(self.duration_sixteenths))
            or self.duration_sixteenths <= 0
        ):
            raise ValueError("duration_sixteenths must be finite and positive")
        if self.role not in MELODY_ROLES:
            raise ValueError(f"unsupported melody role {self.role!r}")
        if self.pitch_source not in MELODY_PITCH_SOURCES:
            raise ValueError(
                f"unsupported melody pitch source {self.pitch_source!r}"
            )
        if self.metric_strength not in METRIC_STRENGTHS:
            raise ValueError(
                f"unsupported metric strength {self.metric_strength!r}"
            )
        if self.midi is None:
            if self.role != "rest" and self.pitch_source != "rest":
                raise ValueError("only rest events may omit MIDI")
            if self.velocity is not None:
                raise ValueError("rest events must omit velocity")
        else:
            if (
                isinstance(self.midi, bool)
                or not isinstance(self.midi, int)
                or not 0 <= self.midi <= 127
            ):
                raise ValueError("melody MIDI must be an integer from 0 to 127")
            if (
                isinstance(self.velocity, bool)
                or not isinstance(self.velocity, int)
                or not 1 <= self.velocity <= 127
            ):
                raise ValueError(
                    "pitched melody velocity must be an integer from 1 to 127"
                )
        for name in (
            "arrival_interval",
            "departure_interval",
            "resolution_target",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ValueError(f"{name} must be an integer or None")
        merged_from = tuple(self.merged_from)
        if any(
            not isinstance(role, str) or role not in DEGREE_ROLES
            for role in merged_from
        ):
            raise ValueError("merged_from must contain valid degree roles")
        object.__setattr__(self, "merged_from", merged_from)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_event_indices": list(self.source_event_indices),
            "onset_sixteenths": self.onset_sixteenths,
            "duration_sixteenths": float(self.duration_sixteenths),
            "midi": self.midi,
            "velocity": self.velocity,
            "role": self.role,
            "degree_role": self.degree_role,
            "pitch_source": self.pitch_source,
            "metric_strength": self.metric_strength,
            "arrival_interval": self.arrival_interval,
            "departure_interval": self.departure_interval,
            "resolution_target": self.resolution_target,
            "merged_from": list(self.merged_from),
        }


@dataclass(frozen=True)
class MelodyProfile:
    """Immutable melodic performance and behavior distribution."""

    name: str
    instrument_program: int
    hard_min: int
    hard_max: int
    tessitura_min: int
    tessitura_max: int
    preferred_center: int
    base_velocity: int
    max_leap_semitones: int
    density_weights: Mapping[str, float]
    rhythm_weights: Mapping[str, float]
    behavior_weights: Mapping[str, float]
    rest_probability: float = 0.18
    hold_probability: float = 0.20

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("melody profile name must be non-empty")
        if (
            isinstance(self.instrument_program, bool)
            or not isinstance(self.instrument_program, int)
            or not 0 <= self.instrument_program <= 127
        ):
            raise ValueError("instrument_program must be in 0..127")
        for name, value in (
            ("hard_min", self.hard_min),
            ("hard_max", self.hard_max),
            ("tessitura_min", self.tessitura_min),
            ("tessitura_max", self.tessitura_max),
            ("preferred_center", self.preferred_center),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 127:
                raise ValueError(f"{name} must be an integer from 0 to 127")
        if self.hard_min > self.hard_max:
            raise ValueError("hard_min must not exceed hard_max")
        if not self.hard_min <= self.tessitura_min <= self.tessitura_max <= self.hard_max:
            raise ValueError("tessitura must be inside the hard range")
        if not self.hard_min <= self.preferred_center <= self.hard_max:
            raise ValueError("preferred_center must be inside the hard range")
        if (
            isinstance(self.base_velocity, bool)
            or not isinstance(self.base_velocity, int)
            or not 1 <= self.base_velocity <= 127
        ):
            raise ValueError("base_velocity must be in 1..127")
        if (
            isinstance(self.max_leap_semitones, bool)
            or not isinstance(self.max_leap_semitones, int)
            or self.max_leap_semitones < 0
        ):
            raise ValueError("max_leap_semitones must be a non-negative integer")
        for field_name in (
            "density_weights",
            "rhythm_weights",
            "behavior_weights",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{field_name} must be a non-empty mapping")
            normalized = {}
            for key, weight in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(f"{field_name} keys must be non-empty strings")
                if (
                    isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(float(weight))
                    or weight < 0
                ):
                    raise ValueError(
                        f"{field_name}[{key!r}] must be finite and non-negative"
                    )
                normalized[key] = float(weight)
            if not any(weight > 0 for weight in normalized.values()):
                raise ValueError(f"{field_name} must contain a positive weight")
            object.__setattr__(
                self, field_name, MappingProxyType(normalized)
            )
        for field_name, value in (
            ("rest_probability", self.rest_probability),
            ("hold_probability", self.hold_probability),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{field_name} must be a finite value from 0 to 1")


@dataclass(frozen=True)
class MelodyGenerationConfig:
    """Bounded decoder and tonal-context settings."""

    time_grid_sixteenths: int = 1
    subphrase_bars: int = 2
    phrase_context_bars: int = 8
    cadence_context_bars: int = 2
    nct_resolution_horizon: int = 2
    beam_width: int = 32
    candidate_limit_per_onset: int = 24
    decoder: str = "chord_centered_random_walk"
    local_key_policy: str = "confidence_aware"
    local_key_window_bars: int = 8
    local_key_persistence_cost: float = 1.0
    local_key_confidence_floor: float = 0.5
    label_safety_policy: str = "naturalistic"
    masking_cost_enabled: bool = False
    melody_collapse_probability: float = 0.25

    def __post_init__(self) -> None:
        for name in (
            "time_grid_sixteenths",
            "subphrase_bars",
            "phrase_context_bars",
            "cadence_context_bars",
            "nct_resolution_horizon",
            "beam_width",
            "candidate_limit_per_onset",
            "local_key_window_bars",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")
        if self.decoder not in {
            "chord_centered_random_walk",
            "sequence_beam",
        }:
            raise ValueError(
                "decoder must be 'chord_centered_random_walk' or 'sequence_beam'"
            )
        if self.local_key_policy not in {"none", "confidence_aware"}:
            raise ValueError(
                "local_key_policy must be 'none' or 'confidence_aware'"
            )
        if self.label_safety_policy != "naturalistic":
            raise ValueError("label_safety_policy must be 'naturalistic'")
        if not isinstance(self.masking_cost_enabled, bool):
            raise ValueError("masking_cost_enabled must be a boolean")
        for name, value in (
            ("local_key_persistence_cost", self.local_key_persistence_cost),
            ("local_key_confidence_floor", self.local_key_confidence_floor),
            ("melody_collapse_probability", self.melody_collapse_probability),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be finite")
        if self.local_key_persistence_cost < 0:
            raise ValueError("local_key_persistence_cost must be non-negative")
        if not 0 <= self.local_key_confidence_floor <= 1:
            raise ValueError("local_key_confidence_floor must be from 0 to 1")
        if not 0 <= self.melody_collapse_probability <= 1:
            raise ValueError("melody_collapse_probability must be from 0 to 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_grid_sixteenths": self.time_grid_sixteenths,
            "subphrase_bars": self.subphrase_bars,
            "phrase_context_bars": self.phrase_context_bars,
            "cadence_context_bars": self.cadence_context_bars,
            "nct_resolution_horizon": self.nct_resolution_horizon,
            "beam_width": self.beam_width,
            "candidate_limit_per_onset": self.candidate_limit_per_onset,
            "decoder": self.decoder,
            "local_key_policy": self.local_key_policy,
            "local_key_window_bars": self.local_key_window_bars,
            "local_key_persistence_cost": self.local_key_persistence_cost,
            "local_key_confidence_floor": self.local_key_confidence_floor,
            "label_safety_policy": self.label_safety_policy,
            "masking_cost_enabled": self.masking_cost_enabled,
            "melody_collapse_probability": self.melody_collapse_probability,
        }


@dataclass(frozen=True)
class LocalKeyContext:
    """A phrase-level tonal context used only as a soft melody prior."""

    start_sixteenths: int
    end_sixteenths: int
    tonic_pc: int
    scale_pcs: tuple[int, ...]
    confidence: float
    transition_cost: float
    source: str
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.start_sixteenths < 0 or self.end_sixteenths <= self.start_sixteenths:
            raise ValueError("local-key context must have a positive interval")
        if not 0 <= self.tonic_pc < 12:
            raise ValueError("local-key tonic_pc must be in 0..11")
        scale = tuple(int(pc) % 12 for pc in self.scale_pcs)
        if len(set(scale)) != len(scale):
            raise ValueError("local-key scale_pcs must not contain duplicates")
        object.__setattr__(self, "scale_pcs", scale)
        if not 0 <= self.confidence <= 1:
            raise ValueError("local-key confidence must be from 0 to 1")
        if self.transition_cost < 0:
            raise ValueError("local-key transition_cost must be non-negative")
        if self.source not in {"explicit", "inferred", "global_fallback"}:
            raise ValueError(f"unsupported local-key source {self.source!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_sixteenths": self.start_sixteenths,
            "end_sixteenths": self.end_sixteenths,
            "tonic_pc": self.tonic_pc,
            "scale_pcs": list(self.scale_pcs),
            "confidence": self.confidence,
            "transition_cost": self.transition_cost,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class MelodyCandidate:
    """Pitch/role candidate retained before sequence realization."""

    midi: int | None
    role: str
    degree_role: str | None
    pitch_source: str
    metric_strength: str
    duration_sixteenths: float
    resolution_target: int | None = None
    weight: float = 1.0
    source_event_indices: tuple[int, ...] = ()
    arrival_interval: int | None = None
    departure_interval: int | None = None
    merged_from: tuple[str, ...] = ()


@dataclass(frozen=True)
class MelodyGenerationResult:
    """Generated events and decoder diagnostics."""

    events: tuple[MelodyEvent, ...]
    metadata: dict[str, Any]

    def to_manifest(self, **overrides: Any) -> dict[str, Any]:
        result = dict(self.metadata)
        result.update(overrides)
        result["events"] = [event.to_dict() for event in self.events]
        return result


def _weighted_choice(
    weights: Mapping[str, float],
    rng: random.Random,
    default: str,
) -> str:
    choices = [(key, float(value)) for key, value in weights.items() if value > 0]
    if not choices:
        return default
    return rng.choices(
        [key for key, _ in choices],
        weights=[weight for _, weight in choices],
        k=1,
    )[0]


def _profile(
    name: str,
    program: int,
    hard_range: tuple[int, int],
    tessitura: tuple[int, int],
    center: int,
    velocity: int,
    leap: int,
    density: Mapping[str, float],
    rhythm: Mapping[str, float],
    behavior: Mapping[str, float],
    rest: float,
    hold: float,
) -> MelodyProfile:
    return MelodyProfile(
        name=name,
        instrument_program=program,
        hard_min=hard_range[0],
        hard_max=hard_range[1],
        tessitura_min=tessitura[0],
        tessitura_max=tessitura[1],
        preferred_center=center,
        base_velocity=velocity,
        max_leap_semitones=leap,
        density_weights=density,
        rhythm_weights=rhythm,
        behavior_weights=behavior,
        rest_probability=rest,
        hold_probability=hold,
    )


_DEFAULT_BEHAVIORS = {
    "chord_tone": 5.0,
    "extension": 2.4,
    "scale_tone": 1.2,
    "passing": 0.18,
    "neighbor": 0.18,
    "approach": 0.22,
    "enclosure": 0.08,
    "suspension": 0.12,
    "appoggiatura": 0.08,
}

MELODY_PROFILES = MappingProxyType({
    "lead-high-sparse": _profile(
        "lead-high-sparse",
        40,
        (55, 105),
        (67, 96),
        82,
        88,
        9,
        {"sparse": 0.70, "moderate": 0.25, "active": 0.05},
        {"sixteenth": 0.05, "eighth": 0.35, "quarter": 0.45, "half": 0.15},
        _DEFAULT_BEHAVIORS,
        0.32,
        0.35,
    ),
    "lead-mid-neutral": _profile(
        "lead-mid-neutral",
        56,
        (48, 96),
        (60, 88),
        74,
        86,
        10,
        {"sparse": 0.25, "moderate": 0.60, "active": 0.15},
        {"sixteenth": 0.12, "eighth": 0.50, "quarter": 0.30, "half": 0.08},
        _DEFAULT_BEHAVIORS,
        0.18,
        0.22,
    ),
    "lead-mid-active": _profile(
        "lead-mid-active",
        29,
        (45, 96),
        (55, 84),
        70,
        90,
        12,
        {"sparse": 0.10, "moderate": 0.45, "active": 0.45},
        {"sixteenth": 0.40, "eighth": 0.45, "quarter": 0.13, "half": 0.02},
        _DEFAULT_BEHAVIORS,
        0.10,
        0.12,
    ),
})


def get_melody_profile(profile: str | MelodyProfile) -> MelodyProfile:
    if isinstance(profile, MelodyProfile):
        return profile
    if not isinstance(profile, str):
        raise TypeError("profile must be a profile name or MelodyProfile")
    try:
        return MELODY_PROFILES[profile]
    except KeyError:
        raise ValueError(
            f"unknown melody profile {profile!r}; "
            f"expected one of {tuple(MELODY_PROFILES)}"
        ) from None


def validate_melody_instrument_catalog() -> None:
    programs = set()
    for instrument in MELODY_INSTRUMENTS:
        if instrument.program in programs:
            raise ValueError(
                f"duplicate melody instrument program {instrument.program}"
            )
        programs.add(instrument.program)
        if not 0 <= instrument.program <= 127:
            raise ValueError(
                f"melody instrument {instrument.name!r} has invalid program"
            )
    for profile, names in MELODY_PROFILE_PREFERRED_INSTRUMENTS.items():
        if profile not in MELODY_PROFILES:
            raise ValueError(f"preferred melody profile {profile!r} is unknown")
        if not names:
            raise ValueError(f"melody profile {profile!r} has no instruments")
        available_names = {
            item.name for item in MELODY_INSTRUMENTS
        }
        if any(
            name not in available_names
            for name in names
        ):
            raise ValueError(f"melody profile {profile!r} references an unknown instrument")


def derive_child_seed(seed: int, domain: str) -> int:
    """Derive a stable child seed without using Python's randomized hash()."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not isinstance(domain, str) or not domain:
        raise ValueError("seed domain must be a non-empty string")
    digest = hashlib.sha256(
        f"{SEED_DERIVATION_VERSION}:{seed!r}:{domain}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:16], "big")


stable_child_seed = derive_child_seed


def child_rng(seed: int, domain: str) -> random.Random:
    return random.Random(derive_child_seed(seed, domain))


def _percentage(value: float | int, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 100
    ):
        raise ValueError(f"{name} must be a finite percentage between 0 and 100")
    return float(value)


def melody_quota_count(song_count: int, melody_percent: float | int) -> int:
    if isinstance(song_count, bool) or not isinstance(song_count, int) or song_count < 0:
        raise ValueError("song_count must be a non-negative integer")
    percent = _percentage(melody_percent, "melody_percent")
    return min(song_count, max(0, math.floor(song_count * percent / 100 + 0.5)))


def melody_inclusion_plan(
    song_count: int,
    melody_percent: float | int,
    seed: int,
    source_ids: Sequence[int] | None = None,
) -> list[bool]:
    """Return an exact deterministic nearest-whole-song inclusion plan."""
    target = melody_quota_count(song_count, melody_percent)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if source_ids is not None and len(source_ids) != song_count:
        raise ValueError("source_ids must match song_count")
    order = list(range(song_count))
    if source_ids is not None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in source_ids
        ):
            raise ValueError("source_ids must contain integers")
        order.sort(key=lambda index: (source_ids[index], index))
    selected = set(
        child_rng(seed, "melody-inclusion").sample(order, target)
    )
    return [index in selected for index in range(song_count)]


compute_melody_inclusion_plan = melody_inclusion_plan


def _song(progression: dict | Song) -> Song:
    if isinstance(progression, Song):
        return progression
    if not isinstance(progression, dict):
        raise TypeError("progression must be a dict or Song")
    return Song.from_dict(progression)


def resolve_scale_pcs(
    tonic_pc: int,
    mode: str | None = None,
    scale_pcs: Sequence[int] | None = None,
) -> tuple[int, ...]:
    """Resolve a tonic-relative explicit scale into absolute pitch classes."""
    if isinstance(tonic_pc, bool) or not isinstance(tonic_pc, int) or not 0 <= tonic_pc < 12:
        raise ValueError("tonic_pc must be an integer from 0 to 11")
    if scale_pcs is not None:
        if not isinstance(scale_pcs, Sequence) or isinstance(scale_pcs, (str, bytes)):
            raise ValueError("scale_pcs must be a sequence of pitch classes")
        values = tuple(scale_pcs)
        if not values:
            raise ValueError("scale_pcs must not be empty")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 12
            for value in values
        ):
            raise ValueError("scale_pcs must contain integers from 0 to 11")
        if len(set(values)) != len(values):
            raise ValueError("scale_pcs must not contain duplicate pitch classes")
        return tuple((tonic_pc + value) % 12 for value in values)
    if mode is None:
        return ()
    if not isinstance(mode, str) or mode.lower() not in SCALE_MODES:
        raise ValueError(
            f"unknown mode {mode!r}; expected one of {tuple(SCALE_MODES)}"
        )
    return tuple((tonic_pc + value) % 12 for value in SCALE_MODES[mode.lower()])


resolve_tonal_scale = resolve_scale_pcs


def _timeline(song: Song) -> tuple[list[int], list[int]]:
    starts: list[int] = []
    ends: list[int] = []
    cursor = 0
    for event in song.chords:
        try:
            units = DURATION_BEATS[event.duration_token] * 4
        except KeyError:
            raise ValueError(
                f"unknown duration token {event.duration_token!r}"
            ) from None
        if not float(units).is_integer() or units <= 0:
            raise ValueError(
                f"duration token {event.duration_token!r} is not a positive "
                "sixteenth-grid duration"
            )
        starts.append(cursor)
        cursor += int(units)
        ends.append(cursor)
    return starts, ends


def _metric_strength(onset: int) -> str:
    position = onset % 16
    if position in (0, 8):
        return "strong"
    if position in (4, 12):
        return "medium"
    if position % 2:
        return "offbeat"
    return "weak"


def _active_pitch_classes(chord: ChordEvent, tonic_pc: int) -> set[int]:
    root_pc = (tonic_pc + chord.root_interval) % 12
    return {(root_pc + degree.semitone) % 12 for degree in resolve_degrees(chord)}


def _degree_candidates(
    chord: ChordEvent,
    tonic_pc: int,
    metric: str,
    profile: MelodyProfile,
) -> list[tuple[int, str, str, str, float, tuple[str, ...]]]:
    root_pc = (tonic_pc + chord.root_interval) % 12
    metric_factor = {"strong": 1.35, "medium": 1.05, "weak": 0.85, "offbeat": 0.65}[metric]
    result = []
    for degree in resolve_degrees(chord):
        role = (
            "extension"
            if degree.role in {"7th", "9th", "11th", "13th"}
            else "chord_tone"
        )
        weight = float(profile.behavior_weights.get(role, 0.0)) * metric_factor
        if metric == "strong" and role == "chord_tone":
            weight *= 1.35
        if metric == "offbeat" and role == "extension":
            weight *= 1.12
        if weight <= 0:
            continue
        result.append((
            (root_pc + degree.semitone) % 12,
            role,
            degree.role,
            "source_degree",
            weight,
            tuple(degree.merged_from),
        ))
    return result


def _nearest_legal_pitches(
    pitch_class: int,
    profile: MelodyProfile,
    previous_midi: int | None,
) -> list[int]:
    legal = [
        midi
        for midi in range(profile.hard_min, profile.hard_max + 1)
        if midi % 12 == pitch_class % 12
    ]
    if not legal:
        return []
    anchor = previous_midi if previous_midi is not None else profile.preferred_center
    legal.sort(key=lambda midi: (abs(midi - anchor), abs(midi - profile.preferred_center), midi))
    # Retain a small octave neighborhood, not every MIDI pitch in the range.
    return legal[:3]


def _next_target_pc(song: Song, event_index: int) -> int | None:
    for next_index in range(event_index + 1, len(song.chords)):
        event = song.chords[next_index]
        if event.is_no_chord:
            return None
        degrees = resolve_degrees(event)
        if not degrees:
            continue
        root_pc = (song.tonic_pc + event.root_interval) % 12
        return root_pc
    return None


def enumerate_melody_candidates(
    progression: dict | Song,
    event_index: int,
    previous_midi: int | None = None,
    profile: str | MelodyProfile = "lead-high-sparse",
    config: MelodyGenerationConfig | None = None,
    *,
    local_key: LocalKeyContext | None = None,
    next_target_midi: int | None = None,
    duration_sixteenths: float | None = None,
    realized_voicing: Any | None = None,
) -> tuple[MelodyCandidate, ...]:
    """Build a bounded candidate pool for one source event."""
    song = _song(progression)
    profile_obj = get_melody_profile(profile)
    config = config or MelodyGenerationConfig()
    if (
        isinstance(event_index, bool)
        or not isinstance(event_index, int)
        or not 0 <= event_index < len(song.chords)
    ):
        raise IndexError("event_index is outside the progression")
    event = song.chords[event_index]
    starts, ends = _timeline(song)
    duration = float(
        duration_sixteenths
        if duration_sixteenths is not None
        else ends[event_index] - starts[event_index]
    )
    metric = _metric_strength(starts[event_index])
    if event.is_no_chord:
        return (
            MelodyCandidate(
                None,
                "rest",
                None,
                "rest",
                metric,
                duration,
                source_event_indices=(event_index,),
            ),
        )

    if local_key is None:
        scale_pcs = resolve_scale_pcs(
            song.tonic_pc,
            getattr(song, "mode", None),
            getattr(song, "scale_pcs", None),
        )
    else:
        scale_pcs = local_key.scale_pcs
    target_pc = (
        next_target_midi % 12
        if next_target_midi is not None
        else _next_target_pc(song, event_index)
    )
    raw: list[tuple[int | None, str, str | None, str, float, int | None, tuple[str, ...]]] = []
    for pitch_class, role, degree_role, source, weight, merged in _degree_candidates(
        event, song.tonic_pc, metric, profile_obj
    ):
        for midi in _nearest_legal_pitches(pitch_class, profile_obj, previous_midi):
            arrival = None if previous_midi is None else midi - previous_midi
            raw.append((midi, role, degree_role, source, weight, None, merged))

    if realized_voicing is not None:
        if isinstance(realized_voicing, Mapping):
            realized_midis = realized_voicing.get("midi")
            if realized_midis is None:
                realized_midis = realized_voicing.get("midis")
            realized_roles = realized_voicing.get("roles") or ()
        else:
            realized_midis = getattr(realized_voicing, "midi", None)
            realized_roles = getattr(realized_voicing, "roles", ()) or ()
        realized_midis = tuple(realized_midis or ())
        realized_roles = tuple(realized_roles)
        degree_by_role = {
            degree.role: degree
            for degree in resolve_degrees(event)
        }
        metric_factor = {
            "strong": 1.35,
            "medium": 1.05,
            "weak": 0.85,
            "offbeat": 0.65,
        }[metric]
        for index, midi in enumerate(realized_midis):
            if (
                isinstance(midi, bool)
                or not isinstance(midi, int)
                or not profile_obj.hard_min <= midi <= profile_obj.hard_max
            ):
                continue
            degree_role = (
                realized_roles[index]
                if index < len(realized_roles)
                and realized_roles[index] in DEGREE_ROLES
                else None
            )
            role = (
                "extension"
                if degree_role in {"7th", "9th", "11th", "13th"}
                else "chord_tone"
            )
            degree = degree_by_role.get(degree_role)
            weight = (
                float(profile_obj.behavior_weights.get(role, 0.0))
                * metric_factor
            )
            if weight > 0:
                raw.append((
                    midi,
                    role,
                    degree_role,
                    "realized_voicing",
                    weight,
                    None,
                    () if degree is None else tuple(degree.merged_from),
                ))

    active_pcs = _active_pitch_classes(event, song.tonic_pc)
    if scale_pcs:
        for pitch_class in scale_pcs:
            if pitch_class in active_pcs:
                continue
            weight = float(profile_obj.behavior_weights.get("scale_tone", 0.0))
            if metric in {"strong", "medium"}:
                weight *= 0.72
            if weight <= 0:
                continue
            for midi in _nearest_legal_pitches(pitch_class, profile_obj, previous_midi):
                raw.append((midi, "scale_tone", None, "global_scale", weight, None, ()))

    if previous_midi is not None and profile_obj.hold_probability > 0:
        if profile_obj.hard_min <= previous_midi <= profile_obj.hard_max:
            raw.append((
                previous_midi,
                "held",
                None,
                "previous_melody",
                float(profile_obj.behavior_weights.get("suspension", 0.1)),
                None,
                (),
            ))

    if target_pc is not None:
        nct_specs = (
            ("approach", -1),
            ("approach", 1),
            ("passing", -2),
            ("passing", 2),
            ("neighbor", -1),
            ("neighbor", 1),
            ("enclosure", -2),
            ("enclosure", 2),
            ("appoggiatura", -1),
            ("appoggiatura", 1),
        )
        seen_nct = set()
        for role, offset in nct_specs:
            pitch_class = (target_pc + offset) % 12
            key = (role, pitch_class)
            if key in seen_nct or pitch_class in active_pcs:
                continue
            seen_nct.add(key)
            weight = float(profile_obj.behavior_weights.get(role, 0.0))
            if metric == "strong" and role in {"approach", "neighbor"}:
                weight *= 0.55
            if metric == "offbeat":
                weight *= 1.15
            if weight <= 0:
                continue
            for midi in _nearest_legal_pitches(pitch_class, profile_obj, previous_midi):
                raw.append((
                    midi,
                    role,
                    None,
                    "chromatic_neighbor",
                    weight,
                    target_pc if midi is not None else None,
                    (),
                ))

    if previous_midi is not None:
        raw.append((
            previous_midi,
            "held",
            None,
            "previous_melody",
            max(0.01, profile_obj.hold_probability),
            None,
            (),
        ))
    raw.append((
        None,
        "rest",
        None,
        "rest",
        max(0.01, profile_obj.rest_probability),
        None,
        (),
    ))

    candidates: dict[tuple[int | None, str, str | None], MelodyCandidate] = {}
    for midi, role, degree_role, source, weight, resolution, merged in raw:
        if midi is not None and previous_midi is not None:
            if abs(midi - previous_midi) > profile_obj.max_leap_semitones:
                continue
        candidate = MelodyCandidate(
            midi=midi,
            role=role,
            degree_role=degree_role,
            pitch_source=source,
            metric_strength=metric,
            duration_sixteenths=duration,
            resolution_target=(
                None
                if resolution is None
                else next(
                    (
                        target
                        for target in _nearest_legal_pitches(
                            resolution, profile_obj, midi
                        )
                        if target is not None
                    ),
                    None,
                )
            ),
            weight=max(0.0001, float(weight)),
            source_event_indices=(event_index,),
            arrival_interval=None if previous_midi is None or midi is None else midi - previous_midi,
            merged_from=merged,
        )
        key = (
            candidate.midi,
            candidate.role,
            candidate.degree_role,
            candidate.pitch_source,
        )
        existing = candidates.get(key)
        if existing is None or candidate.weight > existing.weight:
            candidates[key] = candidate
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            item.midi is None,
            -item.weight,
            abs(
                (item.midi if item.midi is not None else profile_obj.preferred_center)
                - profile_obj.preferred_center
            ),
            item.midi if item.midi is not None else 128,
            item.role,
        ),
    )
    return tuple(ordered[: config.candidate_limit_per_onset])


def infer_local_key_context(
    progression: dict | Song,
    start_sixteenths: int,
    end_sixteenths: int,
    config: MelodyGenerationConfig | None = None,
    previous: LocalKeyContext | None = None,
) -> LocalKeyContext:
    """Infer a soft phrase context with explicit-scale precedence and fallback."""
    song = _song(progression)
    config = config or MelodyGenerationConfig()
    if end_sixteenths <= start_sixteenths:
        raise ValueError("local-key window must have a positive duration")
    explicit_scale = resolve_scale_pcs(
        song.tonic_pc,
        getattr(song, "mode", None),
        getattr(song, "scale_pcs", None),
    )
    if explicit_scale:
        transition = (
            config.local_key_persistence_cost
            if previous is not None and previous.tonic_pc != song.tonic_pc
            else 0.0
        )
        return LocalKeyContext(
            start_sixteenths,
            end_sixteenths,
            song.tonic_pc,
            explicit_scale,
            1.0,
            transition,
            "explicit",
        )
    if config.local_key_policy == "none":
        return LocalKeyContext(
            start_sixteenths,
            end_sixteenths,
            song.tonic_pc,
            (),
            0.0,
            0.0,
            "global_fallback",
            "local_key_policy_none",
        )

    starts, ends = _timeline(song)
    weighted_pcs: Counter[int] = Counter()
    total_weight = 0.0
    for index, event in enumerate(song.chords):
        if event.is_no_chord or ends[index] <= start_sixteenths or starts[index] >= end_sixteenths:
            continue
        weight = float(min(ends[index], end_sixteenths) - max(starts[index], start_sixteenths))
        total_weight += weight
        for pc in _active_pitch_classes(event, song.tonic_pc):
            weighted_pcs[pc] += weight
    if total_weight <= 0:
        return LocalKeyContext(
            start_sixteenths,
            end_sixteenths,
            song.tonic_pc,
            (),
            0.0,
            0.0,
            "global_fallback",
            "no_playable_events",
        )

    candidates = []
    for tonic in range(12):
        for mode_name in ("major", "minor", "dorian", "mixolydian"):
            scale = tuple((tonic + value) % 12 for value in SCALE_MODES[mode_name])
            in_scale = sum(weighted_pcs[pc] for pc in scale)
            root_support = sum(
                weight
                for index, event in enumerate(song.chords)
                if not event.is_no_chord
                and start_sixteenths <= ends[index]
                and end_sixteenths >= starts[index]
                and (song.tonic_pc + event.root_interval) % 12 == tonic
                for weight in [float(min(ends[index], end_sixteenths) - max(starts[index], start_sixteenths))]
            )
            candidates.append((in_scale + 0.35 * root_support, tonic, scale, mode_name))
    candidates.sort(key=lambda item: (-item[0], item[1], item[3]))
    best_score, best_tonic, best_scale, _best_mode = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    confidence = max(0.0, min(1.0, best_score / (total_weight * 7.0)))
    margin = (
        (best_score - second_score) / best_score
        if best_score > 0 else 0.0
    )
    if confidence < config.local_key_confidence_floor or margin < 0.08:
        return LocalKeyContext(
            start_sixteenths,
            end_sixteenths,
            song.tonic_pc,
            (),
            confidence,
            0.0,
            "global_fallback",
            "low_confidence_or_unstable_window",
        )
    transition = 0.0
    if previous is not None and previous.tonic_pc != best_tonic:
        transition = config.local_key_persistence_cost
        if margin < min(0.5, config.local_key_persistence_cost / 4.0):
            return LocalKeyContext(
                start_sixteenths,
                end_sixteenths,
                previous.tonic_pc,
                previous.scale_pcs,
                confidence,
                transition,
                "global_fallback",
                "persistence_cost_exceeded",
            )
    return LocalKeyContext(
        start_sixteenths,
        end_sixteenths,
        best_tonic,
        best_scale,
        confidence,
        transition,
        "inferred",
    )


infer_local_key = infer_local_key_context


@dataclass(frozen=True)
class _RhythmSlot:
    onset: int
    duration: int
    source_event_indices: tuple[int, ...]
    event_index: int
    metric_strength: str
    is_forced_rest: bool = False


def _rhythm_units(profile: MelodyProfile, rng: random.Random) -> int:
    family = _weighted_choice(profile.rhythm_weights, rng, "eighth")
    return {
        "sixteenth": 1,
        "eighth": 2,
        "quarter": 4,
        "quarter_or_longer": 4,
        "half": 8,
        "long": 8,
    }.get(family, 2)


def _density_factor(profile: MelodyProfile, rng: random.Random) -> float:
    density = _weighted_choice(profile.density_weights, rng, "moderate")
    return {"sparse": 0.65, "moderate": 1.0, "active": 1.35}.get(density, 1.0)


def _build_rhythm_plan(
    song: Song,
    profile: MelodyProfile,
    seed: int,
) -> list[_RhythmSlot]:
    starts, ends = _timeline(song)
    total = ends[-1] if ends else 0
    rng = child_rng(seed, "melody-rhythm")
    slots: list[_RhythmSlot] = []
    position = 0
    while position < total:
        event_index = next(
            index
            for index, (start, end) in enumerate(zip(starts, ends))
            if start <= position < end
        )
        event = song.chords[event_index]
        remaining = ends[event_index] - position
        if event.is_no_chord:
            slots.append(_RhythmSlot(
                position,
                remaining,
                (event_index,),
                event_index,
                _metric_strength(position),
                True,
            ))
            position = ends[event_index]
            continue
        duration = min(remaining, max(1, _rhythm_units(profile, rng)))
        factor = _density_factor(profile, rng)
        rest = rng.random() < min(0.9, profile.rest_probability / factor)
        covered = [event_index]
        if (
            not rest
            and rng.random() < profile.hold_probability
            and position + duration >= ends[event_index]
        ):
            next_index = event_index + 1
            while (
                next_index < len(song.chords)
                and not song.chords[next_index].is_no_chord
                and len(covered) < 3
            ):
                duration += ends[next_index] - starts[next_index]
                covered.append(next_index)
                next_index += 1
        slots.append(_RhythmSlot(
            position,
            duration,
            tuple(covered),
            event_index,
            _metric_strength(position),
            rest,
        ))
        position += duration
    return slots


def _candidate_score(
    candidate: MelodyCandidate,
    previous_midi: int | None,
    profile: MelodyProfile,
    local_key: LocalKeyContext | None,
    is_last: bool,
) -> float:
    if candidate.midi is None:
        score = math.log(max(candidate.weight, 0.0001)) - (
            1.5 if candidate.metric_strength == "strong" else 0.0
        )
        return score
    score = math.log(max(candidate.weight, 0.0001))
    if previous_midi is not None:
        leap = abs(candidate.midi - previous_midi)
        score -= 0.16 * leap
        if leap > profile.max_leap_semitones:
            score -= 100.0
        if leap == 0:
            score -= 0.15
    if profile.tessitura_min <= candidate.midi <= profile.tessitura_max:
        score += 0.75
    else:
        score -= 0.18 * min(
            abs(candidate.midi - profile.tessitura_min),
            abs(candidate.midi - profile.tessitura_max),
        )
    if local_key is not None and local_key.scale_pcs:
        if candidate.midi % 12 in local_key.scale_pcs:
            score += 0.24
        elif candidate.role == "scale_tone":
            score -= 0.25
    if candidate.metric_strength == "strong" and candidate.role in {
        "chord_tone",
        "extension",
        "held",
    }:
        score += 0.7
    if candidate.role in {
        "approach",
        "passing",
        "neighbor",
        "enclosure",
        "appoggiatura",
    }:
        score -= 0.15 if candidate.metric_strength != "strong" else 0.65
    if is_last and candidate.role in {"chord_tone", "extension"}:
        score += 0.55
    return score


def _legal_candidates(
    candidates: Sequence[MelodyCandidate],
    previous_midi: int | None,
    profile: MelodyProfile,
    pending_target: int | None = None,
) -> list[MelodyCandidate]:
    legal = []
    for candidate in candidates:
        if candidate.midi is None:
            if pending_target is None:
                legal.append(candidate)
            continue
        if not profile.hard_min <= candidate.midi <= profile.hard_max:
            continue
        if previous_midi is not None and abs(candidate.midi - previous_midi) > profile.max_leap_semitones:
            continue
        if pending_target is not None and candidate.midi % 12 != pending_target % 12:
            continue
        legal.append(candidate)
    return legal


@dataclass
class _BeamState:
    candidates: list[MelodyCandidate]
    score: float
    pending_target: int | None = None


def _choose_random_walk(
    candidate_lists: Sequence[Sequence[MelodyCandidate]],
    slots: Sequence[_RhythmSlot],
    profile: MelodyProfile,
    contexts: Sequence[LocalKeyContext],
    seed: int,
) -> list[MelodyCandidate]:
    rng = child_rng(seed, "melody-candidate")
    chosen: list[MelodyCandidate] = []
    previous_midi = None
    pending_target = None
    for index, candidates in enumerate(candidate_lists):
        legal = _legal_candidates(candidates, previous_midi, profile, pending_target)
        if not legal:
            legal = [
                candidate for candidate in candidates
                if candidate.midi is None
            ]
        if not legal:
            legal = list(candidates)
        scored = [
            (
                candidate,
                math.exp(max(-30.0, min(30.0, _candidate_score(
                    candidate,
                    previous_midi,
                    profile,
                    contexts[index],
                    index == len(candidate_lists) - 1,
                )))) * max(candidate.weight, 0.0001),
            )
            for candidate in legal
        ]
        selected = rng.choices(
            [candidate for candidate, _ in scored],
            weights=[weight for _, weight in scored],
            k=1,
        )[0]
        chosen.append(selected)
        if selected.midi is not None:
            previous_midi = selected.midi
        else:
            previous_midi = None
            pending_target = None
        if selected.midi is not None:
            pending_target = (
                selected.resolution_target
                if selected.role in {
                    "approach",
                    "passing",
                    "neighbor",
                    "enclosure",
                    "suspension",
                    "appoggiatura",
                }
                else None
            )
    return chosen


def _choose_sequence_beam(
    candidate_lists: Sequence[Sequence[MelodyCandidate]],
    slots: Sequence[_RhythmSlot],
    profile: MelodyProfile,
    contexts: Sequence[LocalKeyContext],
    config: MelodyGenerationConfig,
    seed: int,
) -> list[MelodyCandidate]:
    tie_rng = child_rng(seed, "melody-tie-break")
    states = [_BeamState([], 0.0, None)]
    for index, candidates in enumerate(candidate_lists):
        expanded: list[tuple[float, float, _BeamState, MelodyCandidate]] = []
        for state in states:
            previous_midi = None
            for prior in reversed(state.candidates):
                if prior.midi is None:
                    break
                previous_midi = prior.midi
                break
            legal = _legal_candidates(
                candidates,
                previous_midi,
                profile,
                state.pending_target,
            )
            if not legal:
                legal = [candidate for candidate in candidates if candidate.midi is None]
            if not legal:
                legal = list(candidates)
            for candidate in legal:
                value = _candidate_score(
                    candidate,
                    previous_midi,
                    profile,
                    contexts[index],
                    index == len(candidate_lists) - 1,
                )
                if state.candidates and candidate.midi is not None and previous_midi == candidate.midi:
                    value -= 0.10
                pending_target = (
                    candidate.resolution_target
                    if candidate.role in {
                        "approach",
                        "passing",
                        "neighbor",
                        "enclosure",
                        "suspension",
                        "appoggiatura",
                    }
                    else None
                )
                next_state = _BeamState(
                    [*state.candidates, candidate],
                    state.score + value,
                    pending_target,
                )
                expanded.append((next_state.score, tie_rng.random(), next_state, candidate))
        expanded.sort(key=lambda item: (-item[0], item[1]))
        states = [item[2] for item in expanded[: config.beam_width]]
    if not states:
        return []
    return states[0].candidates


def _realized_evidence(
    song: Song,
    realized_voicings: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    evidence = []
    for index, event in enumerate(song.chords):
        source_roles = [] if event.is_no_chord else [
            degree.role for degree in resolve_degrees(event)
        ]
        realized = realized_voicings[index] if realized_voicings and index < len(realized_voicings) else None
        if realized is None:
            realized_roles = []
            realized_midis = []
        elif isinstance(realized, Mapping):
            realized_roles = list(realized.get("roles") or ())
            realized_midis = list(realized.get("midi") or realized.get("midis") or ())
        else:
            realized_roles = list(getattr(realized, "roles", ()) or ())
            realized_midis = list(getattr(realized, "midi", ()) or ())
        evidence.append({
            "source_event_index": index,
            "source_degree_evidence": source_roles,
            "realized_voicing_evidence": realized_roles,
            "realized_midis": realized_midis,
            "omitted_source_roles": sorted(set(source_roles) - set(realized_roles)),
        })
    return evidence


def _finalize_events(
    selected: Sequence[MelodyCandidate],
    slots: Sequence[_RhythmSlot],
    profile: MelodyProfile,
) -> tuple[MelodyEvent, ...]:
    events: list[MelodyEvent] = []
    previous_midi = None
    for candidate, slot in zip(selected, slots):
        midi = candidate.midi
        role = candidate.role
        source = candidate.pitch_source
        if slot.is_forced_rest:
            midi = None
            role = "rest"
            source = "rest"
        if midi is None:
            event = MelodyEvent(
                slot.source_event_indices,
                slot.onset,
                float(slot.duration),
                None,
                None,
                "rest",
                None,
                "rest",
                slot.metric_strength,
            )
        else:
            velocity = max(
                1,
                min(
                    127,
                    profile.base_velocity
                    + (4 if slot.metric_strength == "strong" else 0),
                ),
            )
            event = MelodyEvent(
                slot.source_event_indices,
                slot.onset,
                float(slot.duration),
                midi,
                velocity,
                role if role in MELODY_ROLES else "fallback",
                candidate.degree_role,
                source if source in MELODY_PITCH_SOURCES else "source_degree",
                slot.metric_strength,
                None if previous_midi is None else midi - previous_midi,
                None,
                candidate.resolution_target,
                candidate.merged_from,
            )
        events.append(event)
        if midi is not None:
            previous_midi = midi
        else:
            previous_midi = None
    pitched = [
        (index, event)
        for index, event in enumerate(events)
        if event.midi is not None
    ]
    for pitched_index, (index, event) in enumerate(pitched):
        next_event = pitched[pitched_index + 1][1] if pitched_index + 1 < len(pitched) else None
        departure = (
            None if next_event is None else next_event.midi - event.midi
        )
        events[index] = replace(event, departure_interval=departure)
    return tuple(events)


def rest_melody_events(progression: dict | Song) -> tuple[MelodyEvent, ...]:
    song = _song(progression)
    starts, ends = _timeline(song)
    return tuple(
        MelodyEvent(
            (index,),
            starts[index],
            float(ends[index] - starts[index]),
            None,
            None,
            "rest",
            None,
            "rest",
            _metric_strength(starts[index]),
        )
        for index in range(len(song.chords))
    )


def generate_melody(
    progression: dict | Song,
    seed: int = 0,
    profile: str | MelodyProfile = "lead-high-sparse",
    config: MelodyGenerationConfig | None = None,
    *,
    realized_voicings: Sequence[Any] | None = None,
) -> MelodyGenerationResult:
    """Generate a deterministic monophonic melody over ``progression``."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    song = _song(progression)
    profile_obj = get_melody_profile(profile)
    config = config or MelodyGenerationConfig()
    starts, ends = _timeline(song)
    if not song.chords:
        return MelodyGenerationResult(
            (),
            {
                "decoder": config.decoder,
                "generation_config": config.to_dict(),
                "seed": seed,
                "seed_derivation_version": SEED_DERIVATION_VERSION,
                "fallback_count": 0,
                "local_key_contexts": [],
                "realized_voicing_evidence": [],
            },
        )
    contexts = []
    window = config.local_key_window_bars * 16
    previous_context = None
    total = ends[-1]
    for start in range(0, total, window):
        context = infer_local_key_context(
            song,
            start,
            min(total, start + window),
            config,
            previous_context,
        )
        contexts.append(context)
        previous_context = context

    def context_for(onset: int) -> LocalKeyContext:
        for context in contexts:
            if context.start_sixteenths <= onset < context.end_sixteenths:
                return context
        return contexts[-1]

    slots = _build_rhythm_plan(song, profile_obj, seed)
    candidate_lists = []
    previous_midi = None
    for slot in slots:
        if slot.is_forced_rest:
            candidate_lists.append((
                MelodyCandidate(
                    None,
                    "rest",
                    None,
                    "rest",
                    slot.metric_strength,
                    float(slot.duration),
                    source_event_indices=slot.source_event_indices,
                ),
            ))
            previous_midi = None
            continue
        candidates = enumerate_melody_candidates(
            song,
            slot.event_index,
            previous_midi,
            profile_obj,
            config,
            local_key=context_for(slot.onset),
            duration_sixteenths=slot.duration,
            realized_voicing=(
                realized_voicings[slot.event_index]
                if realized_voicings is not None
                and slot.event_index < len(realized_voicings)
                else None
            ),
        )
        candidates = tuple(
            replace(
                candidate,
                duration_sixteenths=float(slot.duration),
                source_event_indices=slot.source_event_indices,
            )
            for candidate in candidates
        )
        if len(slot.source_event_indices) > 1:
            shared_pcs: set[int] | None = None
            for source_index in slot.source_event_indices:
                active_pcs = _active_pitch_classes(
                    song.chords[source_index],
                    song.tonic_pc,
                )
                shared_pcs = (
                    active_pcs
                    if shared_pcs is None
                    else shared_pcs & active_pcs
                )
            candidates = tuple(
                candidate
                for candidate in candidates
                if candidate.midi is None
                or candidate.midi % 12 in (shared_pcs or set())
            )
            if not candidates:
                candidates = (
                    MelodyCandidate(
                        None,
                        "rest",
                        None,
                        "rest",
                        slot.metric_strength,
                        float(slot.duration),
                        source_event_indices=slot.source_event_indices,
                    ),
                )
        candidate_lists.append(candidates)
        # Center the next candidate pool on a deterministic proposal without
        # consuming the decoder's eventual choice.
        previous_midi = next(
            (
                candidate.midi
                for candidate in candidates
                if candidate.midi is not None
            ),
            previous_midi,
        )

    if config.decoder == "sequence_beam":
        selected = _choose_sequence_beam(
            candidate_lists,
            slots,
            profile_obj,
            [context_for(slot.onset) for slot in slots],
            config,
            seed,
        )
    else:
        selected = _choose_random_walk(
            candidate_lists,
            slots,
            profile_obj,
            [context_for(slot.onset) for slot in slots],
            seed,
        )
    if len(selected) != len(slots):
        selected = [
            candidates[0]
            for candidates in candidate_lists
            if candidates
        ]
    events = _finalize_events(selected, slots, profile_obj)
    behavior_counts = Counter(
        event.role for event in events if event.midi is not None
    )
    rhythm_counts = Counter()
    for event in events:
        if event.duration_sixteenths <= 1:
            rhythm_counts["sixteenth"] += 1
        elif event.duration_sixteenths <= 2:
            rhythm_counts["eighth"] += 1
        else:
            rhythm_counts["quarter_or_longer"] += 1
    nonrests = [event.midi for event in events if event.midi is not None]
    chromatic_roles = {
        "approach",
        "passing",
        "neighbor",
        "enclosure",
        "suspension",
        "appoggiatura",
    }
    fallback_count = sum(
        1
        for event in events
        if event.role == "fallback"
    )
    metadata = {
        "decoder": config.decoder,
        "generation_config": config.to_dict(),
        "seed": seed,
        "seed_derivation_version": SEED_DERIVATION_VERSION,
        "note_count": len(nonrests),
        "rest_count": sum(event.midi is None for event in events),
        "held_note_count": sum(
            event.role == "held" or len(event.source_event_indices) > 1
            for event in events
            if event.midi is not None
        ),
        "chord_tone_count": sum(event.role == "chord_tone" for event in events),
        "extension_count": sum(event.role == "extension" for event in events),
        "scale_tone_count": sum(event.role == "scale_tone" for event in events),
        "chromatic_count": sum(event.role in chromatic_roles for event in events),
        "behavior_counts": dict(sorted(behavior_counts.items())),
        "rhythm_counts": dict(sorted(rhythm_counts.items())),
        "range": (
            [min(nonrests), max(nonrests)] if nonrests else [None, None]
        ),
        "hard_range": [profile_obj.hard_min, profile_obj.hard_max],
        "tessitura": [profile_obj.tessitura_min, profile_obj.tessitura_max],
        "preferred_center": profile_obj.preferred_center,
        "max_leap_semitones": profile_obj.max_leap_semitones,
        "requested_density": dict(profile_obj.density_weights),
        "realized_density": (
            len(nonrests) / len(events) if events else 0.0
        ),
        "fallback_count": fallback_count,
        "fallback_reasons": [],
        "local_key_contexts": [context.to_dict() for context in contexts],
        "realized_voicing_evidence": _realized_evidence(song, realized_voicings),
        "no_chord_policy": "rest_and_reset",
        "no_chord_decisions": [
            {
                "source_event_index": index,
                "action": "rest",
                "reset": True,
            }
            for index, event in enumerate(song.chords)
            if event.is_no_chord
        ],
        "label_safety_policy": config.label_safety_policy,
    }
    return MelodyGenerationResult(events, metadata)


generate = generate_melody


def serialize_melody_events(
    events: Sequence[MelodyEvent],
    bpm: int | float,
    instrument_program: int,
) -> str:
    """Serialize an event map as an absolute-time JFugue V2 track."""
    if (
        isinstance(instrument_program, bool)
        or not isinstance(instrument_program, int)
        or not 0 <= instrument_program <= 127
    ):
        raise ValueError("instrument_program must be an integer from 0 to 127")
    if (
        isinstance(bpm, bool)
        or not isinstance(bpm, (int, float))
        or not math.isfinite(float(bpm))
        or bpm <= 0
    ):
        raise ValueError("bpm must be positive")
    ordered = sorted(events, key=lambda event: event.onset_sixteenths)
    tokens = [f"T{bpm:g}", f"V2", f"I{instrument_program}"]
    previous_end = 0.0
    for event in ordered:
        onset = float(event.onset_sixteenths)
        if onset < previous_end - 1e-9:
            raise ValueError("melody events may not overlap")
        tokens.append(f"@{_score_number(onset / 16)}")
        duration = _score_number(float(event.duration_sixteenths) / 16)
        if event.midi is None:
            tokens.append(f"R/{duration}")
        else:
            tokens.append(
                f"{midi_to_jfugue(event.midi)}/{duration}A{event.velocity}"
            )
        previous_end = onset + float(event.duration_sixteenths)
    return " ".join(tokens)


def serialize_melody_track(
    progression: dict | Song,
    events: Sequence[MelodyEvent],
    instrument_program: int,
) -> str:
    song = _song(progression)
    return serialize_melody_events(events, song.bpm, instrument_program)


def midi_to_jfugue(midi: int) -> str:
    if isinstance(midi, bool) or not isinstance(midi, int) or not 0 <= midi <= 127:
        raise ValueError("MIDI must be an integer from 0 to 127")
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _score_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def select_melody_instrument(
    profile: str | MelodyProfile,
    seed: int,
    *,
    chord_instrument: Any | None = None,
    collapse_probability: float = 0.25,
) -> dict[str, Any]:
    """Select a melody timbre with an isolated collapse decision."""
    profile_obj = get_melody_profile(profile)
    if (
        isinstance(collapse_probability, bool)
        or not isinstance(collapse_probability, (int, float))
        or not math.isfinite(float(collapse_probability))
        or not 0 <= collapse_probability <= 1
    ):
        raise ValueError("collapse_probability must be a finite value from 0 to 1")
    validate_melody_instrument_catalog()
    preferred_names = MELODY_PROFILE_PREFERRED_INSTRUMENTS[profile_obj.name]
    preferred = [
        instrument for instrument in MELODY_INSTRUMENTS
        if instrument.name in preferred_names
    ]
    catalog_rng = child_rng(seed, "melody-instrument")
    selected = catalog_rng.choice(preferred or MELODY_INSTRUMENTS)
    collapse_rng = child_rng(seed, "melody-collapse")
    collapse_draw = collapse_rng.random()
    collapsed = (
        chord_instrument is not None
        and collapse_probability > 0
        and (
            collapse_probability >= 1.0
            or collapse_draw < collapse_probability
        )
    )
    if collapsed:
        instrument = chord_instrument
        source = "chord_collapse"
    else:
        instrument = selected
        source = "melody_catalog"
    return {
        "instrument": getattr(instrument, "name", None),
        "instrument_program": getattr(instrument, "program", None),
        "instrument_source": source,
        "collapsed_to_chord": collapsed,
        "collapse_draw": collapse_draw,
        "chord_instrument": getattr(chord_instrument, "name", None),
        "chord_instrument_program": getattr(chord_instrument, "program", None),
        "melody_catalog_instrument": selected.name,
        "melody_catalog_program": selected.program,
    }


def disabled_melody_record(
    condition: str = "none",
    profile: str = "lead-high-sparse",
    *,
    inclusion_percent: float | None = None,
    included: bool = False,
    selection_mode: str = "disabled",
    omission_reason: str | None = None,
    collapse_probability: float = 0.25,
) -> dict[str, Any] | None:
    if condition not in {"none", "naturalistic"}:
        raise ValueError("condition must be 'none' or 'naturalistic'")
    if condition == "none":
        return None
    return {
        "enabled": True,
        "included": bool(included),
        "condition": condition,
        "profile": profile,
        "inclusion_percent": inclusion_percent,
        "selection_mode": selection_mode,
        "omission_reason": omission_reason,
        "instrument": None,
        "instrument_program": None,
        "instrument_source": "omitted",
        "collapsed_to_chord": None,
        "chord_instrument": None,
        "chord_instrument_program": None,
        "melody_collapse_probability": collapse_probability,
        "track": "V2",
        "seed": None,
        "note_count": 0,
        "rest_count": 0,
        "held_note_count": 0,
        "chord_tone_count": 0,
        "extension_count": 0,
        "chromatic_count": 0,
        "behavior_counts": {},
        "rhythm_counts": {},
        "events": [],
    }


def build_melody_manifest(
    result: MelodyGenerationResult,
    profile: str | MelodyProfile,
    *,
    condition: str = "naturalistic",
    included: bool = True,
    inclusion_percent: float | None = None,
    selection_mode: str = "single_song_resolved",
    omission_reason: str | None = None,
    instrument_selection: Mapping[str, Any] | None = None,
    collapse_probability: float | None = None,
) -> dict[str, Any]:
    if condition not in {"none", "naturalistic"}:
        raise ValueError("condition must be 'none' or 'naturalistic'")
    if condition == "none":
        return None
    profile_obj = get_melody_profile(profile)
    record = result.to_manifest(
        enabled=True,
        included=bool(included),
        condition=condition,
        profile=profile_obj.name,
        inclusion_percent=inclusion_percent,
        selection_mode=selection_mode,
        omission_reason=omission_reason,
        track="V2",
    )
    if instrument_selection is None:
        instrument_selection = {
            "instrument": None,
            "instrument_program": None,
            "instrument_source": "omitted",
            "collapsed_to_chord": None,
            "chord_instrument": None,
            "chord_instrument_program": None,
        }
    record.update({
        "instrument": instrument_selection.get("instrument"),
        "instrument_program": instrument_selection.get("instrument_program"),
        "instrument_source": instrument_selection.get(
            "instrument_source", "omitted"
        ),
        "collapsed_to_chord": instrument_selection.get("collapsed_to_chord"),
        "chord_instrument": instrument_selection.get("chord_instrument"),
        "chord_instrument_program": instrument_selection.get(
            "chord_instrument_program"
        ),
        "melody_collapse_probability": (
            collapse_probability
            if collapse_probability is not None
            else result.metadata["generation_config"][
                "melody_collapse_probability"
            ]
        ),
    })
    if not included:
        record["instrument_source"] = "omitted"
        record["instrument"] = None
        record["instrument_program"] = None
        record["collapsed_to_chord"] = None
    if instrument_selection.get("collapse_draw") is not None:
        record["collapse_draw"] = instrument_selection["collapse_draw"]
    return record


validate_melody_instrument_catalog()
