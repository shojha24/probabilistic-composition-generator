"""
voicing/guitar/pop_shapes.py -- spec 02 §3.2 shape library: hand-authored
idiomatic E-/A-/D-string barre and open-position shapes for the common
chord families, backstopped by `derive.py`-generated shapes for every
(triad, seventh) pair and for every extended (9th/11th/13th) family the
hand-authored set doesn't cover.

Hand-authored fret/degree patterns below were verified by hand against
standard guitar chord-chart fingerings and cross-checked against
`TRIAD_THIRD_FIFTH`/`EXT_SEMI` semitone values (see session notes); they
are the *preferred* shapes (idiomatic, realistic, and diverse in string
content/doubling), with `derive_shape()` filling every gap so spec 02
§9.3's completeness requirement can never fail because of a hand-authored
omission. Both sets live in the same `SHAPES_BY_KEY` index and the
softmax/cost function naturally prefers whichever scores best.

All shapes are stored root-relative (root fret 0 for movable/barre
shapes); `frets`/`degrees` tuples are always given low-to-high across all
6 strings (string 6 first), matching `STRING_NUMS`.
"""
from __future__ import annotations

from .model import Shape
from .derive import derive_shape

X = "x"


def _shape(id, root_string, frets, degrees, chord_types, barre=True,
           tags=("hand_authored",)) -> Shape:
    assert len(frets) == 6 and len(degrees) == 6
    return Shape(id=id, root_string=root_string, frets=tuple(frets),
                 degrees=tuple(degrees), chord_types=tuple(chord_types),
                 barre=barre, open_only=False, tags=tags)


# ---------------------------------------------------------------------------
# E-shape family (root_string=6) -- movable barre, root-relative fret 0.
# ---------------------------------------------------------------------------
_E_DEG3 = ("root", "5th", "root", "3rd", "5th", "root")
_E_DEG_SEV = ("root", "5th", "7th", "3rd", "5th", "root")

_E_SHAPES = [
    _shape("E-barre-major", 6, [0, 2, 2, 1, 0, 0], _E_DEG3,
           [("major", "N", "N", "N", "N")]),
    _shape("E-barre-minor", 6, [0, 2, 2, 0, 0, 0], _E_DEG3,
           [("minor", "N", "N", "N", "N")]),
    _shape("E-barre-dom7", 6, [0, 2, 0, 1, 0, 0], _E_DEG_SEV,
           [("major", "b7", "N", "N", "N")]),
    # maj7 hazard (spec 02 §7): high-E root sits 1 semitone above the maj7
    # 7th on the B string in the naive open-shape layout -- muted here to
    # avoid the doubled-root maj7 trap (spec 07 §5.2).
    _shape("E-barre-maj7", 6, [0, 2, 1, 1, 0, X], ("root", "5th", "7th", "3rd", "5th", None),
           [("major", "7", "N", "N", "N")]),
    _shape("E-barre-min7", 6, [0, 2, 0, 0, 0, 0], _E_DEG_SEV,
           [("minor", "b7", "N", "N", "N")]),
    _shape("E-barre-m7b5", 6, [0, 1, 0, 0, X, 0], ("root", "5th", "7th", "3rd", None, "root"),
           [("diminished", "b7", "N", "N", "N")]),
]

# ---------------------------------------------------------------------------
# A-shape family (root_string=5) -- movable barre, string 6 always muted.
# ---------------------------------------------------------------------------
_A_DEG3 = (None, "root", "5th", "root", "3rd", "5th")
_A_DEG_SEV = (None, "root", "5th", "7th", "3rd", "5th")

_A_SHAPES = [
    _shape("A-barre-major", 5, [X, 0, 2, 2, 2, 0], _A_DEG3,
           [("major", "N", "N", "N", "N")]),
    _shape("A-barre-minor", 5, [X, 0, 2, 2, 1, 0], _A_DEG3,
           [("minor", "N", "N", "N", "N")]),
    _shape("A-barre-dom7", 5, [X, 0, 2, 0, 2, 0], _A_DEG_SEV,
           [("major", "b7", "N", "N", "N")]),
    _shape("A-barre-maj7", 5, [X, 0, 2, 1, 2, 0], _A_DEG_SEV,
           [("major", "7", "N", "N", "N")]),
    _shape("A-barre-min7", 5, [X, 0, 2, 0, 1, 0], _A_DEG_SEV,
           [("minor", "b7", "N", "N", "N")]),
    _shape("A-barre-m7b5", 5, [X, 0, 1, 0, 1, X], (None, "root", "5th", "7th", "3rd", None),
           [("diminished", "b7", "N", "N", "N")]),
]

