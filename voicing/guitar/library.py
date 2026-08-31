"""
voicing/guitar/library.py -- shape lookup, the §3.3 omission ladder, and the
`shape_library` candidate_source factory, shared by pop-guitar (spec 02) and
jazz-guitar (spec 05).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from ..candidates import Candidate
from ..types import DEGREE_RANK, EXT_SLOTS, SLOT_TO_ROLE, resolve_degrees
from .derive import derive_shape
from .model import Shape, realize, shape_distance

# spec 02 §3.3 / inherited by spec 05: most-omittable first.
OMISSION_PRIORITY = ("5th", "root", "9th", "11th", "3rd", "13th", "7th")


@lru_cache(maxsize=None)
def _role_semitones(shape: Shape) -> dict:
    return shape.role_semitones()


def _matches(shape: Shape, required_roles: frozenset, role_semi: dict) -> bool:
    rs = _role_semitones(shape)
    for role in required_roles:
        if role not in rs or rs[role] != role_semi[role]:
            return False
    return True


def validate_realization(chord, realized: dict, root_pc: int,
                         allowed_dropped=()) -> tuple[bool, tuple[str, ...]]:
    """Validate a realized guitar shape against the exact active sound set.

    The coarse ``(triad, seventh)`` index is deliberately retained for
    performance, but it is not an admission check: an indexed shape can
    still carry an inactive extension or a stale hand-authored role.  This
    validator runs after fret realization, before MIDI deduplication, so
    duplicate pitches and role/pitch mismatches cannot be hidden.

    Returns ``(is_valid, missing_extension_roles)``.  Missing roles are only
    legal when explicitly supplied by the current omission ladder (or by a
    derived shape's recorded omission metadata); the DCT exclusion is
    enforced by the caller because it depends on the current corpus
    vocabulary.
    """
    if not 0 <= root_pc < 12:
        raise ValueError(f"root_pc must be in 0..11, got {root_pc!r}")
    pitches = list(realized.get("pitches", ()))
    roles = list(realized.get("roles", ()))
    if len(pitches) != len(roles) or len(pitches) != len(set(pitches)):
        return False, ()

    degrees = resolve_degrees(chord)
    degree_by_role = {degree.role: degree for degree in degrees}
    active_pcs = {
        (root_pc + degree.semitone) % 12
        for degree in degrees
    }
    for pitch, role in zip(pitches, roles):
        degree = degree_by_role.get(role)
        if degree is None:
            return False, ()
        if pitch % 12 not in active_pcs:
            return False, ()
        if pitch % 12 != (root_pc + degree.semitone) % 12:
            return False, ()

    emitted_roles = set(roles)
    allowed = set(allowed_dropped)
    missing_roles = set(degree_by_role) - emitted_roles
    if missing_roles - allowed:
        return False, ()
    extension_roles = {
        SLOT_TO_ROLE[slot] for slot in EXT_SLOTS
        if getattr(chord, slot) != "N"
    }
    missing_extensions = tuple(
        role for role in sorted(extension_roles, key=DEGREE_RANK.get)
        if role in missing_roles
    )
    return True, missing_extensions


def find_matching_shapes(chord_triad_seventh_key, shapes_by_key: dict, required_roles: frozenset,
                          role_semi: dict, dct_role: Optional[str],
                          bass_module_active: bool) -> tuple:
    """Search `shapes_by_key[chord_triad_seventh_key]` (a coarse whitelist
    pre-filter -- see module docstring below) for shapes whose *semitone*
    content covers every currently-required role, applying the §3.3
    omission ladder one role at a time (most-omittable first) when
    needed, skipping "root" unless `bass_module_active`, and refusing to
    go past a ladder step that would need to drop the DCT role -- the one
    hard rule with no exceptions.

    Unlike a naive "stop at the first level with any match" search, this
    accumulates matches from *every* ladder level up to (but not
    including) one that would drop the DCT, each tagged with the specific
    roles dropped to reach it. This matters because a chord's degree count
    can force omission for genuinely *register* reasons even when a
    (semitone-wise) full-role shape technically exists: e.g. a 6-role
    dom9(11) chord only has one possible full-role fretting (root_string=6,
    the only layout with 6 available strings), and that single fretting
    may be too low/dense to pass spec 07 §7.3's low-interval-limit --
    without also offering the omission-ladder alternatives as candidates,
    the engine would have no fallback and the chord would be unvoiceable.
    Downstream register/spacing filters (already applied by the shared
    engine) are what actually decide which of these gets chosen; this
    function's job is only to make sure they have real options to choose
    from, never to guess which will "look better" ahead of time.

    Returns `list[(Shape, dropped: tuple[str, ...])]`, ordered from least
    to most omission (an empty list if nothing was found even after every
    admissible drop -- a library gap, caught by spec 02 §9.3's
    completeness test)."""
    candidates = shapes_by_key.get(chord_triad_seventh_key, ())
    out: list = []
    dropped: list = []
    idx = 0
    while True:
        cur_required = frozenset(r for r in required_roles if r not in dropped)
        matches = [s for s in candidates if _matches(s, cur_required, role_semi)]
        if matches:
            out.extend((s, tuple(dropped)) for s in matches)
        advanced = False
        while idx < len(OMISSION_PRIORITY):
            role = OMISSION_PRIORITY[idx]
            idx += 1
            if role not in cur_required:
                continue
            if role == "root" and not bass_module_active:
                continue
            if role == dct_role:
                return out  # never drop the DCT -- stop, keep what we have
            dropped.append(role)
            advanced = True
            break
        if not advanced:
            return out  # ladder exhausted


def find_exact_shape_matches(chord, shapes_by_key: dict, root_pc: int,
                             max_fret: int, dct_role: Optional[str] = None,
                             require_extensions: bool = False) -> tuple:
    """Return coarse-index matches that also pass exact post-realization
    sound-set validation for one concrete root pitch class.  When
    ``require_extensions`` is true, only candidates retaining every active
    extension are returned; this is used by the shape-completeness audit
    before allowing omission-ladder fallbacks to stand in for coverage."""
    degrees = resolve_degrees(chord)
    required_roles = frozenset(d.role for d in degrees)
    role_semi = {d.role: d.semitone % 12 for d in degrees}
    if dct_role is None:
        from ..dct import compute_dct
        dct_role, _ = compute_dct(chord, degrees)
    coarse = find_matching_shapes(
        (chord.triad, chord.seventh), shapes_by_key, required_roles,
        role_semi, dct_role, True,
    )
    out = []
    for shape, dropped in coarse:
        realized = realize(shape, root_pc, max_fret=max_fret)
        if realized is None:
            continue
        declared_omissions = (
            getattr(shape, "omitted_roles", ())
            if chord.chord_type() in shape.chord_types else ()
        )
        allowed_dropped = tuple(dict.fromkeys((*dropped, *declared_omissions)))
        valid, missing_extensions = validate_realization(
            chord, realized, root_pc, allowed_dropped
        )
        if valid and dct_role not in missing_extensions \
                and (not require_extensions or not missing_extensions):
            out.append((shape, dropped))
    return tuple(out)


@lru_cache(maxsize=None)
def _derive_rootless_shapes(chord_type: tuple) -> tuple[Shape, ...]:
    """Build rootless movable shapes for jazz seventh chords when the
    coarse shared library has no exact rootless realization."""
    out = []
    for root_string in (6, 5, 4):
        result = derive_shape(
            chord_type, root_string,
            f"rootless-{chord_type}-{root_string}",
            bass_module_active=True,
            initial_dropped=("root",),
            prefer_low_interval_safe=True,
        )
        if result is not None:
            out.append(result[0])
    return tuple(out)


class ExtensionDropTracker:
    """Dataset-wide EXTENSION_DROPPED accounting (spec 02 §3.3 / §9.4).
    Logging + the actual <2% rate check live here so pop-guitar and
    jazz-guitar share one implementation; the Engine also keeps its own
    per-run `extension_dropped_count`/`log` (see `engine.py`), which this
    complements with a by-chord-type breakdown for the validation report."""

    def __init__(self):
        self.total = 0
        self.dropped = 0
        self.by_type: dict = {}

    def record(self, chord_type: tuple, dropped_roles) -> None:
        self.total += 1
        bucket = self.by_type.setdefault(chord_type, [0, 0])
        bucket[1] += 1
        if dropped_roles:
            self.dropped += 1
            bucket[0] += 1

    def record_from_log(self, log: list) -> None:
        """Populate from an `Engine.log` (spec 07's own per-run
        EXTENSION_DROPPED record, see `engine.py::step`) after a run --
        this is the *authoritative* source since it reflects only the
        actually-chosen voicing per chord, not every candidate considered.
        `log` entries are `("EXTENSION_DROPPED", t, chord, dropped_role)`;
        multiple entries can share the same `t` (one per dropped role), so
        chords are counted once each via a seen-`t` set, with any dropped
        role at all counting as one "dropped" chord."""
        seen_t = set()
        by_t: dict = {}
        for entry in log:
            if entry[0] != "EXTENSION_DROPPED":
                continue
            _, t, chord, _role = entry
            by_t.setdefault(t, chord)
            seen_t.add(t)
        for t in seen_t:
            self.record(by_t[t].chord_type(), [True])

    def rate(self) -> float:
        return self.dropped / self.total if self.total else 0.0


def make_shape_library_source(shapes: list, shapes_by_key: dict, max_fret: int,
                               max_fret_span: int):
    """Build a `candidate_source` closure over a concrete shape library.
    `shapes_by_key` maps a coarse whitelist key -> tuple[Shape, ...]; the
    key function is the caller's choice (pop-guitar and jazz-guitar key
    differently, see their `shapes.py`), but must be derivable from
    `(chord.triad, chord.seventh)` alone so the omission ladder (which only
    ever removes non-token degrees or zeroes an extension slot) doesn't
    need to recompute the key at every ladder step.

    EXTENSION_DROPPED accounting is *not* done here -- `find_matching_shapes`
    now returns candidates from every admissible ladder level (see its
    docstring), so "was anything dropped" is a per-*candidate*, not
    per-chord, question until the engine actually selects one. The
    authoritative count is the Engine's own per-run log (`step()` already
    consumes `Candidate.meta["extensions_dropped"]` for the chosen
    candidate); `ExtensionDropTracker.record_from_log` builds the by-type
    breakdown from that after a run."""

    def candidate_source(params) -> list:
        chord = params.chord
        key = (chord.triad, chord.seventh)
        required_roles = frozenset(d.role for d in params.degrees)
        role_semi = {d.role: d.semitone % 12 for d in params.degrees}
        bass_active = bool(params.ctx.get("bass_module_active"))
        matches = list(find_matching_shapes(
            key, shapes_by_key, required_roles, role_semi,
            params.dct_role, bass_active,
        ))
        if bass_active and "root" not in required_roles:
            known_ids = {shape.id for shape, _ in matches}
            for shape in _derive_rootless_shapes(chord.chord_type()):
                if shape.id not in known_ids:
                    matches.append((shape, tuple(shape.omitted_roles)))
        if not matches:
            return []
        root_pc = params.root_pc
        full_degrees = resolve_degrees(chord)
        selection_dropped = tuple(
            degree.role for degree in full_degrees
            if degree.role not in required_roles
        )
        out = []
        for shape, dropped in matches:
            fret_span = shape.span()
            if fret_span > max_fret_span + 1:
                continue
            realized = realize(shape, root_pc, max_fret=max_fret)
            if realized is None:
                continue
            declared_omissions = (
                getattr(shape, "omitted_roles", ())
                if chord.chord_type() in shape.chord_types else ()
            )
            allowed_dropped = tuple(dict.fromkeys(
                (*dropped, *declared_omissions, *selection_dropped)
            ))
            valid, missing_extensions = validate_realization(
                chord, realized, root_pc, allowed_dropped
            )
            if not valid or (params.dct_role in missing_extensions):
                continue
            template = shape.tags[0] if shape.tags else shape.id
            cand = Candidate(
                pitches=realized["pitches"], roles=realized["roles"], shape_id=shape.id,
                meta={
                    "root_fret": realized["root_fret"], "muted": realized["muted"],
                    "strings": realized["strings"], "root_string": shape.root_string,
                    "family": template, "voicing_template": template,
                    "fret_span": fret_span,
                    "extensions_dropped": list(missing_extensions),
                    "roles_dropped": [
                        degree.role for degree in full_degrees
                        if degree.role not in set(realized["roles"])
                    ],
                    "signature_extra": (shape.root_string, template),
                },
            ).dedup_sorted()
            out.append(cand)
        return out

    return candidate_source
