"""
voicing/guitar/model.py -- shape data model + fretboard realization, shared
by pop-guitar (spec 02) and jazz-guitar (spec 05).

A `Shape` is a *movable* (or open-position) fingering pattern, stored
root-relative per spec 02 §3.1. `OPEN_STRINGS` gives standard tuning, low
string (6) first, matching spec 02 §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# string 6 (low E) .. string 1 (high E), standard tuning
OPEN_STRINGS = (40, 45, 50, 55, 59, 64)
STRING_NUMS = (6, 5, 4, 3, 2, 1)


@dataclass(frozen=True)
class Shape:
    """`frets`: one entry per string (index 0 = string 6 .. index 5 =
    string 1), each an int fret offset or `"x"` for muted. For movable
    shapes, frets are stored relative to the shape's *native* root
    position (i.e. as physically fingered with the root at fret 0 on
    `root_string`); realizing at a different root is a uniform +N shift to
    every non-`x` entry (a literal barre move). `open_only` shapes are
    stored absolutely (already at their real fret) and are only ever
    offered for their native root pc.

    `degrees`: one entry per string, `None` for muted strings, else a role
    string ("root"/"3rd"/"5th"/"7th"/"9th"/"11th"/"13th").
    """
    id: str
    root_string: int             # 6 | 5 | 4 (which string the root's home position is on)
    frets: tuple                 # 6-tuple of int | "x"
    degrees: tuple                # 6-tuple of Optional[str]
    chord_types: tuple           # tuple of (triad, seventh, ninth, eleventh, thirteenth)
    barre: bool = False
    open_only: bool = False
    native_root_pc: Optional[int] = None  # required if open_only
    tags: tuple = ()
    omitted_roles: tuple = ()

    def span(self) -> int:
        fretted = [f for f in self.frets if f != "x" and f != 0]
        if not fretted:
            return 0
        return max(fretted) - min(fretted)

    def role_semitones(self) -> dict:
        """{role: semitone offset above root}, derived once from
        (open_string_pc, fret, degree) minus the root's own semitone (0).
        Robust to however many strings double a role: last string wins,
        which is fine since doubled roles all share the same semitone.

        Falls back to `native_root_pc` (required for shapes that don't
        sound the root at all -- e.g. a root omitted by the §3.3 omission
        ladder) when no string is labelled "root"."""
        root_pc = None
        for s_idx, fret in enumerate(self.frets):
            if fret == "x":
                continue
            if self.degrees[s_idx] == "root":
                root_pc = (OPEN_STRINGS[s_idx] + fret) % 12
                break
        if root_pc is None:
            if self.native_root_pc is not None:
                root_pc = self.native_root_pc % 12
            else:
                raise ValueError(f"shape {self.id} has no root string and no native_root_pc")
        out = {}
        for s_idx, fret in enumerate(self.frets):
            if fret == "x":
                continue
            role = self.degrees[s_idx]
            if role is None:
                continue
            pc = (OPEN_STRINGS[s_idx] + fret) % 12
            out[role] = (pc - root_pc) % 12
        return out

    def muted_strings(self) -> frozenset:
        return frozenset(STRING_NUMS[i] for i, f in enumerate(self.frets) if f == "x")

    def native_root_fret(self) -> int:
        """The fret on `root_string` in this shape's stored (unshifted)
        pattern that sounds the root."""
        s_idx = STRING_NUMS.index(self.root_string)
        f = self.frets[s_idx]
        if f == "x":
            raise ValueError(f"shape {self.id}: root_string is muted")
        return f


def realize(shape: Shape, chord_root_pc: int, max_fret: int = 12,
            octave_offset: int = 0) -> Optional[dict]:
    """Realize `shape` for a concrete chord root pitch-class. Returns
    `{"pitches": [...], "roles": [...], "strings": [...], "root_fret": int,
    "muted": frozenset}` sorted ascending by pitch, or `None` if unplayable
    (a required fret would fall outside `[0, max_fret]`).

    Open-position shapes are only offered for their native root and are
    never transposed (spec 02 §3.1).

    `octave_offset` (a multiple of 12, default 0) shifts the whole shape
    up/down an extra octave on top of the usual `shift = (chord_root_pc -
    native_root_pc) % 12` -- a movable shape's `shift` is only ever
    computed mod 12, so without this a shape has exactly ONE fretting
    (and thus one absolute register) per chord root, even though the
    identical pitch classes are perfectly playable an octave away too.
    That single-register-per-anchor limit is what left register gaps
    between the 3 anchors' native positions for some roots (e.g.
    G:sus4(7): the E-anchor's low fretting fails the low-interval-limit
    and the A/D-anchor's only fretting sits an octave too high, with
    nothing in between) -- callers should try the extra octaves too, not
    just rely on having 3 anchors."""
    if shape.open_only:
        if octave_offset != 0:
            return None
        if shape.native_root_pc is None or shape.native_root_pc % 12 != chord_root_pc % 12:
            return None
        pitches, roles, strings = [], [], []
        for s_idx, fret in enumerate(shape.frets):
            if fret == "x":
                continue
            pitches.append(OPEN_STRINGS[s_idx] + fret)
            roles.append(shape.degrees[s_idx])
            strings.append(STRING_NUMS[s_idx])
        order = sorted(range(len(pitches)), key=lambda i: pitches[i])
        return {
            "pitches": [pitches[i] for i in order],
            "roles": [roles[i] for i in order],
            "strings": [strings[i] for i in order],
            "root_fret": shape.frets[STRING_NUMS.index(shape.root_string)],
            "muted": shape.muted_strings(),
        }

    if shape.native_root_pc is not None:
        native_root_pc = shape.native_root_pc % 12
    else:
        s_idx_root = STRING_NUMS.index(shape.root_string)
        if shape.frets[s_idx_root] == "x":
            raise ValueError(f"shape {shape.id}: root_string muted and no native_root_pc set")
        native_root_pc = (OPEN_STRINGS[s_idx_root] + shape.frets[s_idx_root]) % 12
    shift = (chord_root_pc - native_root_pc) % 12 + octave_offset
    frets = []
    for f in shape.frets:
        if f == "x":
            frets.append("x")
        else:
            nf = f + shift
            if nf < 0 or nf > max_fret:
                return None
            frets.append(nf)
    pitches, roles, strings = [], [], []
    for s_idx, fret in enumerate(frets):
        if fret == "x":
            continue
        pitches.append(OPEN_STRINGS[s_idx] + fret)
        roles.append(shape.degrees[s_idx])
        strings.append(STRING_NUMS[s_idx])
    order = sorted(range(len(pitches)), key=lambda i: pitches[i])
    return {
        "pitches": [pitches[i] for i in order],
        "roles": [roles[i] for i in order],
        "strings": [strings[i] for i in order],
        "root_fret": shift,
        "muted": shape.muted_strings(),
    }


def shape_distance(root_fret_a: int, id_a: str, muted_a: frozenset,
                    root_fret_b: int, id_b: str, muted_b: frozenset) -> float:
    """Spec 02 §5 discrete shape-distance term."""
    return (abs(root_fret_a - root_fret_b)
            + 2 * (id_a != id_b)
            + len(muted_a.symmetric_difference(muted_b)))
