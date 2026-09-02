"""Curated zero-based General MIDI programs for JFugue rendering."""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


_ARPEGGIO_PATTERN_FAMILIES = (
    "up",
    "down",
    "up_down",
    "outside_in",
)
_ARPEGGIO_MOTIF_FAMILIES = (
    "straight",
    "bass_return",
    "top_return",
    "light_alternate",
)
_ARPEGGIO_SUBDIVISIONS = frozenset(("sixteenth", "eighth"))


@dataclass(frozen=True)
class ArpeggioProfile:
    """Immutable performance settings for one arpeggio instrument family.

    ``gate_ratio`` remains part of the profile schema for compatibility;
    profile-aware arpeggios now sustain every attack to its source boundary.
    """

    family: str
    fast_bpm_threshold: int
    normal_subdivision: str
    fast_subdivision: str
    gate_ratio: float
    base_velocity: int
    pattern_weights: Mapping[str, float]
    motif_weights: Mapping[str, float]
    change_interval_bars: tuple[int, ...]
    eighth_probability: float = 0.35
    pair_probability: float = 0.25

    def __post_init__(self) -> None:
        if not isinstance(self.family, str) or not self.family.strip():
            raise ValueError("arpeggio profile family must be a non-empty string")
        if (
            isinstance(self.fast_bpm_threshold, bool)
            or not isinstance(self.fast_bpm_threshold, int)
            or self.fast_bpm_threshold <= 0
        ):
            raise ValueError("fast_bpm_threshold must be a positive integer")
        if (
            self.normal_subdivision not in _ARPEGGIO_SUBDIVISIONS
            or self.fast_subdivision not in _ARPEGGIO_SUBDIVISIONS
        ):
            raise ValueError(
                "normal_subdivision and fast_subdivision must be "
                "'sixteenth' or 'eighth'"
            )
        if (
            isinstance(self.gate_ratio, bool)
            or not isinstance(self.gate_ratio, (int, float))
            or not math.isfinite(float(self.gate_ratio))
            or self.gate_ratio <= 0
        ):
            raise ValueError("gate_ratio must be a finite positive number")
        if (
            isinstance(self.base_velocity, bool)
            or not isinstance(self.base_velocity, int)
            or not 1 <= self.base_velocity <= 127
        ):
            raise ValueError("base_velocity must be an integer from 1 to 127")
        for field_name, probability in (
            ("eighth_probability", self.eighth_probability),
            ("pair_probability", self.pair_probability),
        ):
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(float(probability))
                or not 0 <= probability <= 1
            ):
                raise ValueError(
                    f"{field_name} must be a finite number from 0 to 1"
                )

        pattern_weights = self._validated_weights(
            self.pattern_weights,
            _ARPEGGIO_PATTERN_FAMILIES,
            "pattern_weights",
        )
        motif_weights = self._validated_weights(
            self.motif_weights,
            _ARPEGGIO_MOTIF_FAMILIES,
            "motif_weights",
        )
        if not isinstance(self.change_interval_bars, (tuple, list)):
            raise ValueError("change_interval_bars must be a tuple of integers")
        intervals = tuple(self.change_interval_bars)
        if not intervals or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in intervals
        ):
            raise ValueError(
                "change_interval_bars must contain positive integers"
            )
        object.__setattr__(
            self,
            "pattern_weights",
            MappingProxyType(pattern_weights),
        )
        object.__setattr__(
            self,
            "motif_weights",
            MappingProxyType(motif_weights),
        )
        object.__setattr__(self, "change_interval_bars", intervals)

    @staticmethod
    def _validated_weights(
        weights: Mapping[str, float],
        allowed: tuple[str, ...],
        field_name: str,
    ) -> dict[str, float]:
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError(f"{field_name} must be a non-empty mapping")
        result = {}
        for name, value in weights.items():
            if name not in allowed:
                raise ValueError(f"{field_name} contains unknown family {name!r}")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(
                    f"{field_name}[{name!r}] must be finite and non-negative"
                )
            result[name] = float(value)
        if not any(value > 0 for value in result.values()):
            raise ValueError(f"{field_name} must contain a positive value")
        return result


