"""
voicing/types.py -- shared data model for the voicing engine.

Implements spec 07 §2.2-§2.5: the ChordEvent contract, the degree/semitone
tables, and the collision-merge rule. Also defines the output VoicedChord
record (spec 07 §3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# §2.3 -- Degree -> semitone table (normative)
# ---------------------------------------------------------------------------

# triad -> (third_semitone_or_None, fifth_semitone_or_None)
TRIAD_THIRD_FIFTH = {
    "major": (4, 7),
    "minor": (3, 7),
    "diminished": (3, 6),
    "augmented": (4, 8),
    "sus4": (5, 7),
    "sus2": (2, 7),
    "5": (None, 7),
    "1": (None, None),
}

# extension slot -> token -> semitones above root
EXT_SEMI = {
    "seventh": {"7": 11, "b7": 10, "bb7": 9},
    "ninth": {"9": 2, "b9": 1, "#9": 3},
    "eleventh": {"11": 5, "#11": 6},
    "thirteenth": {"13": 9, "b13": 8},
}

# Canonical degree ordering, used for "highest-numbered differentiator" (§5.1)
# and for signature/role bookkeeping.
DEGREE_ORDER = ("root", "3rd", "5th", "7th", "9th", "11th", "13th")
DEGREE_RANK = {d: i for i, d in enumerate(DEGREE_ORDER)}

EXT_SLOTS = ("seventh", "ninth", "eleventh", "thirteenth")
SLOT_TO_ROLE = {"seventh": "7th", "ninth": "9th", "eleventh": "11th", "thirteenth": "13th"}


@dataclass(frozen=True)
class Degree:
    """A single active chord degree: a role label, semitone offset above
    root, and the raw token that produced it (for diagnostics)."""
    role: str          # "root" | "3rd" | "5th" | "7th" | "9th" | "11th" | "13th"
    semitone: int      # 0-11, offset above root
    token: str         # e.g. "b7", "#11", "1" (triad root/third labels)
    merged_from: tuple = ()  # roles absorbed into this degree by §2.5 merge


@dataclass(frozen=True)
class ChordEvent:
    """Mirrors spec 07 §2.2 exactly. `root_interval`/`bass_interval` are
    normative; `root`/`bass`/`harte` are carried through for diagnostics only
    and must not be parsed."""
    root_interval: int
    triad: str
    bass_interval: int
    seventh: str = "N"
    ninth: str = "N"
    eleventh: str = "N"
    thirteenth: str = "N"
    root: str = ""
    bass: str = ""
    harte: str = ""

    @staticmethod
    def from_dict(d: dict) -> "ChordEvent":
        return ChordEvent(
            root_interval=d["root_interval"],
            triad=d["triad"],
            bass_interval=d.get("bass_interval", 0),
            seventh=d.get("seventh", "N"),
            ninth=d.get("ninth", "N"),
            eleventh=d.get("eleventh", "N"),
            thirteenth=d.get("thirteenth", "N"),
            root=d.get("root", ""),
            bass=d.get("bass", ""),
            harte=d.get("harte", ""),
        )

    def chord_type(self) -> tuple:
        """Root-invariant chord-type key, spec 07 §8.2."""
        return (self.triad, self.seventh, self.ninth, self.eleventh, self.thirteenth)


@dataclass(frozen=True)
class Song:
    genre: str
    tonic_pc: int
    bpm: int
    num_chords: int
    chords: tuple  # tuple[ChordEvent, ...]

    @staticmethod
    def from_dict(d: dict) -> "Song":
        return Song(
            genre=d["genre"],
            tonic_pc=d["tonic_pc"],
            bpm=d.get("bpm", 120),
            num_chords=d.get("num_chords", len(d["chords"])),
            chords=tuple(ChordEvent.from_dict(c) for c in d["chords"]),
        )


def resolve_degrees(chord: ChordEvent) -> list[Degree]:
    """Build the full active-degree list for a chord event, applying the
    §2.5 collision-merge rule. Degrees are returned in DEGREE_ORDER order
    (root, 3rd, 5th, 7th, 9th, 11th, 13th), one entry per *surviving* voice
    (merges collapse two roles into one Degree)."""
    third_semi, fifth_semi = TRIAD_THIRD_FIFTH[chord.triad]

    slots: dict[str, Optional[Degree]] = {}
    slots["root"] = Degree("root", 0, "1")
    if third_semi is not None:
        slots["3rd"] = Degree("3rd", third_semi, chord.triad)
    if fifth_semi is not None:
        slots["5th"] = Degree("5th", fifth_semi, chord.triad)

    ext_role_to_semi = {}
    for slot in EXT_SLOTS:
        token = getattr(chord, slot)
        if token != "N":
            role = SLOT_TO_ROLE[slot]
            semi = EXT_SEMI[slot][token]
            slots[role] = Degree(role, semi, token)
            ext_role_to_semi[role] = semi

    # §2.5 collision merges: (condition) -> (lower_role, higher_role)
    collisions = []
    if chord.seventh == "bb7" and chord.thirteenth == "13":
        collisions.append(("7th", "13th"))
    if chord.triad == "sus4" and chord.eleventh == "11":
        collisions.append(("3rd", "11th"))
    if chord.triad == "sus2" and chord.ninth == "9":
        collisions.append(("3rd", "9th"))
    if chord.triad == "diminished" and chord.eleventh == "#11":
        collisions.append(("5th", "11th"))
    if chord.triad == "augmented" and chord.thirteenth == "b13":
        collisions.append(("5th", "13th"))

    for lower, higher in collisions:
        if lower in slots and higher in slots:
            hi_deg = slots[higher]
            merged = Degree(higher, hi_deg.semitone, hi_deg.token,
                             merged_from=hi_deg.merged_from + (lower,))
            slots[higher] = merged
            del slots[lower]

    out = [slots[r] for r in DEGREE_ORDER if r in slots]
    return out


@dataclass
class VoicedChord:
    """Spec 07 §3 output contract."""
    index: int
    voicer: str
    midi: list
    roles: list
    dct_pitch: Optional[int]
    hands: Optional[dict]
    shape_id: Optional[str]
    vl_distance: float
    centroid: float
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "voicer": self.voicer,
            "midi": list(self.midi),
            "roles": list(self.roles),
            "dct_pitch": self.dct_pitch,
            "hands": self.hands,
            "shape_id": self.shape_id,
            "vl_distance": self.vl_distance,
            "centroid": self.centroid,
            "diagnostics": self.diagnostics,
        }
