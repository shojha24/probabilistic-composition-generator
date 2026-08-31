"""
voicing/rootless.py -- root-omission gating, spec 04 §3.1 (inherited
verbatim by specs 05, 06). This is one of the explicitly called-out hard
invariants: all three conditions must hold, no shortcuts.
"""
from __future__ import annotations

import random
from typing import Optional

from .types import ChordEvent, Degree
from .corpus import rootless_collision


def root_omission_condition_1(ctx: dict) -> bool:
    """(1) bass_module_active must be True. With no bass module, the voicer
    owns the root and omission is forced to impossible."""
    return bool(ctx.get("bass_module_active"))


def root_omission_condition_2(chord: ChordEvent) -> bool:
    """(2) bass_interval == 0 -- on an inversion, omitting the root leaves
    the root pitch class absent from the entire texture."""
    return chord.bass_interval == 0


def root_omission_condition_3(chord: ChordEvent, degrees_without_root: list[Degree],
                               gen_dir: str = None, root_pc: int = None) -> Optional[tuple]:
    """(3) the rootless pitch-class set must not be an exact member of the
    corpus vocabulary under a different root. Returns None if condition 3
    is satisfied (no collision); otherwise returns the colliding
    (root_pc, chord_type) pair."""
    if not isinstance(root_pc, int) or not 0 <= root_pc < 12:
        raise ValueError(f"root_pc must be in 0..11, got {root_pc!r}")
    kwargs = {} if gen_dir is None else {"gen_dir": gen_dir}
    rootless_pcs = frozenset((root_pc + d.semitone) % 12 for d in degrees_without_root)
    return rootless_collision(rootless_pcs, root_pc, chord.chord_type(), **kwargs)


def resolve_root_omission(chord: ChordEvent, degrees: list[Degree],
                           root_omission_p: float, ctx: dict, rng: random.Random,
                           allow_branch_b: bool = True,
                           branch_b_requires_octave_dct: bool = False,
                           gen_dir: str = None,
                           root_pc: int = None) -> tuple[bool, dict]:
    """Full gate + branch resolution (spec 04 §3.1, §6.2; inherited by 05/06).

    Returns (omit_root, flags). flags may contain:
      - "branch_b": True if root omission proceeded via the ambiguity-
        breaking branch (extended-chord DCT-on-top compensation)
      - "force_dct_top": True if the DCT must be placed on top (branch B
        requirement, and the general rootless-7th-DCT rule of spec 04 §6.1)
      - "force_dct_octave": True if the DCT must additionally be octave-
        doubled (jazz-synth's stricter branch-B condition, spec 06 §6)
    """
    def retained(reason: str | None = None) -> tuple[bool, dict]:
        flags = {"root_omission_status": "retained"}
        if reason is not None:
            flags["root_omission_gate_failure"] = reason
        return False, flags

    # Condition 1 -- no bass module means omission is impossible, full stop.
    if not root_omission_condition_1(ctx):
        return retained("bass_module_inactive")
    # Condition 2 -- inversions never omit the root.
    if not root_omission_condition_2(chord):
        return retained("inversion")
    # A seventh supplies the quality-bearing shell used by the jazz
    # rootless policies.  Triads remain rooted so the piano/guitar shell
    # rules do not manufacture a root copy after selection.
    if chord.seventh == "N":
        return retained("no_seventh_shell")

    if rng.random() >= root_omission_p:
        return retained("probability")

    degrees_without_root = [d for d in degrees if d.role != "root"]

    # Not one of spec 04 §3.1's three named conditions, but a load-bearing
    # implicit premise of all three: root omission only makes sense when
    # some *other* degree remains to carry the chord's identity. The spec
    # was written assuming a "normal" harmonic chord (always has at least
    # a 3rd or 5th); it doesn't anticipate a bare power-chord/"1" token
    # (root only, no 3rd/5th/7th/extensions at all) reaching this gate.
    # Without this guard, condition 3's collision check on an *empty*
    # rootless pc-set trivially finds no collision (nothing to collide
    # with) and root omission proceeds, leaving `degrees=[]` -- a
    # voicing with literally nothing left to voice, which is a hard
    # VoicingImpossible downstream (candidate_source has no pc, no role,
    # nothing to build from), not a legitimate rootless voicing.
    if not degrees_without_root:
        return retained("no_rootless_degrees")

    # Rootless voicing of a chord with an active 7th must always keep the
    # 7th (spec 04 §6.1) -- asserted by callers separately; here we only
    # decide legality of omitting the root at all.
    collision = root_omission_condition_3(
        chord, degrees_without_root, gen_dir, root_pc=root_pc
    )
    if collision is None:
        return True, {"root_omission_status": "omitted"}

    # Condition 3 failed: branch A/B resolution (spec 04 §6.2).
    if not allow_branch_b:
        return retained("alternate_root_collision")
    has_upper_ext = chord.ninth != "N" or chord.eleventh != "N" or chord.thirteenth != "N"
    if not has_upper_ext:
        return retained("alternate_root_collision")
    if rng.random() < 0.5:
        return retained("branch_a_collision")  # Branch A: keep the root.
    flags = {
        "branch_b": True,
        "force_dct_top": True,
        "root_omission_status": "omitted",
    }
    if branch_b_requires_octave_dct:
        flags["force_dct_octave"] = True
    return True, flags
