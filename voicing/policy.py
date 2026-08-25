"""
voicing/policy.py -- VoicerPolicy schema, spec 07 §10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class VoicerPolicy:
    voicer_id: str
    genre: str
    instrument: str

    # register (§7)
    window_lo: int
    window_hi: int
    drift_free: int
    drift_tol: int

    # voice budget (§4)
    min_voices: int
    max_voices: int
    p_omit_fifth: float
    root_double_p: float
    root_omission_p: float
    max_doublings: int

    # parsimony (§6)
    tau: float
    w_vl: float
    w_drift: float
    w_space: float
    w_div: float
    w_role: float
    plr_bonus: float
    unmatched_penalty: float
    vl_target_mean: float
    vl_target_sd: float

    # disambiguation (§5)
    dct_mode_weights: dict

    # hooks
    candidate_source: Callable
    role_penalty: Callable
    post_filter: Callable
    section_profile: Optional[dict] = None

    # extra per-voicer knobs that don't fit the generic schema but every
    # voicer needs somewhere to hang instrument-specific constants (hand
    # windows, fret limits, spread templates, etc). Kept as a free-form dict
    # so spec 07's frozen schema (§10) is not forked per voicer.
    extra: dict = field(default_factory=dict)
