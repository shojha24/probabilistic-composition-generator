"""
voicing/voicers/pop_guitar.py -- spec 02, pop/rock guitar voicer.

`candidate_source` is the shape-library lookup (spec 02 §5), built once at
import time over `voicing/guitar/pop_shapes.py`'s library. `role_penalty`
folds in the discrete shape-distance term (§5) plus a root-doubling bias
(§4: "root doubling is largely decided by the shape, not chosen"). Three
guitar-specific hard hazards (§7) are enforced in `post_filter`.
"""
from __future__ import annotations

from ..policy import VoicerPolicy
from ..candidates import Candidate
from ..engine import CandidateGenParams
from ..guitar.model import shape_distance
from ..guitar.library import make_shape_library_source, ExtensionDropTracker
from ..guitar.pop_shapes import ALL_SHAPES, SHAPES_BY_KEY

MAX_FRET = 12
MAX_FRET_SPAN = 4  # spec 02 §2: "5 allowed with penalty" -- enforced via role_penalty, not a hard cutoff
FRET_DRIFT_FREE = 2   # matches spec 02 §9 item 5's <=3-fret tolerance, kept
                       # slightly tighter than the gate itself since this is
                       # a soft per-candidate term, not the gate's own mean
FRET_DRIFT_WEIGHT = 1.5

# Dataset-wide EXTENSION_DROPPED accounting (spec 02 §3.3/§9.4), shared
# across the whole run/validation report; the Engine's own per-run counter
# (spec 07 hook) complements this with a by-chord-type breakdown.
drop_tracker = ExtensionDropTracker()

_candidate_source_impl = make_shape_library_source(
    ALL_SHAPES, SHAPES_BY_KEY, max_fret=MAX_FRET, max_fret_span=MAX_FRET_SPAN,
)


def candidate_source(p: CandidateGenParams) -> list:
    return _candidate_source_impl(p)


def role_penalty(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> float:
    penalty = 0.0

    # spec 02 §5: discrete shape-distance term, folded into w_role since
    # this *is* the guitar-specific voice-leading analogue.
    if prev is not None and prev.shape_id is not None and candidate.shape_id is not None:
        penalty += 0.5 * shape_distance(
            prev.meta.get("root_fret", 0), prev.shape_id, frozenset(prev.meta.get("muted", ())),
            candidate.meta.get("root_fret", 0), candidate.shape_id, frozenset(candidate.meta.get("muted", ())),
        )

    # spec 02 §9 item 5 ("no fretboard drift"): shape_distance above is a
    # purely *local* (chord-to-chord) pull, which bounds single-step jumps
    # but not a slow monotonic walk across many small steps -- the same
    # failure mode spec 07 §13.7's whole-song drift test targets for
    # pitch, restated here in fret terms since two shapes can have nearly
    # identical pitch centroids while sitting at very different neck
    # positions (a chord voiced high on low strings vs low on high
    # strings). We establish a per-song "home" root_fret from the first
    # chord's actually-chosen shape (the first candidate seen with `prev`
    # not None already reflects the chosen first-chord shape) and softly
    # pull every later candidate back toward it, exactly mirroring
    # `anchor.drift_penalty`'s fixed-target/free-zone/quadratic-beyond
    # shape but on the fret axis instead of the pitch axis.
    if prev is not None and "_guitar_home_root_fret" not in ctx:
        ctx["_guitar_home_root_fret"] = prev.meta.get("root_fret", 0)
    home_fret = ctx.get("_guitar_home_root_fret")
    if home_fret is not None:
        root_fret = candidate.meta.get("root_fret", 0)
        excursion = abs(root_fret - home_fret) - FRET_DRIFT_FREE
        if excursion > 0:
            penalty += FRET_DRIFT_WEIGHT * excursion ** 2

    # spec 02 §4: root doubling is a shape-selection bias, not a chosen
    # voice -- favour root-containing (and root-doubling) shapes when
    # root_double_p is high, and lightly favour rootless shapes when
    # root_omission_p would otherwise apply.
    root_count = candidate.roles.count("root")
    if root_count == 0:
        penalty += (1.0 - policy.root_omission_p) * 1.5
    elif root_count == 1:
        penalty += (1.0 - policy.root_double_p) * 0.6
    # root_count >= 2: no penalty, this is the shape naturally "doubling".

    # span > 4 is allowed but penalized (spec 02 §2: "5 allowed with penalty").
    fret_span = candidate.meta.get("fret_span", 0)
    if fret_span > MAX_FRET_SPAN:
        penalty += 0.8 * (fret_span - MAX_FRET_SPAN)

    # spec 02 §3.3: extension omission is "the one place... an extension
    # may be dropped", permitted "only because the alternative is
    # emitting an unplayable shape" -- it must never be an equally-scored
    # alternative to a fuller shape that also satisfies every hard filter.
    # `library.py`'s omission ladder now returns candidates from *every*
    # admissible drop level (not just the first that matches) so register
    # -infeasible full-role shapes have a fallback -- but that means a
    # thinned candidate is offered even on ordinary chords where the full
    # shape was already fine, and without a cost penalty here the
    # softmax selection would sometimes pick one anyway for unrelated
    # reasons (VL/parsimony), which would silently blow the <2%
    # dataset-wide EXTENSION_DROPPED rate gate (spec 02 §9.4). This
    # penalty must dominate every other role_penalty term so a drop is
    # only ever actually chosen when it is the *last* candidate standing
    # after the engine's hard filters (register/window/cluster/drift).
    n_dropped = len(candidate.meta.get("extensions_dropped") or ())
    if n_dropped:
        penalty += 60.0 * n_dropped

    return penalty


def post_filter(candidate: Candidate, prev, ctx: dict, policy: VoicerPolicy) -> bool:
    chord = ctx.get("_current_chord")
    dct_role = ctx.get("_current_dct_role")
    dct_pc = ctx.get("_current_dct_pc")

    # Hazard 1 (spec 02 §7): the doubled-root maj7 trap. A maj7's 7th sits
    # a semitone below a root; a root copy 1-2 semitones above a sounded
    # DCT 7th buries it (spec 07 §5.2's exposure rule, but generalized here
    # since a barre shape can duplicate the root on any string).
    if chord is not None and dct_role == "7th" and dct_pc is not None and \
            chord.triad == "major" and chord.seventh in ("7", "b7"):
        dct_pitches = [p for p, r in zip(candidate.pitches, candidate.roles)
                       if r == "7th" and p % 12 == dct_pc]
        root_pitches = [p for p, r in zip(candidate.pitches, candidate.roles) if r == "root"]
        for dp in dct_pitches:
            for rp in root_pitches:
                if 1 <= (rp - dp) <= 2:
                    return False

    # Hazard 2 (spec 02 §7): the dropped-3rd power-chord trap. §3.3 puts
    # the 3rd late in the omission priority precisely because a shape
    # reduced to root/5th/7th reads as a power-chord-plus-note and feeds
    # N19's confusion between "5", maj, and min -- so a 3rd-dropped
    # candidate is rejected outright rather than merely logged.
    if "3rd" in (candidate.meta.get("extensions_dropped") or ()):
        return False

    # Hazard 3 (spec 02 §7): open-string ambiguity. Extended-chord shapes
    # tagged open_only must place the DCT on a fretted string 1 or 2 (an
    # inner open string is quiet and buries the extension).
    family = candidate.meta.get("family")
    if family == "open" and dct_role is not None and dct_role in ("9th", "11th", "13th"):
        strings = candidate.meta.get("strings", [])
        roles = candidate.roles
        dct_strings = [s for s, r in zip(strings, roles) if r == dct_role]
        if dct_strings and not any(s in (1, 2) for s in dct_strings):
            return False

    return True


SECTION_PROFILE = {
    "verse": {"drift_free": 4, "max_voices": 5, "root_double_p": 0.55},
    "prechorus": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.65},
    "chorus": {"drift_free": 6, "max_voices": 6, "root_double_p": 0.75},
    "bridge": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.60},
    "outro": {"drift_free": 5, "max_voices": 6, "root_double_p": 0.65},
}

