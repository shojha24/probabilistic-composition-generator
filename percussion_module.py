"""Render an optional, synchronized General MIDI percussion track."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import ClassVar

from chord_gen import DURATION_BEATS
from voicing.types import Song


PERCUSSION_OMISSION_PROBABILITY = 0.30
CUT_TIME_PROBABILITY = 0.20
HALF_TIME_PROBABILITY = 0.20


@dataclass
class PercussionModule:
    """Build a seeded kick, snare, and cymbal groove on MIDI voice 9.

    The source generator's duration tokens are all multiples of an eighth
    note, so the predecessor's eight-character bar vocabulary can be
    repeated without changing the song timeline.  Voice 9 is emitted even
    when percussion is omitted; in that case it contains synchronized rests.
    The ``cut_time`` feel is rendered as a double-time subdivision.
    """

    seed: int | None = None
    omission_probability: float = PERCUSSION_OMISSION_PROBABILITY
    last_included: bool = field(default=False, init=False)
    last_feel: str = field(default="normal", init=False)
    last_kit: dict[str, str] = field(default_factory=dict, init=False)

    _KICK_SOUNDS: ClassVar[dict[str, float]] = {
        "BASS_DRUM": 0.80,
        "ACOUSTIC_BASS_DRUM": 0.20,
    }
    _SNARE_SOUNDS: ClassVar[dict[str, float]] = {
        "ACOUSTIC_SNARE": 0.50,
        "ELECTRIC_SNARE": 0.40,
        "SIDE_STICK": 0.10,
    }
    _CYMBAL_SOUNDS: ClassVar[dict[str, float]] = {
        "CLOSED_HI_HAT": 0.50,
        "PEDAL_HI_HAT": 0.20,
        "RIDE_CYMBAL_1": 0.15,
        "RIDE_CYMBAL_2": 0.15,
    }
    _CRASH_SOUNDS: ClassVar[dict[str, float]] = {
        "CRASH_CYMBAL_1": 0.50,
        "CRASH_CYMBAL_2": 0.25,
        "CHINESE_CYMBAL": 0.25,
    }
    _SNARE_GROOVES: ClassVar[dict[str, float]] = {
        "..S...S.": 0.70,
        "....S...": 0.15,
        "......S.": 0.05,
        ".s....S.": 0.05,
        "..SS..S.": 0.05,
    }
    _CYMBAL_GROOVES: ClassVar[dict[str, float]] = {
        "^^^^^^^^": 0.70,
        "^.^.^.^.": 0.25,
        ".^.^.^.^": 0.05,
    }
    _HALF_TIME_CYMBAL_GROOVES: ClassVar[tuple[str, ...]] = (
        "^...^...",
        ".^...^..",
        "........",
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.omission_probability, bool)
            or not isinstance(self.omission_probability, (int, float))
            or not 0.0 <= self.omission_probability <= 1.0
        ):
            raise ValueError("omission_probability must be between 0 and 1")

    @staticmethod
    def _event_duration_token(event: object) -> str:
        if isinstance(event, dict):
            return event.get("duration_token", "w")
        return getattr(event, "duration_token", "w")

    @classmethod
    def _slot_count(cls, event: object) -> int:
        duration = cls._event_duration_token(event)
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
                "sixteenth-note percussion grid"
            )
        return int(slots)

    @staticmethod
    def _weighted_choice(
        choices: dict[str, float], rng: random.Random
    ) -> str:
        return rng.choices(
            tuple(choices), weights=tuple(choices.values()), k=1
        )[0]

    @staticmethod
    def _sound_for_symbol(
        symbol: str,
        kit: dict[str, str],
    ) -> tuple[str, int] | None:
        sounds = {
            "O": (kit["kick"], 0),
            "S": (kit["snare"], 0),
            "s": (kit["snare"], 1),
            "^": (kit["cymbal"], 0),
            "C": (kit["crash"], 0),
        }
        sound = sounds.get(symbol)
        if sound is not None:
            return sound
        if symbol == ".":
            return None
        raise ValueError(f"Unknown percussion symbol {symbol!r}")

    @classmethod
    def _render_slot(
        cls,
        kick: str,
        snare: str,
        cymbal: str,
        kit: dict[str, str],
        cymbal_on_both_halves: bool = False,
    ) -> tuple[str, str]:
        """Render one eighth-note slot as two sixteenth-note tokens."""
        halves: list[list[str]] = [[], []]
        for symbol in (kick, snare, cymbal):
            hit = cls._sound_for_symbol(symbol, kit)
            if hit is not None:
                sound, half = hit
                halves[half].append(f"[{sound}]s")
                if cymbal_on_both_halves and symbol == "^":
                    halves[1].append(f"[{sound}]s")

        return tuple(
            "+".join(notes) if notes else "Rs"
            for notes in halves
        )

    @staticmethod
    def _progression_events(progression: dict | Song) -> tuple:
        if isinstance(progression, Song):
            return progression.chords
        if not isinstance(progression, dict):
            raise TypeError("progression must be a dict or Song")
        events = progression.get("chords")
        if not isinstance(events, (list, tuple)):
            raise ValueError("progression must contain a chords list")
        return tuple(events)

    @staticmethod
    def _progression_bpm(progression: dict | Song) -> int | float:
        bpm = (
            progression.bpm
            if isinstance(progression, Song)
            else progression.get("bpm", 120)
        )
        if (
            isinstance(bpm, bool)
            or not isinstance(bpm, (int, float))
            or bpm <= 0
        ):
            raise ValueError("progression bpm must be positive")
        return bpm

    @staticmethod
    def _select_feel(bpm: int | float, rng: random.Random) -> str:
        if 60 <= bpm <= 120:
            return (
                "cut_time"
                if rng.random() < CUT_TIME_PROBABILITY
                else "normal"
            )
        if 121 <= bpm <= 180:
            return (
                "half_time"
                if rng.random() < HALF_TIME_PROBABILITY
                else "normal"
            )
        return "normal"

    def render(self, progression: dict | Song) -> str:
        """Return a synchronized ``V9`` track for ``progression``."""
        events = self._progression_events(progression)
        bpm = self._progression_bpm(progression)
        total_slots = sum(self._slot_count(event) for event in events)
        rng = random.Random(self.seed)
        self.last_included = rng.random() >= self.omission_probability
        self.last_feel = "normal"
        self.last_kit = {}

        if not self.last_included:
            return "V9" + (" " + " ".join("Rs" for _ in range(total_slots))
                           if total_slots else "")

        self.last_feel = self._select_feel(bpm, rng)
        self.last_kit = {
            "kick": self._weighted_choice(self._KICK_SOUNDS, rng),
            "snare": self._weighted_choice(self._SNARE_SOUNDS, rng),
            "cymbal": self._weighted_choice(self._CYMBAL_SOUNDS, rng),
            "crash": self._weighted_choice(self._CRASH_SOUNDS, rng),
        }
        if self.last_feel == "cut_time":
            kick_bar = "O...O..."
            snare_bar = "..S...S."
            cymbal_bar = self._weighted_choice(self._CYMBAL_GROOVES, rng)
        elif self.last_feel == "half_time":
            kick_bar = "O" + "".join(
                "O" if rng.random() < probability else "."
                for probability in (0.10, 0.05, 0.15, 0.30, 0.10, 0.05, 0.15)
            )
            snare_bar = "....S..."
            cymbal_bar = rng.choice(self._HALF_TIME_CYMBAL_GROOVES)
        else:
            kick_bar = "O" + "".join(
                "O" if rng.random() < probability else "."
                for probability in (0.30, 0.10, 0.40, 0.50, 0.30, 0.10, 0.40)
            )
            snare_bar = self._weighted_choice(self._SNARE_GROOVES, rng)
            cymbal_bar = self._weighted_choice(self._CYMBAL_GROOVES, rng)
        if rng.random() < 0.60:
            cymbal_bar = "C" + cymbal_bar[1:]

        eighth_slots = total_slots // 2
        tokens: list[str] = []
        for slot in range(eighth_slots):
            bar_slot = slot % 8
            tokens.extend(self._render_slot(
                kick_bar[bar_slot],
                snare_bar[bar_slot],
                cymbal_bar[bar_slot],
                self.last_kit,
                cymbal_on_both_halves=self.last_feel == "cut_time",
            ))
        return "V9" + (" " + " ".join(tokens) if tokens else "")