# ---------------------------------------------------------------------------
# D-shape family (root_string=4) -- movable barre, strings 6/5 always muted.
# ---------------------------------------------------------------------------
_D_DEG3 = (None, None, "root", "5th", "root", "3rd")
_D_DEG_SEV = (None, None, "root", "5th", "7th", "3rd")

_D_SHAPES = [
    _shape("D-barre-major", 4, [X, X, 0, 2, 3, 2], _D_DEG3,
           [("major", "N", "N", "N", "N")]),
    _shape("D-barre-minor", 4, [X, X, 0, 2, 3, 1], _D_DEG3,
           [("minor", "N", "N", "N", "N")]),
    _shape("D-barre-dom7", 4, [X, X, 0, 2, 1, 2], _D_DEG_SEV,
           [("major", "b7", "N", "N", "N")]),
    _shape("D-barre-maj7", 4, [X, X, 0, 2, 2, 2], _D_DEG_SEV,
           [("major", "7", "N", "N", "N")]),
    _shape("D-barre-min7", 4, [X, X, 0, 2, 1, 1], _D_DEG_SEV,
           [("minor", "b7", "N", "N", "N")]),
    _shape("D-barre-m7b5", 4, [X, X, 0, 1, 1, 1], _D_DEG_SEV,
           [("diminished", "b7", "N", "N", "N")]),
]

# ---------------------------------------------------------------------------
# True open-position shapes (open_only=True, native_root_pc fixed) for the
# True open-position shapes are a nice-to-have per spec 02 §3.2 ("Open-
# position C, A, G, E, D... native roots only") but hand-deriving them
# reliably from memory proved error-prone (open-C/open-G fret/degree
# mismatches were caught by the smoke test below and removed rather than
# risk a silently wrong shape). The (major, N)/(major, b7) keys these would
# have covered remain served by the E-/A-/D-barre families above; the
# completeness backstop further down covers every (triad, seventh) pair
# programmatically, so no coverage gap results from dropping them. Noting
# this as a scope simplification (idiom/diversity nicety forgone, not a
# correctness or completeness requirement).
_HAND_SHAPES = _E_SHAPES + _A_SHAPES + _D_SHAPES

# ---------------------------------------------------------------------------
# Power chords (triad == "5") and sus4 -- E/A/D roots. (sus2/dim/aug were
# hand-authored here too but had real fret/role bugs caught by the smoke
# test; they're dropped in favour of the `derive_shape()` completeness
# backstop below, which is validated and safe.)
# ---------------------------------------------------------------------------
_POWER_SHAPES = [
    _shape("E-power2", 6, [0, 2, X, X, X, X], ("root", "5th", None, None, None, None), [("5", "N", "N", "N", "N")]),
    _shape("E-power3", 6, [0, 2, 2, X, X, X], ("root", "5th", "root", None, None, None), [("5", "N", "N", "N", "N")]),
    _shape("A-power2", 5, [X, 0, 2, X, X, X], (None, "root", "5th", None, None, None), [("5", "N", "N", "N", "N")]),
    _shape("A-power3", 5, [X, 0, 2, 2, X, X], (None, "root", "5th", "root", None, None), [("5", "N", "N", "N", "N")]),
    _shape("D-power2", 4, [X, X, 0, 2, X, X], (None, None, "root", "5th", None, None), [("5", "N", "N", "N", "N")]),
    _shape("D-power3", 4, [X, X, 0, 2, 3, X], (None, None, "root", "5th", "root", None), [("5", "N", "N", "N", "N")]),
]

_SUS_SHAPES = [
    # sus4: role "3rd" carries semitone 5. E sus4 = E A B.
    _shape("E-sus4", 6, [0, 2, 2, 2, 0, 0], ("root", "5th", "root", "3rd", "5th", "root"),
           [("sus4", "N", "N", "N", "N")]),
    _shape("A-sus4", 5, [X, 0, 2, 2, 3, 0], (None, "root", "5th", "root", "3rd", "5th"),
           [("sus4", "N", "N", "N", "N")]),
]

# Bare-root ("1", e.g. C:1) chords: no 3rd/5th at all, just the root
# pitch class doubled across 3 non-adjacent strings so a shape exists
# with >= min_voices=3 notes -- a single-string root has only 1 pitch and
# always fails the shared engine's min_voices gate.
_ROOT_ONLY_SHAPES = [
    _shape("E-root3", 6, [0, X, 2, X, X, 0], ("root", None, "root", None, None, "root"),
           [("1", "N", "N", "N", "N")]),
    _shape("A-root3", 5, [X, 0, X, 2, X, 5], (None, "root", None, "root", None, "root"),
           [("1", "N", "N", "N", "N")]),
    _shape("D-root3", 4, [X, 5, 0, X, 3, X], (None, "root", "root", None, "root", None),
           [("1", "N", "N", "N", "N")]),
]