SIGNATURE_BANDS = {"low": (40, 52), "mid": (53, 64), "high": (65, 76)}

POLICY = VoicerPolicy(
    voicer_id="pop-guitar", genre="pop_rock", instrument="guitar",
    window_lo=40, window_hi=76, drift_free=5, drift_tol=10,
    min_voices=3, max_voices=6, p_omit_fifth=0.35, root_double_p=0.60,
    root_omission_p=0.08, max_doublings=2,
    tau=5.0, w_vl=0.7, w_drift=0.9, w_space=0.3, w_div=1.2, w_role=1.4,
    plr_bonus=0.5, unmatched_penalty=2.0, vl_target_mean=9.5, vl_target_sd=5.0,
    dct_mode_weights={"top": 0.65, "isolated": 0.30, "octave": 0.05},
    candidate_source=candidate_source, role_penalty=role_penalty, post_filter=post_filter,
    section_profile=SECTION_PROFILE,
    extra={"max_fret": MAX_FRET, "max_fret_span": MAX_FRET_SPAN, "drop_tracker": drop_tracker,
           # spec 07 §7.3's generic semitone-cluster rule (min_gap=13,
           # i.e. requiring a root/maj7 pair to be spread over an octave)
           # is unreachable on a physically compact fretted shape: a
           # barre maj7 voicing routinely puts the root and the major 7th
           # (pc-gap 1 mod 12) only 11 real semitones apart on adjacent
           # strings -- this is the idiomatic open/barre Emaj7-type voicing,
           # not a harsh clash (the strings' distinct timbre separates
           # them, unlike a single-hand piano cluster). spacing.py's own
           # docstring anticipates exactly this per-voicer override
           # ("jazz synth's controlled exemption"); guitar needs the same
           # treatment for the same physical reason. True <10-semitone
           # clashes are still rejected.
           "cluster_min_gap": 10, "cluster_cap": 4},
)
