"""
voicing/engine.py -- the shared voicing engine, spec 07 §4 (pipeline),
§9 (relaxation ladder).
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .types import ChordEvent, Song, VoicedChord, resolve_degrees
from .policy import VoicerPolicy
from .select import select_tones, SelectedTones
from .dct import (compute_dct, dct_pitch_class, exposure_branch, predicate_top,
                   predicate_isolated, predicate_octave, secondary_ok)
from .vl import vl_distance, normalize_vl, transformation_bonus
from .anchor import anchor_center as compute_anchor_center, drift_penalty, window_ok, drift_ok, centroid_of
from .spacing import policy_spacing_penalty, spacing_hard_ok, spacing_penalty, low_interval_limit_ok
from .candidates import (
    Candidate,
    dct_expose_repair,
    dense_hand_layout,
    post_filter_repairs,
    preferred_template_for,
    template_selection_penalty,
)
from .diversity import DiversityCounter, shape_signature

logger = logging.getLogger("voicing.engine")


class VoicingImpossible(Exception):
    def __init__(self, chord: ChordEvent, index: int):
        super().__init__(f"No legal voicing for chord #{index}: {chord}")
        self.chord = chord
        self.index = index


@dataclass
class CandidateGenParams:
    chord: ChordEvent
    root_pc: int                 # absolute runtime root pitch class
    degrees: list                # required Degree list (post 5th/root omission)
    doubling_roles: list
    max_doublings: int
    window_lo: int
    window_hi: int
    anchor_center: int
    prev_midi: Optional[list]
    rng: random.Random
    ctx: dict
    policy: VoicerPolicy
    dct_role: Optional[str]
    section: Optional[str]
    max_voices: int
    anchor_shift: int = 0
    forced_dct_role: Optional[str] = None
    source_phase: str = "normal"


def _role_pcs(root_pc: int, degrees: list) -> list:
    return [(d.role, (root_pc + d.semitone) % 12) for d in degrees]


def _default_dct_sample(weights: dict, rng: random.Random) -> str:
    items = list(weights.items())
    total = sum(w for _, w in items)
    if total <= 0:
        return "top"
    r = rng.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


class Engine:
    def __init__(self, policy: VoicerPolicy, ctx: dict,
                 diversity_counter: Optional[DiversityCounter] = None,
                 signature_bands: Optional[dict] = None,
                 gen_dir: Optional[str] = None,
                 root_omission_gate: bool = False,
                 branch_b_requires_octave_dct: bool = False):
        self.policy = policy
        self.ctx = dict(ctx)
        self.diversity = diversity_counter if diversity_counter is not None else DiversityCounter()
        self.signature_bands = signature_bands or {
            "low": (policy.window_lo, policy.window_lo + (policy.window_hi - policy.window_lo) // 3),
            "mid": (policy.window_lo + (policy.window_hi - policy.window_lo) // 3 + 1,
                    policy.window_lo + 2 * (policy.window_hi - policy.window_lo) // 3),
            "high": (policy.window_lo + 2 * (policy.window_hi - policy.window_lo) // 3 + 1, policy.window_hi),
        }
        self.gen_dir = gen_dir
        self.root_omission_gate = root_omission_gate
        self.branch_b_requires_octave_dct = branch_b_requires_octave_dct
        self.rng = random.Random(ctx.get("seed", 0))
        self.window_relaxed_count = 0
        self.total_count = 0
        self.extension_dropped_count = 0
        self.log = []
        self.extension_drop_records = []

    # ------------------------------------------------------------------
    def run(self, song: Song) -> list[VoicedChord]:
        if not isinstance(song.tonic_pc, int) or not 0 <= song.tonic_pc < 12:
            raise ValueError(f"song.tonic_pc must be in 0..11, got {song.tonic_pc!r}")
        self.ctx["_active_tonic_pc"] = song.tonic_pc
        self.ctx["_active_genre"] = song.genre
        self.ctx["bass_module_active"] = bool(self.ctx.get("bass_module_active", False))
        center = compute_anchor_center(song.tonic_pc, self.policy.window_lo, self.policy.window_hi)
        prev_cand = None
        prev_chord = None
        out = []
        for t, chord in enumerate(song.chords):
            voiced, cand = self.step(t, chord, prev_cand, prev_chord, center)
            out.append(voiced)
            prev_cand = cand
            prev_chord = chord
        return out

    # ------------------------------------------------------------------
    def _effective_policy_and_center(self, base_center: int):
        """Apply section_profile overrides (specs 01/03), including
        anchor_shift, which moves the anchor for this chord only."""
        section = self.ctx.get("section")
        overrides = {}
        anchor_shift = 0
        if self.policy.section_profile and section in self.policy.section_profile:
            overrides = dict(self.policy.section_profile[section])
            anchor_shift = overrides.pop("anchor_shift", 0)
        center = base_center + anchor_shift
        return overrides, center, section, anchor_shift

    def _param(self, overrides: dict, name: str, default):
        return overrides.get(name, default)

    @staticmethod
    def _candidate_key(candidate) -> tuple:
        hands = tuple(
            (name, tuple(values))
            for name, values in sorted((candidate.hands or {}).items())
        )
        return (
            tuple(candidate.pitches), tuple(candidate.roles),
            candidate.shape_id, hands,
        )

    @staticmethod
    def _copy_candidate(candidate, **updates):
        meta = dict(candidate.meta)
        meta.update(updates)
        return Candidate(
            list(candidate.pitches), list(candidate.roles),
            candidate.shape_id, candidate.hands, meta,
        )

    def _annotate_source_candidates(self, candidates, policy, phase: str):
        """Add stable provenance defaults without changing source ordering."""
        source_name = "shape_library" if policy.instrument == "guitar" else "free_placement"
        annotated = []
        for rank, candidate in enumerate(candidates):
            base_shape_id = candidate.meta.get("base_shape_id")
            shape_id = candidate.shape_id
            if base_shape_id is None and shape_id and "@oct" in shape_id:
                base_shape_id = shape_id.split("@oct", 1)[0]
            annotated.append(self._copy_candidate(
                candidate,
                candidate_source=candidate.meta.get("candidate_source", source_name),
                generation_phase=candidate.meta.get("generation_phase", phase),
                source_rank=rank,
                dct_repair=bool(candidate.meta.get("dct_repair", False)),
                forced_dct_copy=bool(candidate.meta.get("forced_dct_copy", False)),
                base_shape_id=base_shape_id,
                octave_offset=int(candidate.meta.get("octave_offset", 0)),
                extensions_dropped=tuple(
                    candidate.meta.get("extensions_dropped") or ()
                ),
            ))
        return annotated

    @staticmethod
    def _degree_signature(degrees) -> tuple:
        return tuple(
            (degree.role, degree.semitone, degree.token, degree.merged_from)
            for degree in degrees
        )

    def _hard_clean_ok(self, candidate, chord, selected, required_degrees,
                       root_pc, bass_active, allow_dropped,
                       min_voices, max_voices, window_lo, window_hi,
                       center, drift_tol, min_gap, cluster_cap) -> bool:
        if not self._candidate_sound_set_ok(
                candidate, chord, selected, required_degrees, root_pc,
                bass_active, allow_dropped):
            return False
        if len(candidate.pitches) < min_voices or len(candidate.pitches) > max_voices:
            return False
        if not window_ok(candidate.pitches, window_lo, window_hi):
            return False
        if not low_interval_limit_ok(candidate.pitches):
            return False
        if not self._cluster_ok(candidate, min_gap, cluster_cap):
            return False
        return drift_ok(centroid_of(candidate.pitches), center, drift_tol)

    def _dct_and_post_filter_ok(self, candidate, dct_role, dct_pc,
                                secondary_roles, sampled_branch,
                                relax_any_branch, prev_cand, policy) -> bool:
        if not self._dct_filter(
                candidate, dct_role, dct_pc, secondary_roles,
                sampled_branch, relax_any_branch=relax_any_branch,
        ):
            return False
        return policy.post_filter is None or policy.post_filter(
            candidate, prev_cand, self.ctx, policy
        )

    # ------------------------------------------------------------------
    def step(self, t: int, chord: ChordEvent, prev_cand, prev_chord, base_center: int):
        self.total_count += 1
        self.ctx["_current_chord"] = chord
        self.ctx["_current_t"] = t
        overrides, center, section, anchor_shift = self._effective_policy_and_center(base_center)
        policy = self.policy
        active_tonic_pc = self.ctx.get("_active_tonic_pc")
        if not isinstance(active_tonic_pc, int) or not 0 <= active_tonic_pc < 12:
            raise ValueError("Engine runtime absolute root context is missing or invalid")
        root_pc = (active_tonic_pc + chord.root_interval) % 12
        self.ctx["_current_root_pc"] = root_pc
        bass_active = bool(self.ctx.get("bass_module_active", False))
        root_gate = self.root_omission_gate or (
            self.ctx.get("_active_genre") == "jazz" and bass_active
        )
        branch_b_octave = self.branch_b_requires_octave_dct or (
            root_gate and policy.voicer_id == "jazz-synth"
        )
        # Exposed for voicer hooks (post_filter/role_penalty) that need the
        # *effective*, section-shifted anchor rather than the song-level
        # base center -- e.g. spec 03's bottom-voice octave-band anchoring,
        # which is relative to this chord's actual anchor, not the song's.
        self.ctx["_current_anchor_center"] = center

        max_voices = self._param(overrides, "max_voices", policy.max_voices)
        root_double_p = self._param(overrides, "root_double_p", policy.root_double_p)
        root_omission_p = self._param(overrides, "root_omission_p", policy.root_omission_p)
        drift_free = self._param(overrides, "drift_free", policy.drift_free)

        eff_policy = policy
        if root_double_p != policy.root_double_p or root_omission_p != policy.root_omission_p:
            import dataclasses
            eff_policy = dataclasses.replace(policy, root_double_p=root_double_p,
                                              root_omission_p=root_omission_p)

        preferred_template_for(chord, policy, self.ctx)
        selected = select_tones(chord, eff_policy, self.ctx, self.rng,
                                 root_pc=root_pc,
                                 root_omission_gate=root_gate,
                                 branch_b_requires_octave_dct=branch_b_octave,
                                 extra_doubling_targets=tuple(policy.extra.get(
                                     "doubling_targets", ("root", "5th"))),
                                 gen_dir=self.gen_dir)
        self.ctx["_selected_tone_flags"] = selected.flags
        self.ctx["_selected_tone_count"] = len(selected.degrees)

        dct_role, secondary_roles = compute_dct(chord, selected.degrees, gen_dir=self.gen_dir)
        dct_pc = dct_pitch_class(chord, selected.degrees, dct_role) if dct_role else None
        if dct_pc is not None:
            dct_pc = (root_pc + dct_pc) % 12
        self.ctx["_current_dct_role"] = dct_role
        self.ctx["_current_dct_pc"] = dct_pc

        # DCT must never be dropped by omission -- assert the degree survived.
        if dct_role is not None:
            assert any(d.role == dct_role for d in selected.degrees), \
                "DCT dropped by omission policy -- hard invariant violated"

        prev_midi = prev_cand.pitches if prev_cand is not None else None

        sampled_branch = None
        if selected.flags.get("force_dct_octave"):
            sampled_branch = "octave"
        elif selected.flags.get("force_dct_top"):
            sampled_branch = "top"
        elif dct_role is not None:
            sampled_branch = _default_dct_sample(policy.dct_mode_weights, self.rng)

        forced_dct_role = dct_role if sampled_branch == "octave" else None
        self.ctx["_current_forced_dct_role"] = forced_dct_role
        window_lo, window_hi = policy.window_lo, policy.window_hi
        max_doublings = policy.max_doublings
        window_relaxed = False

        chosen = None
        chosen_diag = {}
        source_cache = {}
        retention_limit = max(
            0, int(policy.extra.get("max_retained_hard_clean", 256))
        )
        retained_by_level = {}
        retained_keys = set()
        retained_used = 0
        repair_diagnostics = {
            "repair_inputs": 0,
            "repair_generated": 0,
            "repair_accepted": 0,
        }
        # spec 07 §9's ladder floor (step 8) forbids relaxing extension
        # presence at all; spec 02/05 §3.3 carve out extension-dropping as
        # a guitar-only escape hatch *beneath* that floor, strictly lower
        # priority than every generic ladder step (including step 7's DCT
        # branch relaxation). A single flattened level loop that just
        # takes "the first level with any surviving candidate" doesn't
        # respect that ordering, because `find_matching_shapes` already
        # mixes dropped-role candidates into the very first level's raw
        # pool -- a `sampled_branch` mismatch (e.g. "top" wanted but no
        # undropped shape has the DCT on top) would then get "fixed" by
        # accepting a dropped candidate instead of first trying to relax
        # the branch itself, even though branch-relaxation is strictly
        # preferred by spec. So: run the *entire* 8-level ladder twice --
        # once considering only non-dropped candidates, and only if that
        # exhausts every level with nothing, a second pass that allows
        # dropped candidates too.
        source_phases = (
            ("normal", "guitar_octave")
            if policy.instrument == "guitar" else ("normal",)
        )
        for allow_dropped in (False, True):
            for source_phase in source_phases:
              for level in range(0, 8):
                lvl_max_doublings = max_doublings + (1 if level >= 3 else 0)
                lvl_max_voices = max_voices + (1 if (level >= 4 and not policy.extra.get("fixed_voice_count")) else 0)
                lvl_window_lo, lvl_window_hi = window_lo, window_hi
                if level >= 6:
                    lvl_window_lo = window_lo - 12
                    lvl_window_hi = window_hi + 12
                # Only step 6 itself (window widen) should be credited with
                # "relaxing the window" for the WINDOW_RELAXED metric -- the
                # widened bounds stay in effect for level 7 too (relaxations
                # are cumulative, same as doublings/voices/5th-omit above),
                # but if a candidate only succeeds at level 7 that's because
                # DCT-branch relaxation (a distinct, separately-gated step)
                # was the actual cause, not the window. Flattening both into
                # "level >= 6 => window relaxed" over-attributes every
                # DCT-branch-relax success to the window metric too.
                lvl_window_relaxed = (level == 6)
                lvl_degrees = list(selected.degrees)
                if level >= 5 and chord.triad in ("major", "minor"):
                    lvl_degrees = [d for d in lvl_degrees if d.role != "5th"]

                lvl_doubling_roles = list(selected.doubling_roles)
                # Step 7 relaxes the sampled DCT branch. Do not keep asking
                # the source for an octave copy once any valid exposure
                # branch is allowed, or the forced-copy topology can starve
                # otherwise legal top/isolated candidates.
                lvl_forced_dct_role = (
                    forced_dct_role if level < 7 else None
                )

                params = CandidateGenParams(
                    chord=chord, root_pc=root_pc, degrees=lvl_degrees,
                    doubling_roles=lvl_doubling_roles,
                    max_doublings=lvl_max_doublings, window_lo=lvl_window_lo, window_hi=lvl_window_hi,
                    anchor_center=center, prev_midi=prev_midi, rng=self.rng, ctx=self.ctx,
                    policy=policy, dct_role=dct_role, section=section, max_voices=lvl_max_voices,
                    anchor_shift=anchor_shift,
                    forced_dct_role=lvl_forced_dct_role,
                    source_phase=source_phase,
                )
                source_key = (
                    tuple(
                        (degree.role, degree.semitone, degree.token,
                         degree.merged_from)
                        for degree in lvl_degrees
                    ),
                    tuple(lvl_doubling_roles),
                    lvl_max_doublings,
                    lvl_max_voices,
                    lvl_window_lo,
                    lvl_window_hi,
                    center,
                    anchor_shift,
                    tuple(prev_midi or ()),
                    dct_role,
                    self.ctx.get("_template_target"),
                    lvl_forced_dct_role,
                    source_phase,
                )
                if source_key not in source_cache:
                    source_cache[source_key] = self._annotate_source_candidates(
                        policy.candidate_source(params), policy, source_phase
                    )
                raw = source_cache[source_key]
                if not allow_dropped:
                    raw = [c for c in raw if not c.meta.get("extensions_dropped")]

                eff_drift_tol = policy.drift_tol * (1.5 if level >= 2 else 1.0)

                filtered = []
                spacing_survivors = []
                dct_clean_survivors = []
                min_gap = policy.extra.get("cluster_min_gap", 13)
                cluster_cap = policy.extra.get("cluster_cap")
                for c in raw:
                    if not self._hard_clean_ok(
                            c, chord, selected, lvl_degrees, root_pc, bass_active,
                            allow_dropped, policy.min_voices, lvl_max_voices,
                            lvl_window_lo, lvl_window_hi, center, eff_drift_tol,
                            min_gap, cluster_cap):
                        continue
                    spacing_survivors.append(c)
                    degree_signature = self._degree_signature(lvl_degrees)
                    retention_key = (
                        source_phase, degree_signature,
                        bool(selected.root_omitted), bool(selected.fifth_omitted),
                        tuple(c.meta.get("extensions_dropped") or ()),
                        self._candidate_key(c),
                    )
                    level_pool = retained_by_level.setdefault(level, [])
                    if (retention_limit and len(level_pool) < retention_limit
                            and retention_key not in retained_keys):
                        level_pool.append({
                            "level": level,
                            "source_phase": source_phase,
                            "degree_signature": degree_signature,
                            "root_omitted": bool(selected.root_omitted),
                            "fifth_omitted": bool(selected.fifth_omitted),
                            "window": (lvl_window_lo, lvl_window_hi),
                            "max_voices": lvl_max_voices,
                            "cluster_cap": cluster_cap,
                            "drift_tol": eff_drift_tol,
                            "candidate": c,
                        })
                        retained_keys.add(retention_key)
                    dct_ok = self._dct_filter(
                        c, dct_role, dct_pc, secondary_roles, sampled_branch,
                        relax_any_branch=level >= 7,
                    )
                    if not dct_ok:
                        continue
                    # Keep DCT-valid candidates available to the separate
                    # post-filter repair path. Combining these checks here
                    # would discard exactly the candidates that can be
                    # repaired by moving their lowest voice.
                    dct_clean_survivors.append(c)
                    if (policy.post_filter is not None and
                            not policy.post_filter(c, prev_cand, self.ctx, policy)):
                        continue
                    filtered.append(c)

                # Lazy DCT-exposure repair (spec 07 §5.2/§5.3): the beam
                # search that builds `raw` has no notion of which role is
                # the DCT, so for role combinations whose natural stacking
                # order sandwiches the DCT tone between two non-clashing
                # neighbours, *no* naturally-generated candidate may ever
                # satisfy exposure -- widening window/voices doesn't help,
                # since the problem is topology, not headroom. Only pay for
                # this (and only touch pitches, not regenerate the pool)
                # when every spacing-clean survivor at this level actually
                # failed the DCT filter specifically.
                #
                # Opt-in only (`policy.extra["dct_repair_ok"]`): this
                # repositions a single pitch by pc within a flat
                # (window_lo, window_hi) band, which is only meaningful for
                # voicers whose candidates are genuinely free-register
                # (plain `free_placement`). For guitar (shape-library
                # candidates tied to actual fretboard positions) or piano
                # (hand-split candidates where the flat window doesn't
                # distinguish LH from RH), an arbitrary pc-in-window
                # reposition can silently produce a candidate that isn't
                # actually realizable by that voicer's physical model --
                # confirmed by a regression this repair caused in
                # previously-validated pop-guitar tests when it applied
                # unconditionally to every voicer.
                if (not filtered and spacing_survivors and dct_role is not None
                        and policy.extra.get("dct_repair_ok")):
                    repair_inputs = int(policy.extra.get("max_repair_inputs", 64))
                    repair_variants = int(policy.extra.get("max_repair_variants", 256))
                    secondary_assignments = int(
                        policy.extra.get("max_secondary_assignments", 32)
                    )
                    repair_diagnostics["repair_inputs"] += min(
                        len(spacing_survivors), max(0, repair_inputs)
                    )
                    generated = 0
                    for c in spacing_survivors[:max(0, repair_inputs)]:
                        for repaired in dct_expose_repair(
                                c, dct_role, secondary_roles, sampled_branch,
                                lvl_window_lo, lvl_window_hi, center,
                                max_variants=max(0, repair_variants - generated),
                                max_secondary_assignments=secondary_assignments,
                                max_voices=lvl_max_voices,
                                max_doublings=lvl_max_doublings):
                            generated += 1
                            repair_diagnostics["repair_generated"] += 1
                            if not self._hard_clean_ok(
                                    repaired, chord, selected, lvl_degrees, root_pc,
                                    bass_active, allow_dropped, policy.min_voices,
                                    lvl_max_voices, lvl_window_lo, lvl_window_hi,
                                    center, eff_drift_tol, min_gap, cluster_cap):
                                continue
                            if not self._dct_filter(
                                    repaired, dct_role, dct_pc, secondary_roles,
                                    sampled_branch, relax_any_branch=level >= 7):
                                continue
                            if (policy.post_filter is not None and
                                    not policy.post_filter(
                                        repaired, prev_cand, self.ctx, policy
                                    )):
                                dct_clean_survivors.append(repaired)
                                continue
                            filtered.append(repaired)
                            repair_diagnostics["repair_accepted"] += 1
                            if generated >= repair_variants:
                                break
                        if generated >= repair_variants:
                            break

                # Lazy post_filter repair (opt-in, same flag): a voicer's
                # hard post_filter (e.g. spec 03's bottom-voice octave-band
                # anchoring) is invisible to the anchor-distance-only beam
                # search, which can consistently settle on a tied-cost
                # register combination that fails it on *every* surviving
                # candidate even though an equal-cost alternative would
                # have passed (e.g. a clash-avoidance push landing the
                # bottom role at the window floor instead of a few
                # semitones higher). Only tried when every DCT-clean
                # survivor at this level failed post_filter specifically.
                if (not filtered and dct_clean_survivors and policy.post_filter is not None
                        and policy.extra.get("dct_repair_ok")):
                    repair_inputs = int(policy.extra.get("max_repair_inputs", 64))
                    repair_variants = int(policy.extra.get("max_repair_variants", 256))
                    repair_diagnostics["repair_inputs"] += min(
                        len(dct_clean_survivors), max(0, repair_inputs)
                    )
                    generated = 0
                    for c in dct_clean_survivors[:max(0, repair_inputs)]:
                        for repaired in post_filter_repairs(
                                c, lvl_window_lo, lvl_window_hi, center, min_gap,
                                policy.post_filter, prev_cand, self.ctx, policy,
                                max_variants=max(0, repair_variants - generated)):
                            generated += 1
                            repair_diagnostics["repair_generated"] += 1
                            if not self._hard_clean_ok(
                                    repaired, chord, selected, lvl_degrees, root_pc,
                                    bass_active, allow_dropped, policy.min_voices,
                                    lvl_max_voices, lvl_window_lo, lvl_window_hi,
                                    center, eff_drift_tol, min_gap, cluster_cap):
                                continue
                            if not self._dct_and_post_filter_ok(
                                    repaired, dct_role, dct_pc, secondary_roles,
                                    sampled_branch, level >= 7, prev_cand, policy):
                                continue
                            filtered.append(repaired)
                            repair_diagnostics["repair_accepted"] += 1
                            if generated >= repair_variants:
                                break
                        if generated >= repair_variants:
                            break

                # Piano hand topology is intentionally kept out of the flat
                # synth repair path. This source is fallback-only and is
                # consulted only after the ordinary source and any guarded
                # repair have produced no valid candidate.
                if (not filtered and source_phase == "normal"
                        and policy.voicer_id == "pop-piano"
                        and policy.extra.get("dense_layout_fallback")):
                    dense_role_pcs = [
                        (degree.role, (root_pc + degree.semitone) % 12)
                        for degree in lvl_degrees
                    ]
                    dense_lh = tuple(policy.extra.get("dense_lh_window", (43, 60)))
                    dense_rh = tuple(policy.extra.get("dense_rh_window", (55, 88)))
                    dense_raw = dense_hand_layout(
                        dense_role_pcs, dense_lh, dense_rh,
                        int(policy.extra.get("dense_lh_max_voices", 3)),
                        int(policy.extra.get("dense_rh_max_voices", 4)),
                        int(policy.extra.get("dense_max_hand_span", 14)),
                        center, lvl_max_voices,
                        max_candidates=int(policy.extra.get(
                            "max_dense_layout_candidates", 512)),
                        forced_dct_role=lvl_forced_dct_role,
                        max_doublings=lvl_max_doublings,
                    )
                    dense_raw = self._annotate_source_candidates(
                        dense_raw, policy, "dense_hand"
                    )
                    for dense_candidate in dense_raw:
                        if not self._hard_clean_ok(
                                dense_candidate, chord, selected, lvl_degrees,
                                root_pc, bass_active, allow_dropped,
                                policy.min_voices, lvl_max_voices,
                                lvl_window_lo, lvl_window_hi, center,
                                eff_drift_tol, min_gap, cluster_cap):
                            continue
                        if not self._dct_and_post_filter_ok(
                                dense_candidate, dct_role, dct_pc, secondary_roles,
                                sampled_branch, level >= 7, prev_cand, policy):
                            continue
                        filtered.append(dense_candidate)

                # Earlier hard-clean candidates are a search-completeness
                # fallback, never a replacement for the current source.
                if not filtered and retention_limit:
                    current_degree_signature = self._degree_signature(lvl_degrees)
                    retained_candidates = []
                    prior_records = [
                        record
                        for record_level in sorted(retained_by_level)
                        if record_level < level
                        for record in retained_by_level[record_level]
                    ]
                    prior_records.sort(key=lambda record: (
                        record["level"],
                        record["candidate"].meta.get("source_rank", 0),
                        self._candidate_key(record["candidate"]),
                    ))
                    for record in prior_records:
                        if record["source_phase"] != source_phase:
                            continue
                        if record["degree_signature"] != current_degree_signature:
                            continue
                        if record["root_omitted"] != bool(selected.root_omitted):
                            continue
                        if record["fifth_omitted"] != bool(selected.fifth_omitted):
                            continue
                        retained_candidate = self._copy_candidate(
                            record["candidate"],
                            generation_phase="retained",
                            retained=True,
                            retained_from_level=record["level"],
                        )
                        if not self._hard_clean_ok(
                                retained_candidate, chord, selected, lvl_degrees,
                                root_pc, bass_active, allow_dropped,
                                policy.min_voices, lvl_max_voices,
                                lvl_window_lo, lvl_window_hi, center,
                                eff_drift_tol, min_gap, cluster_cap):
                            continue
                        if not self._dct_and_post_filter_ok(
                                retained_candidate, dct_role, dct_pc,
                                secondary_roles, sampled_branch,
                                level >= 7, prev_cand, policy):
                            continue
                        retained_candidates.append(retained_candidate)
                    if retained_candidates:
                        retained_used = len(retained_candidates)
                        filtered = retained_candidates
                if filtered:
                    # Stable physical deduplication is important when a
                    # repair or retained source reaches the same realization
                    # as the current source.
                    unique_filtered = []
                    seen_filtered = set()
                    for candidate in filtered:
                        key = self._candidate_key(candidate)
                        if key in seen_filtered:
                            continue
                        seen_filtered.add(key)
                        unique_filtered.append(candidate)
                    filtered = unique_filtered

                if filtered:
                    chosen_diag = {
                        "candidates": len(filtered),
                        "tau": policy.tau,
                        "level": level,
                        "source_phase": source_phase,
                        "retained_candidates": retained_used,
                        **repair_diagnostics,
                        "register_mapping": {
                            "bass": "root/bass",
                            "mid": "3rd/7th/essential",
                            "top": "9th/11th/13th",
                        },
                    }
                    chosen = self._select(filtered, prev_cand, prev_chord, chord, policy, center,
                                           drift_free, chosen_diag)
                    chosen_diag["voicing_template"] = self._template_name(chosen)
                    window_relaxed = lvl_window_relaxed
                    break
            if chosen is not None:
                break


        if chosen is None:
            raise VoicingImpossible(chord, t)

        if window_relaxed:
            self.window_relaxed_count += 1
            self.log.append(("WINDOW_RELAXED", t, chord))

        # spec 02/05 §3.3: the guitar shape-library candidate_source is the
        # only place an extension may be silently dropped; it reports what
        # it dropped via `meta["extensions_dropped"]` and this is the one
        # place that logs/counts it, so the mechanism is centralized
        # regardless of which voicer triggers it.
        for dropped_role in chosen.meta.get("extensions_dropped") or ():
            self.extension_dropped_count += 1
            self.log.append(("EXTENSION_DROPPED", t, chord, dropped_role))
            self.extension_drop_records.append({
                "event_index": t,
                "chord": chord.chord_type(),
                "shape_id": chosen.shape_id,
                "role": dropped_role,
                "source_file": self.ctx.get("source_file"),
            })

        active_degrees = resolve_degrees(chord)
        active_degree_pcs = sorted({
            (root_pc + degree.semitone) % 12 for degree in active_degrees
        })
        dct_exposure = None
        if dct_role is not None:
            dct_exposure = next(
                (
                    exposure_branch(p, chosen.pitches)
                    for p, role in zip(chosen.pitches, chosen.roles)
                    if role == dct_role and exposure_branch(p, chosen.pitches)
                ),
                None,
            )
        chosen_diag.update({
            "absolute_root_pc": root_pc,
            "bass_module_active": bass_active,
            "bass_pc": ((root_pc + chord.bass_interval) % 12)
            if bass_active else None,
            "active_degree_pcs": active_degree_pcs,
            "emitted_chord_pcs": sorted({p % 12 for p in chosen.pitches}),
            "dct_role": dct_role,
            "dct_pc": dct_pc,
            "dct_exposure": dct_exposure,
            "dct_branch_requested": sampled_branch,
            "root_omitted": selected.root_omitted,
            "chord_type": list(chord.chord_type()),
            "shape_id": chosen.shape_id,
            "base_shape_id": chosen.meta.get("base_shape_id"),
            "octave_offset": int(chosen.meta.get("octave_offset", 0)),
            "root_fret": chosen.meta.get("root_fret"),
            "muted_strings": sorted(chosen.meta.get("muted", ())),
            "sounding_strings": list(chosen.meta.get("strings", ())),
            "retained_root_string": chosen.meta.get("retained_root_string"),
            "candidate_source": chosen.meta.get("candidate_source"),
            "generation_phase": chosen.meta.get("generation_phase"),
            "dct_repair": bool(chosen.meta.get("dct_repair", False)),
            "forced_dct_copy": bool(chosen.meta.get("forced_dct_copy", False)),
            "root_omission": selected.flags.get(
                "root_omission_status",
                "omitted" if selected.root_omitted else "retained",
            ),
            "root_omission_gate_failure": selected.flags.get(
                "root_omission_gate_failure"
            ),
            "omitted_roles": list(chosen.meta.get("roles_dropped") or ()),
            "extensions_dropped": list(chosen.meta.get("extensions_dropped") or ()),
        })
        if policy.instrument == "guitar" and prev_cand is not None:
            from .guitar.model import shape_distance
            chosen_diag["shape_distance"] = shape_distance(
                prev_cand.meta.get("root_fret", 0),
                prev_cand.shape_id or "",
                frozenset(prev_cand.meta.get("muted", ())),
                chosen.meta.get("root_fret", 0),
                chosen.shape_id or "",
                frozenset(chosen.meta.get("muted", ())),
            )

        centroid = centroid_of(chosen.pitches)
        vld = vl_distance(prev_midi or [], chosen.pitches, policy.unmatched_penalty) if prev_midi is not None else 0.0

        chord_type = chord.chord_type()
        sig = shape_signature(policy.voicer_id, chosen.pitches, self.signature_bands, sampled_branch,
                               extra=chosen.meta.get("signature_extra", ()))
        self.diversity.record(chord_type, sig)

        # Optional per-voicer "chord committed" side-effect hook (e.g.
        # pop-synth's spec 03 §8 spread-distribution self-correction,
        # which needs to react to the *realized* choice, not just what was
        # requested pre-selection). Stored in `extra` rather than as a
        # first-class VoicerPolicy field so voicers that don't need it are
        # completely unaffected.
        on_commit = policy.extra.get("on_commit")
        if on_commit is not None:
            on_commit(chosen, self.ctx)

        voiced = VoicedChord(
            index=t, voicer=policy.voicer_id, midi=list(chosen.pitches), roles=list(chosen.roles),
            dct_pitch=(next((p for p, r in zip(chosen.pitches, chosen.roles) if r == dct_role), None)
                       if dct_role else None),
            hands=chosen.hands, shape_id=chosen.shape_id, vl_distance=vld, centroid=centroid,
            diagnostics=chosen_diag,
        )
        return voiced, chosen

    def _candidate_sound_set_ok(self, candidate, chord: ChordEvent,
                                selected: SelectedTones, required_degrees: list,
                                root_pc: int, bass_active: bool,
                                allow_dropped: bool) -> bool:
        """Reject candidates that cannot be explained by the label.

        Candidate generators are allowed to add duplicate active roles, but
        they may not introduce a role or pitch class that the event does not
        request.  Extension omissions are accepted only when the guitar
        omission ladder explicitly recorded them; the DCT and root-omission
        contract remain hard invariants.
        """
        pitches = list(candidate.pitches)
        roles = list(candidate.roles)
        if len(pitches) != len(roles) or pitches != sorted(pitches):
            return False
        if len(pitches) != len(set(pitches)):
            return False

        active_degrees = resolve_degrees(chord)
        degree_by_role = {degree.role: degree for degree in active_degrees}
        active_pcs = {
            (root_pc + degree.semitone) % 12 for degree in active_degrees
        }
        emitted_roles = set(roles)
        for pitch, role in zip(pitches, roles):
            degree = degree_by_role.get(role)
            if degree is None:
                return False
            expected_pc = (root_pc + degree.semitone) % 12
            if pitch % 12 != expected_pc or pitch % 12 not in active_pcs:
                return False

        dropped = set(candidate.meta.get("extensions_dropped") or ())
        extension_roles = {
            role for role in ("7th", "9th", "11th", "13th")
            if role in degree_by_role
        }
        if not dropped <= extension_roles:
            return False
        if dropped and not allow_dropped:
            return False
        if dropped & emitted_roles:
            return False
        dct_role = self.ctx.get("_current_dct_role")
        if dct_role in dropped or (dct_role is not None and dct_role not in emitted_roles):
            return False

        required_roles = {degree.role for degree in required_degrees}
        missing_required = required_roles - emitted_roles
        if missing_required:
            if not missing_required <= dropped:
                return False

        if selected.root_omitted and "root" in emitted_roles:
            return False
        if bass_active and selected.root_omitted:
            bass_pc = (root_pc + chord.bass_interval) % 12
            if bass_pc != root_pc:
                return False

        return True

    # ------------------------------------------------------------------
    def _cluster_ok(self, candidate, min_gap: int, cluster_cap: int | None = None) -> bool:
        from .spacing import semitone_cluster_ok
        exempt = candidate.meta.get("cluster_exempt_pairs")
        if not exempt:
            if not semitone_cluster_ok(candidate.pitches, min_gap):
                return False
        else:
            # voicer-declared exemptions (jazz synth §4.1): re-check with
            # the exempted pair(s) removed from consideration, at the
            # relaxed gap.
            relaxed_gap = candidate.meta.get("cluster_exempt_gap", min_gap)
            s = sorted(candidate.pitches)
            for i in range(len(s)):
                for j in range(i + 1, len(s)):
                    pc_gap = (s[j] - s[i]) % 12
                    if pc_gap in (1, 11):
                        gap = s[j] - s[i]
                        pair_exempt = (s[i], s[j]) in exempt or (s[j], s[i]) in exempt
                        limit = relaxed_gap if pair_exempt else min_gap
                        if gap < limit:
                            return False
        if cluster_cap is not None:
            pitches = sorted(candidate.pitches)
            for start, low in enumerate(pitches):
                count = sum(high - low <= 12 for high in pitches[start:])
                if count > cluster_cap:
                    return False
        return True

    @staticmethod
    def _template_name(candidate) -> str:
        explicit = candidate.meta.get("voicing_template")
        if explicit:
            return explicit
        roles = set(candidate.roles)
        if "root" not in roles and {"3rd", "7th"} <= roles:
            return "rootless"
        if "7th" in roles and len(roles) <= 4:
            return "shell"
        if any(role in roles for role in ("9th", "11th", "13th")):
            return "triad_plus_extension"
        return "drop2_or_closed"

    def _dct_filter(self, candidate, dct_role, dct_pc, secondary_roles, sampled_branch,
                     relax_any_branch: bool) -> bool:
        if dct_role is None:
            return True
        dct_pitches = [p for p, r in zip(candidate.pitches, candidate.roles) if r == dct_role]
        if not dct_pitches:
            return False  # DCT dropped -- never allowed
        ok = False
        for p in dct_pitches:
            if relax_any_branch:
                for order in ("octave", "isolated", "top"):
                    fn = {"top": predicate_top, "isolated": predicate_isolated,
                          "octave": predicate_octave}[order]
                    if fn(p, candidate.pitches):
                        ok = True
                        break
            else:
                fn = {"top": predicate_top, "isolated": predicate_isolated,
                      "octave": predicate_octave}[sampled_branch]
                if fn(p, candidate.pitches):
                    ok = True
            if ok:
                break
        if not ok:
            return False
        # §5.3 secondary differentiators: predicate (b) alone
        for role in secondary_roles:
            sec_pitches = [p for p, r in zip(candidate.pitches, candidate.roles) if r == role]
            if not sec_pitches:
                return False
            if not any(secondary_ok(p, candidate.pitches) for p in sec_pitches):
                return False
        return True

    def _select(self, candidates, prev_cand, prev_chord, chord, policy, center, drift_free, diag):
        prev_midi = prev_cand.pitches if prev_cand is not None else None
        costs = []
        for c in candidates:
            cost = 0.0
            if prev_midi is not None:
                vld = vl_distance(prev_midi, c.pitches, policy.unmatched_penalty)
                cost += policy.w_vl * vld
                if prev_chord is not None:
                    cost -= transformation_bonus(prev_chord, chord, policy.plr_bonus)
                    # §5.2 common-tone retention bonus (spec.md §5.2 / spec04 §5.2)
                    common = len(set(prev_midi) & set(c.pitches))
                    cost -= 0.5 * min(common, 3)
            centroid = centroid_of(c.pitches)
            cost += policy.w_drift * drift_penalty(centroid, center, drift_free)
            cost += policy.w_space * spacing_penalty(c.pitches, policy.extra.get("max_close_pairs", 2))
            cost += policy.w_space * policy_spacing_penalty(c, chord, policy)
            # Keep additional octave copies available, but make density
            # progressively more expensive after the first optional voice.
            # This preserves rare two-extra-voice realizations without
            # routinely selecting redundant four-copy chord tones.
            required_voices = int(self.ctx.get("_selected_tone_count", len(c.pitches)))
            extra_voices = max(0, len(c.pitches) - required_voices)
            priced_extras = max(0, extra_voices - 1)
            if priced_extras:
                base = float(policy.extra.get("voice_excess_penalty", 1.0))
                growth = float(policy.extra.get("voice_excess_growth", 2.5))
                cost += base * (growth ** priced_extras)
            chord_type = chord.chord_type()
            sig = shape_signature(policy.voicer_id, c.pitches, self.signature_bands, None,
                                   extra=c.meta.get("signature_extra", ()))
            cost += policy.w_div * self.diversity.penalty(chord_type, sig)
            cost += template_selection_penalty(c, chord, self.ctx, policy)
            if policy.role_penalty is not None:
                cost += policy.w_role * policy.role_penalty(c, prev_cand, self.ctx, policy)
            costs.append(cost)

        chosen_idx = self._softmax_sample(costs, policy.tau)
        diag["rank_chosen"] = sorted(range(len(costs)), key=lambda i: costs[i]).index(chosen_idx)
        return candidates[chosen_idx]

    def _softmax_sample(self, costs: list, tau: float) -> int:
        if tau <= 1e-9:
            return min(range(len(costs)), key=lambda i: costs[i])
        m = min(costs)
        weights = [math.exp(-(c - m) / tau) for c in costs]
        total = sum(weights)
        r = self.rng.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                return i
        return len(costs) - 1