_HAND_SHAPES += _POWER_SHAPES + _SUS_SHAPES + _ROOT_ONLY_SHAPES


# ---------------------------------------------------------------------------
# Extended chords (add9/maj9/min9/dom9, 11/#11/13/b13): spec 02 §3.2 requires
# >=3 hand-or-derived shapes for the 9th family and >=2 for 11/13, at E-/A-
# string roots. Hand-deriving these from memory is exactly the "no
# literature source" open problem spec 02 §3.2/§8 flags -- per the
# previously agreed `drop2_derived` approach, these are produced
# programmatically by `derive_shape()` (root-string search + omission
# ladder, see derive.py), tagged `drop2_derived`, rather than guessed by
# hand. Derivation runs once at library-build time (this module import),
# not per-chord.
# ---------------------------------------------------------------------------
_EXTENDED_CHORD_TYPES_9 = [
    ("major", "N", "9", "N", "N"),        # add9
    ("major", "7", "9", "N", "N"),        # maj9
    ("minor", "b7", "9", "N", "N"),       # min9
    ("major", "b7", "9", "N", "N"),       # dom9
]
_EXTENDED_CHORD_TYPES_11_13 = [
    ("major", "b7", "9", "11", "N"),
    ("major", "b7", "9", "#11", "N"),
    ("major", "b7", "9", "N", "13"),
    ("major", "b7", "9", "N", "b13"),
]

_DERIVED_SHAPES = []


def _derive_all(chord_types, root_strings):
    for ct in chord_types:
        for rs in root_strings:
            shape_id = f"derived-{ct}-{rs}"
            res = derive_shape(ct, rs, shape_id)
            if res is not None:
                shape, _dropped = res
                _DERIVED_SHAPES.append(shape)


def _derive_thin_variants(chord_types, root_strings):
    """A dense 9/11/13-role chord's fullest-role shape at a given anchor
    can be the *only* shape offered there even when a deliberately
    rootless/no-5th "thin" voicing (standard jazz/pop-guitar comping
    practice, not an exotic fallback) would sit in a much better register
    for a specific real chord root -- `derive_shape` stops at the first
    violation-free full-role fit and never explores this on its own (see
    session notes: `G:7(9,11,13)`/`Ab:7(9,11,13)`, register-infeasible
    with only the full-role shape available at every anchor). Adding this
    explicit thin variant per anchor gives the omission-ladder
    accumulation in `library.py` a real alternative to fall back to."""
    for ct in chord_types:
        for rs in root_strings:
            shape_id = f"derived-{ct}-{rs}-thin"
            res = derive_shape(ct, rs, shape_id, initial_dropped=("root", "5th"))
            if res is not None:
                shape, _dropped = res
                _DERIVED_SHAPES.append(shape)


# >=3 shapes each for the 9th family (E, A, D roots).
_derive_all(_EXTENDED_CHORD_TYPES_9, (6, 5, 4))
# >=2 shapes each for 11/13 (E, A, D roots) -- D was originally omitted
# ("E, A roots" only), but for these dense 6-7-role chord types the D
# anchor's *moderate* shift (vs. E's small shift landing too low for the
# low-interval-limit check, or A's large shift overflowing max_fret) is
# sometimes the only register-and-fret-feasible option for a given real
# chord root -- see e.g. G:7(9,11,13), which only the D-anchored
# (root_string=4) shape can realize within [0, max_fret] and above the
# low-interval-limit at once. Keeping only 2 anchors was a real coverage
# gap, not a deliberate restriction.
_derive_all(_EXTENDED_CHORD_TYPES_11_13, (6, 5, 4))
_derive_thin_variants(_EXTENDED_CHORD_TYPES_11_13, (6, 5, 4))

# ---------------------------------------------------------------------------
# Completeness backstop: derive a plain shape (root_string 6/5/4) for every
# (triad, seventh) pair not already covered by a hand-authored shape above,
# so spec 02 §9.3's completeness test can never fail on a hand-authored gap.
# ---------------------------------------------------------------------------
_ALL_TRIADS = ("major", "minor", "diminished", "augmented", "sus4", "sus2", "5", "1")
_ALL_SEVENTHS = ("N", "7", "b7", "bb7")

_hand_covered_keys = {(s.chord_types[0][0], s.chord_types[0][1]) for s in _HAND_SHAPES if s.chord_types}