def _profile(
    family: str,
    threshold: int,
    gate_ratio: float,
    base_velocity: int,
    pattern_weights: Mapping[str, float],
    motif_weights: Mapping[str, float],
) -> ArpeggioProfile:
    return ArpeggioProfile(
        family=family,
        fast_bpm_threshold=threshold,
        normal_subdivision="sixteenth",
        fast_subdivision="eighth",
        gate_ratio=gate_ratio,
        base_velocity=base_velocity,
        pattern_weights=pattern_weights,
        motif_weights=motif_weights,
        change_interval_bars=(2, 4),
    )


ARPEGGIO_PROFILES = MappingProxyType({
    "keyboard-ringing": _profile(
        "keyboard",
        120,
        1.50,
        84,
        {"up": 0.40, "down": 0.25, "up_down": 0.25, "outside_in": 0.10},
        {
            "straight": 0.60,
            "bass_return": 0.15,
            "top_return": 0.15,
            "light_alternate": 0.10,
        },
    ),
    "keyboard-dry": _profile(
        "keyboard",
        120,
        0.80,
        88,
        {"up": 0.35, "down": 0.30, "up_down": 0.25, "outside_in": 0.10},
        {
            "straight": 0.70,
            "bass_return": 0.10,
            "top_return": 0.10,
            "light_alternate": 0.10,
        },
    ),
    "guitar-ringing": _profile(
        "guitar",
        90,
        1.75,
        78,
        {"up": 0.45, "down": 0.30, "up_down": 0.25, "outside_in": 0.0},
        {
            "straight": 0.65,
            "bass_return": 0.15,
            "top_return": 0.10,
            "light_alternate": 0.10,
        },
    ),
    "guitar-muted": _profile(
        "guitar",
        110,
        0.65,
        92,
        {"up": 0.40, "down": 0.35, "up_down": 0.25, "outside_in": 0.0},
        {
            "straight": 0.75,
            "bass_return": 0.10,
            "top_return": 0.10,
            "light_alternate": 0.05,
        },
    ),
    "synth-pulse": _profile(
        "synth",
        150,
        0.85,
        96,
        {"up": 0.45, "down": 0.20, "up_down": 0.25, "outside_in": 0.10},
        {
            "straight": 0.75,
            "bass_return": 0.05,
            "top_return": 0.05,
            "light_alternate": 0.15,
        },
    ),
})


@dataclass(frozen=True)
class Instrument:
    name: str
    program: int
    roles: frozenset[str]
    arpeggio_profile: str | None = None


