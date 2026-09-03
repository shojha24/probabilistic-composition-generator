"""
voicing/select.py -- Stage 1 tone selection, spec 07 §4.1.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .types import ChordEvent, Degree, resolve_degrees
from .rootless import resolve_root_omission


@dataclass
class SelectedTones:
    degrees: list             # list[Degree], final required degrees (root/5th omissions applied)
    doubling_roles: list      # roles eligible for doubling in Stage 2 (root/5th/DCT, per policy)
    root_omitted: bool
    fifth_omitted: bool
    root_doubled_bias: bool   # whether Stage 2 should bias toward doubling the root
    flags: dict = field(default_factory=dict)  # branch_b / force_dct_top / force_dct_octave


def select_tones(chord: ChordEvent, policy, ctx: dict, rng: random.Random,
                  root_omission_gate: bool = False,
                  branch_b_requires_octave_dct: bool = False,
                  extra_doubling_targets: tuple = ("root", "5th"),
                  gen_dir: str = None,
                  root_pc: int = None) -> SelectedTones:
    """Build the active degree set and resolve the probabilistic
    root-doubling / root-omission / 5th-thinning policy (spec 07 §4.1,
    parent §5). Extensions are NEVER dropped here -- every active extension
    slot survives into `degrees` unconditionally, per §4.1's hardest rule.

    `root_omission_gate=True` selects the strict, three-condition jazz gate
    (spec 04 §3.1, inherited by 05/06); `False` uses the same machinery but
    with condition 3's ambiguity check still applied defensively (it can
    only prevent, never cause, a mislabelled rootless voicing) and with
    branch B disabled, matching the simpler pop-voicer row in parent §5.
    """
    degrees = resolve_degrees(chord)
    if not isinstance(root_pc, int) or not 0 <= root_pc < 12:
        raise ValueError(f"root_pc must be in 0..11, got {root_pc!r}")

    # --- 5th thinning (only major/minor triads carry a freely-omittable 5th)
    fifth_omitted = False
    has_fifth = any(d.role == "5th" for d in degrees)
    if has_fifth and chord.triad in ("major", "minor"):
        if rng.random() < policy.p_omit_fifth:
            fifth_omitted = True
            degrees = [d for d in degrees if d.role != "5th"]

    # --- root omission (bass-coordination policy, parent §5)
    omission_enabled = root_omission_gate or bool(
        policy.extra.get("root_omission_gate")
    )
    if omission_enabled:
        root_omitted, flags = resolve_root_omission(
            chord, degrees, policy.root_omission_p, ctx, rng,
            allow_branch_b=omission_enabled,
            branch_b_requires_octave_dct=branch_b_requires_octave_dct,
            gen_dir=gen_dir,
            root_pc=root_pc,
        )
    else:
        root_omitted = False
        flags = {
            "root_omission_status": "retained",
            "root_omission_gate_failure": "gate_disabled",
        }
    if root_omitted:
        degrees = [d for d in degrees if d.role != "root"]

    # --- root doubling bias (only meaningful if root present)
    root_doubled_bias = False
    if not root_omitted and ctx.get("bass_module_active"):
        root_doubled_bias = rng.random() < policy.root_double_p

    doubling_roles = list(extra_doubling_targets)
    if root_omitted and "root" in doubling_roles:
        doubling_roles = [r for r in doubling_roles if r != "root"]

    return SelectedTones(
        degrees=degrees,
        doubling_roles=doubling_roles,
        root_omitted=root_omitted,
        fifth_omitted=fifth_omitted,
        root_doubled_bias=root_doubled_bias,
        flags=flags,
    )