for _triad in _ALL_TRIADS:
    for _seventh in _ALL_SEVENTHS:
        if (_triad, _seventh) in _hand_covered_keys:
            continue
        _ct = (_triad, _seventh, "N", "N", "N")
        for _rs in (6, 5, 4):
            _res = derive_shape(_ct, _rs, f"derived-{_ct}-{_rs}")
            if _res is not None:
                _shp, _ = _res
                _DERIVED_SHAPES.append(_shp)

ALL_SHAPES = _HAND_SHAPES + _DERIVED_SHAPES


def _key_for(shape: Shape):
    """Index key: (triad, seventh) -- matches `library.py`'s coarse
    whitelist pre-filter (the real compatibility check is semitone-based,
    see `library.py`'s docstring)."""
    if not shape.chord_types:
        return None
    return (shape.chord_types[0][0], shape.chord_types[0][1])


SHAPES_BY_KEY: dict = {}
for _s in ALL_SHAPES:
    _k = _key_for(_s)
    if _k is None:
        continue
    SHAPES_BY_KEY.setdefault(_k, []).append(_s)

SHAPES_BY_KEY = {k: tuple(v) for k, v in SHAPES_BY_KEY.items()}

# ---------------------------------------------------------------------------
# Full-vocabulary completeness closure (spec 02 §9.3: "for all corpus chord
# events... at least one shape exists" is a hard, CI-blocking requirement,
# not the more lenient <2% drop-rate gate of §9.4). Rather than couple this
# module to specific corpus files at import time, this scans every
# (triad, seventh, ninth, eleventh, thirteenth) combination the declared
# token vocabulary (`TRIAD_THIRD_FIFTH`/`EXT_SEMI`) can produce -- a
# strict superset of anything any corpus can contain -- and, for any
# combination the omission ladder genuinely cannot reach from the shapes
# above (typically because the extension is the DCT and the ladder
# correctly refuses to drop it), derives a dedicated shape for that exact
# combination and folds it into the same `(triad, seventh)` bucket.
# ---------------------------------------------------------------------------
from ..types import ChordEvent as _ChordEvent, resolve_degrees as _resolve_degrees, \
    TRIAD_THIRD_FIFTH as _TRIAD_THIRD_FIFTH, EXT_SEMI as _EXT_SEMI  # noqa: E402
from ..dct import compute_dct as _compute_dct  # noqa: E402
from .library import find_matching_shapes as _find_matching_shapes  # noqa: E402

_ALL_SEVENTH_TOKENS = ("N",) + tuple(_EXT_SEMI["seventh"].keys())
_ALL_NINTH_TOKENS = ("N",) + tuple(_EXT_SEMI["ninth"].keys())
_ALL_ELEVENTH_TOKENS = ("N",) + tuple(_EXT_SEMI["eleventh"].keys())
_ALL_THIRTEENTH_TOKENS = ("N",) + tuple(_EXT_SEMI["thirteenth"].keys())


def _close_completeness_gaps():
    for triad in _TRIAD_THIRD_FIFTH:
        for seventh in _ALL_SEVENTH_TOKENS:
            for ninth in _ALL_NINTH_TOKENS:
                for eleventh in _ALL_ELEVENTH_TOKENS:
                    for thirteenth in _ALL_THIRTEENTH_TOKENS:
                        ct = (triad, seventh, ninth, eleventh, thirteenth)
                        dummy = _ChordEvent(root_interval=0, triad=triad, bass_interval=0,
                                             seventh=seventh, ninth=ninth, eleventh=eleventh,
                                             thirteenth=thirteenth)
                        degs = _resolve_degrees(dummy)
                        required = frozenset(d.role for d in degs)
                        role_semi = {d.role: d.semitone for d in degs}
                        dct_role, _ = _compute_dct(dummy, degs)
                        key = (triad, seventh)
                        matches = _find_matching_shapes(
                            key, SHAPES_BY_KEY, required, role_semi, dct_role, True)
                        if matches:
                            continue
                        # Genuine gap: derive a shape for this exact combo.
                        for rs in (6, 5, 4):
                            res = derive_shape(ct, rs, f"gapfill-{ct}-{rs}",
                                                bass_module_active=True)
                            if res is not None:
                                shape, _ = res
                                bucket = list(SHAPES_BY_KEY.get(key, ()))
                                bucket.append(shape)
                                SHAPES_BY_KEY[key] = tuple(bucket)
                                ALL_SHAPES.append(shape)
                                _DERIVED_SHAPES.append(shape)


_close_completeness_gaps()
