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
from .spacing import spacing_hard_ok, spacing_penalty, low_interval_limit_ok
from .candidates import dct_expose_repair, post_filter_repair
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


def _role_pcs(chord: ChordEvent, degrees: list) -> list:
    return [(d.role, (chord.root_interval + d.semitone) % 12) for d in degrees]


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

    # ------------------------------------------------------------------
    def run(self, song: Song) -> list[VoicedChord]:
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

    # ------------------------------------------------------------------
    def step(self, t: int, chord: ChordEvent, prev_cand, prev_chord, base_center: int):
        self.total_count += 1
        self.ctx["_current_chord"] = chord
        self.ctx["_current_t"] = t
        overrides, center, section, anchor_shift = self._effective_policy_and_center(base_center)
        policy = self.policy
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

        selected = select_tones(chord, eff_policy, self.ctx, self.rng,
                                 root_omission_gate=self.root_omission_gate,
                                 branch_b_requires_octave_dct=self.branch_b_requires_octave_dct,
                                 extra_doubling_targets=tuple(policy.extra.get(
                                     "doubling_targets", ("root", "5th"))),
                                 gen_dir=self.gen_dir)

        dct_role, secondary_roles = compute_dct(chord, selected.degrees, gen_dir=self.gen_dir)
        dct_pc = dct_pitch_class(chord, selected.degrees, dct_role) if dct_role else None
        if dct_pc is not None:
            dct_pc = (chord.root_interval + dct_pc) % 12
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

        window_lo, window_hi = policy.window_lo, policy.window_hi
        max_doublings = policy.max_doublings
        window_relaxed = False

        chosen = None
        chosen_diag = {}
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
        for allow_dropped in (False, True):
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

                # The octave-exposure branch (§5.2c) requires the DCT itself to
                # be an available doubling target -- otherwise predicate (c)
                # can never be satisfied, since the generic root/5th doubling
                # targets never touch the DCT's pitch class.
                lvl_doubling_roles = list(selected.doubling_roles)
                if sampled_branch == "octave" and dct_role and dct_role not in lvl_doubling_roles:
                    lvl_doubling_roles.append(dct_role)

                params = CandidateGenParams(
                    chord=chord, degrees=lvl_degrees, doubling_roles=lvl_doubling_roles,
                    max_doublings=lvl_max_doublings, window_lo=lvl_window_lo, window_hi=lvl_window_hi,
                    anchor_center=center, prev_midi=prev_midi, rng=self.rng, ctx=self.ctx,
                    policy=policy, dct_role=dct_role, section=section, max_voices=lvl_max_voices,
                    anchor_shift=anchor_shift,
                )
                raw = policy.candidate_source(params)
                if not allow_dropped:
                    raw = [c for c in raw if not c.meta.get("extensions_dropped")]

                allow_soft_spacing_prune = (level < 1)
                eff_drift_tol = policy.drift_tol * (1.5 if level >= 2 else 1.0)

                filtered = []
                spacing_survivors = []
                dct_clean_survivors = []
                for c in raw:
                    if len(c.pitches) < policy.min_voices or len(c.pitches) > lvl_max_voices:
                        continue
                    if not window_ok(c.pitches, lvl_window_lo, lvl_window_hi):
                        continue
                    if not low_interval_limit_ok(c.pitches):
                        continue
                    min_gap = policy.extra.get("cluster_min_gap", 13)
                    if not self._cluster_ok(c, min_gap):
                        continue
                    centroid = centroid_of(c.pitches)
                    if not drift_ok(centroid, center, eff_drift_tol):
                        continue
                    if allow_soft_spacing_prune and spacing_penalty(c.pitches, policy.extra.get(
                            "max_close_pairs", 2)) > 3.0:
                        continue
                    spacing_survivors.append(c)
                    if not self._dct_filter(c, dct_role, dct_pc, secondary_roles, sampled_branch,
                                             relax_any_branch=(level >= 7)):
                        continue
                    dct_clean_survivors.append(c)
                    if policy.post_filter is not None:
                        ok = policy.post_filter(c, prev_cand, self.ctx, policy)
                        if not ok:
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
                    min_gap = policy.extra.get("cluster_min_gap", 13)
                    for c in spacing_survivors[:8]:
                        found = None
                        for repaired in dct_expose_repair(c, dct_role, secondary_roles,
                                                           sampled_branch, lvl_window_lo,
                                                           lvl_window_hi, center):
                            wok = window_ok(repaired.pitches, lvl_window_lo, lvl_window_hi)
                            lil = low_interval_limit_ok(repaired.pitches)
                            clu = self._cluster_ok(repaired, min_gap)
                            drf = drift_ok(centroid_of(repaired.pitches), center, eff_drift_tol)
                            if not (wok and lil and clu and drf):
                                continue
                            if not self._dct_filter(repaired, dct_role, dct_pc, secondary_roles,
                                                     sampled_branch, relax_any_branch=(level >= 7)):
                                continue
                            if policy.post_filter is not None and not policy.post_filter(
                                    repaired, prev_cand, self.ctx, policy):
                                continue
                            found = repaired
                            break
                        if found is not None:
                            filtered.append(found)
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
                    min_gap = policy.extra.get("cluster_min_gap", 13)
                    for c in dct_clean_survivors[:8]:
                        repaired = post_filter_repair(c, lvl_window_lo, lvl_window_hi, center,
                                                        min_gap, policy.post_filter, prev_cand,
                                                        self.ctx, policy)
                        if repaired is None:
                            continue
                        if not (window_ok(repaired.pitches, lvl_window_lo, lvl_window_hi) and
                                 low_interval_limit_ok(repaired.pitches) and
                                 self._cluster_ok(repaired, min_gap) and
                                 drift_ok(centroid_of(repaired.pitches), center, eff_drift_tol)):
                            continue
                        if not self._dct_filter(repaired, dct_role, dct_pc, secondary_roles,
                                                 sampled_branch, relax_any_branch=(level >= 7)):
                            continue
                        filtered.append(repaired)
                        break

                if filtered:
                    chosen_diag = {"candidates": len(filtered), "tau": policy.tau, "level": level}
                    chosen = self._select(filtered, prev_cand, prev_chord, chord, policy, center,
                                           drift_free, chosen_diag)
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

    # ------------------------------------------------------------------
    def _cluster_ok(self, candidate, min_gap: int) -> bool:
        from .spacing import semitone_cluster_ok
        exempt = candidate.meta.get("cluster_exempt_pairs")
        if not exempt:
            return semitone_cluster_ok(candidate.pitches, min_gap)
        # voicer-declared exemptions (jazz synth §4.1): re-check with the
        # exempted pair(s) removed from consideration, at the relaxed gap.
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
        return True

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
            chord_type = chord.chord_type()
            sig = shape_signature(policy.voicer_id, c.pitches, self.signature_bands, None,
                                   extra=c.meta.get("signature_extra", ()))
            cost += policy.w_div * self.diversity.penalty(chord_type, sig)
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