CHORD_INSTRUMENTS = {
    "piano": (
        Instrument(
            "acoustic-grand-piano", 0, frozenset(("pads", "arpeggios")),
            "keyboard-ringing",
        ),
        Instrument(
            "bright-acoustic-piano", 1, frozenset(("arpeggios",)),
            "keyboard-ringing",
        ),
        Instrument(
            "honky-tonk-piano", 3, frozenset(("arpeggios",)),
            "keyboard-ringing",
        ),
        Instrument("electric-piano-1", 4, frozenset(("pads",))),
        Instrument(
            "electric-piano-2", 5, frozenset(("arpeggios",)),
            "keyboard-ringing",
        ),
        Instrument(
            "harpsichord", 6, frozenset(("arpeggios",)),
            "keyboard-dry",
        ),
        Instrument(
            "clavinet", 7, frozenset(("arpeggios",)),
            "keyboard-dry",
        ),
        Instrument(
            "vibraphone", 11, frozenset(("pads", "arpeggios")),
            "keyboard-ringing",
        ),
        Instrument(
            "marimba", 12, frozenset(("arpeggios",)),
            "keyboard-dry",
        ),
        Instrument(
            "percussive-organ", 17, frozenset(("bass", "arpeggios")),
            "keyboard-ringing",
        ),
        Instrument("rock-organ", 18, frozenset(("pads", "bass"))),
        Instrument("church-organ", 19, frozenset(("pads",))),
        Instrument("shakuhachi", 77, frozenset(("pads",))),
        Instrument("bagpipe", 109, frozenset(("pads",))),
        Instrument(
            "fiddle", 110, frozenset(("pads", "arpeggios")),
            "keyboard-ringing",
        ),
        Instrument("shanai", 111, frozenset(("pads",))),
    ),
    "guitar": (
        Instrument(
            "acoustic-guitar-nylon", 24, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
        Instrument(
            "acoustic-guitar-steel", 25, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
        Instrument("electric-guitar-jazz", 26, frozenset(("pads",))),
        Instrument(
            "electric-guitar-clean", 27, frozenset(("pads", "arpeggios")),
            "guitar-ringing",
        ),
        Instrument(
            "electric-guitar-muted", 28, frozenset(("arpeggios",)),
            "guitar-muted",
        ),
        Instrument("overdriven-guitar", 29, frozenset(("pads",))),
        Instrument("distortion-guitar", 30, frozenset(("pads",))),
        Instrument(
            "guitar-harmonics", 31, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
        Instrument(
            "sitar", 104, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
        Instrument(
            "banjo", 105, frozenset(("arpeggios",)),
            "guitar-muted",
        ),
        Instrument(
            "shamisen", 106, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
        Instrument(
            "koto", 107, frozenset(("arpeggios",)),
            "guitar-ringing",
        ),
    ),
    "synth": (
        Instrument("new-age-pad", 88, frozenset(("pads",))),
        Instrument("warm-pad", 89, frozenset(("pads",))),
        Instrument("choir-pad", 91, frozenset(("pads",))),
        Instrument("bowed-pad", 92, frozenset(("pads",))),
        Instrument("metallic-pad", 93, frozenset(("pads",))),
        Instrument("halo-pad", 94, frozenset(("pads",))),
        Instrument("sweep-pad", 95, frozenset(("pads",))),
        Instrument("synth-strings-1", 50, frozenset(("pads",))),
        Instrument("synth-strings-2", 51, frozenset(("pads",))),
        Instrument("atmosphere", 99, frozenset(("pads",))),
        Instrument("soundtrack", 100, frozenset(("pads",))),
        Instrument(
            "synth-lead-square", 80, frozenset(("arpeggios",)),
            "synth-pulse",
        ),
    ),
}

BASS_INSTRUMENTS = (
    Instrument("acoustic-bass", 32, frozenset(("bass",))),
    Instrument("electric-bass-finger", 33, frozenset(("bass",))),
    Instrument("electric-bass-pick", 34, frozenset(("bass",))),
    Instrument("fretless-bass", 35, frozenset(("bass",))),
    Instrument("slap-bass-1", 36, frozenset(("bass",))),
    Instrument("slap-bass-2", 37, frozenset(("bass",))),
    Instrument("synth-bass-1", 38, frozenset(("bass",))),
    Instrument("synth-bass-2", 39, frozenset(("bass",))),
    Instrument("contrabass", 43, frozenset(("bass",))),
    Instrument("cello", 42, frozenset(("bass",))),
    Instrument(
        "square-wave", 80, frozenset(("bass", "arpeggios")),
        "synth-pulse",
    ),
    Instrument("sawtooth-wave", 81, frozenset(("bass",))),
    Instrument("basslead", 87, frozenset(("bass",))),
)


def validate_arpeggio_instrument_catalog() -> None:
    """Validate profile references on every arpeggio-capable instrument."""
    instruments = [
        instrument
        for family in CHORD_INSTRUMENTS.values()
        for instrument in family
    ]
    instruments.extend(BASS_INSTRUMENTS)
    invalid = []
    for instrument in instruments:
        if "arpeggios" not in instrument.roles:
            continue
        profile_id = instrument.arpeggio_profile
        if profile_id is None:
            invalid.append(f"{instrument.name}: missing profile")
        elif profile_id not in ARPEGGIO_PROFILES:
            invalid.append(
                f"{instrument.name}: unknown profile {profile_id!r}"
            )
    if invalid:
        raise ValueError(
            "Invalid arpeggio instrument catalog: " + "; ".join(invalid)
        )
