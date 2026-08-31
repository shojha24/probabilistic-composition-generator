"""
Quota-aware target corpus generation for the §08 event targets.

This module owns target-only scheduling, quota accounting, corpus batching, and
the target-generation CLI. The natural single-song generator and its sampling
primitives remain in ``chord_gen.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from chord_gen import (
    DURATION_BEATS,
    EXT_SLOTS,
    GENRE_LABEL_DIRS,
    GenParams,
    GenState,
    ChordGenerator,
    NOTE_TO_PC,
    PC_TO_NOTE,
    VALID_GENRES,
    _DEFAULT_DIST_DIR,
    _attested_successor_distribution,
    _attested_transition_counts,
    _node_child_distribution,
    _trie_child_prefix,
    _trie_reaches_density,
    reconstruct_harte,
    select_extension_trie,
    stage1_distribution,
    stage2_distribution,
    walk_extension_trie,
    weighted_choice,
)

VOICER_FAMILIES = ("piano", "guitar", "synth")
GUITAR_MAX_ACTIVE_EXTENSIONS = 2

# §08 corpus and minimum-coverage targets. Natural events count toward these
# minimums; only the remaining deficit is eligible for targeted sampling.
TARGET_TOTAL_EVENTS = 250_000
TARGET_EVENTS_BY_GENRE: Dict[str, int] = {
    "jazz": 125_000,
    "pop_rock": 125_000,
}
TARGET_LABEL_DIRS: Dict[str, str] = {
    genre: f"target-{directory}"
    for genre, directory in GENRE_LABEL_DIRS.items()
}
RARE_TRIAD_TARGETS: Dict[str, Dict[str, int]] = {
    "jazz": {
        "sus2": 1_000,
        "augmented": 1_500,
        "diminished": 5_000,
        "1": 0,
        "5": 1_000,
        "sus4": 3_000,
    },
    "pop_rock": {
        "sus2": 1_000,
        "augmented": 750,
        "diminished": 1_500,
        "1": 2_000,
        "5": 2_500,
        "sus4": 3_000,
    },
}
RARE_EXTENSION_TARGETS: Dict[str, Dict[str, int]] = {
    "jazz": {
        "b9": 3_500,
        "#9": 1_000,
        "11": 2_500,
        "#11": 2_000,
        "13": 1_500,
        "b13": 1_000,
    },
    "pop_rock": {
        "b9": 200,
        "#9": 750,
        "11": 1_250,
        "#11": 200,
        "13": 500,
        "b13": 150,
    },
}
DENSE_EXTENSION_TARGETS: Dict[str, Dict[int, int]] = {
    genre: {2: 15_000, 3: 3_000, 4: 1_000}
    for genre in VALID_GENRES
}
EXTENSION_TARGET_SLOTS = {
    "b9": "ninth",
    "#9": "ninth",
    "11": "eleventh",
    "#11": "eleventh",
    "13": "thirteenth",
    "b13": "thirteenth",
}


def _trie_reaches_target(
    trie_node_table: dict,
    prefix: str,
    depth: int,
    target_depth: int,
    target_value: str,
) -> bool:
    """Whether a prefix has an attested descendant with the requested slot."""
    node = trie_node_table.get(prefix)
    if node is None:
        return False
    children = node.get("marginal", {}).get("counts", {})
    if depth == target_depth:
        return children.get(target_value, 0) > 0
    return any(
        _trie_reaches_target(
            trie_node_table,
            _trie_child_prefix(prefix, child),
            depth + 1,
            target_depth,
            target_value,
        )
        for child, count in children.items()
        if count > 0
    )


def walk_extension_trie_density(
    trie_node_table: dict,
    bass_str: str,
    ext_prev_str: str,
    minimum_active: int,
    params: GenParams,
    rng: random.Random,
    maximum_active: Optional[int] = None,
) -> Optional[Tuple[str, str, str, str]]:
    """Walk a trie while requiring at least ``minimum_active`` extensions."""
    if not 1 <= minimum_active <= len(EXT_SLOTS):
        raise ValueError(f"minimum_active must be between 1 and {len(EXT_SLOTS)}")
    if maximum_active is not None and (
        maximum_active < minimum_active
        or maximum_active > len(EXT_SLOTS)
    ):
        raise ValueError(
            f"maximum_active must be between {minimum_active} and {len(EXT_SLOTS)}"
        )
    if not _trie_reaches_density(
        trie_node_table, "ROOT", 0, 0, minimum_active, maximum_active
    ):
        return None

    chosen: List[str] = []
    active_count = 0
    for depth in range(len(EXT_SLOTS)):
        prefix = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix)
        if node is None:
            return None
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        dist = {
            child: probability
            for child, probability in dist.items()
            if _trie_reaches_density(
                trie_node_table,
                _trie_child_prefix(prefix, child),
                depth + 1,
                active_count + (child != "N"),
                minimum_active,
                maximum_active,
            )
        }
        if not dist:
            return None
        child = weighted_choice(dist, rng)
        chosen.append(child)
        active_count += child != "N"
    return tuple(chosen)


def walk_extension_trie_target(
    trie_node_table: dict,
    bass_str: str,
    ext_prev_str: str,
    target_slot: str,
    target_value: str,
    params: GenParams,
    rng: random.Random,
    maximum_active: Optional[int] = None,
) -> Optional[Tuple[str, str, str, str]]:
    """Walk an attested trie path while requiring one extension token."""
    try:
        target_depth = EXT_SLOTS.index(target_slot)
    except ValueError:
        raise ValueError(f"Unknown extension slot: {target_slot!r}") from None
    if maximum_active is not None and not 1 <= maximum_active <= len(EXT_SLOTS):
        raise ValueError(
            f"maximum_active must be between 1 and {len(EXT_SLOTS)}"
        )

    if not _trie_reaches_target(
        trie_node_table, "ROOT", 0, target_depth, target_value
    ):
        return None

    chosen: List[str] = []
    active_count = 0
    for depth in range(len(EXT_SLOTS)):
        prefix = "ROOT" if depth == 0 else "|".join(chosen)
        node = trie_node_table.get(prefix)
        if node is None:
            return None
        dist = _node_child_distribution(node, bass_str, ext_prev_str, params)
        if not dist:
            return None

        if depth == target_depth:
            if target_value not in dist:
                return None
            dist = {target_value: dist[target_value]}
        if depth < target_depth:
            dist = {
                child: probability
                for child, probability in dist.items()
                if _trie_reaches_target(
                    trie_node_table,
                    _trie_child_prefix(prefix, child),
                    depth + 1,
                    target_depth,
                    target_value,
                )
                and _trie_reaches_density(
                    trie_node_table,
                    _trie_child_prefix(prefix, child),
                    depth + 1,
                    sum(value != "N" for value in chosen) + (child != "N"),
                    0,
                    maximum_active,
                )
            }
        else:
            dist = {
                child: probability
                for child, probability in dist.items()
                if _trie_reaches_density(
                    trie_node_table,
                    _trie_child_prefix(prefix, child),
                    depth + 1,
                    active_count + (child != "N"),
                    0,
                    maximum_active,
                )
            }
        if not dist:
            return None

        child = weighted_choice(dist, rng)
        chosen.append(child)
        active_count += child != "N"

    return tuple(chosen)


@dataclass
class GenerationQuota:
    """Minimum feature counts shared across the songs in one target batch."""

    genre: str
    total_events: int
    triad_targets: Dict[str, int]
    extension_targets: Dict[str, int]
    dense_targets: Dict[int, int] = field(default_factory=dict)
    min_target_spacing: int = 2
    target_buffer: int = 50
    dense_target_buffer: int = 5000
    position: int = 0
    last_target_position: int = -10**9
    triad_counts: Counter = field(default_factory=Counter)
    extension_counts: Counter = field(default_factory=Counter)
    dense_counts: Counter = field(default_factory=Counter)
    triad_root_counts: Dict[str, Counter] = field(default_factory=dict)
    triad_target_root_counts: Dict[str, Counter] = field(default_factory=dict)
    extension_target_root_counts: Dict[str, Counter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {self.genre!r}")
        if self.total_events <= 0:
            raise ValueError("total_events must be positive")
        if self.min_target_spacing < 1:
            raise ValueError("min_target_spacing must be at least 1")
        if self.target_buffer < 0:
            raise ValueError("target_buffer must be non-negative")
        if self.dense_target_buffer < 0:
            raise ValueError("dense_target_buffer must be non-negative")
        if any(count < 0 for count in self.triad_targets.values()):
            raise ValueError("triad target counts must be non-negative")
        if any(count < 0 for count in self.extension_targets.values()):
            raise ValueError("extension target counts must be non-negative")
        if any(
            minimum_active < 1 or minimum_active > len(EXT_SLOTS)
            for minimum_active in self.dense_targets
        ):
            raise ValueError("dense target levels must be between 1 and 4")
        if any(count < 0 for count in self.dense_targets.values()):
            raise ValueError("dense target counts must be non-negative")
        unknown = set(self.extension_targets) - set(EXTENSION_TARGET_SLOTS)
        if unknown:
            raise ValueError(f"Unknown extension target(s): {sorted(unknown)}")

    @classmethod
    def for_genre(cls, genre: str, total_events: int) -> "GenerationQuota":
        """Create the §08 targets, proportionally scaled for smaller tests."""
        if genre not in VALID_GENRES:
            raise ValueError(f"genre must be one of {VALID_GENRES}, got {genre!r}")
        if total_events <= 0:
            raise ValueError("total_events must be positive")
        reference_events = TARGET_EVENTS_BY_GENRE[genre]

        def scaled(source: Dict[str, int]) -> Dict[str, int]:
            return {
                key: (
                    value
                    if total_events >= reference_events
                    else (value * total_events + reference_events - 1)
                    // reference_events
                )
                for key, value in source.items()
                if value > 0
            }

        return cls(
            genre=genre,
            total_events=total_events,
            triad_targets=scaled(RARE_TRIAD_TARGETS[genre]),
            extension_targets=scaled(RARE_EXTENSION_TARGETS[genre]),
            dense_targets=scaled(DENSE_EXTENSION_TARGETS[genre]),
        )

    def remaining(self, kind: str, key: str) -> int:
        targets = self.triad_targets if kind == "triad" else self.extension_targets
        counts = self.triad_counts if kind == "triad" else self.extension_counts
        return max(targets.get(key, 0) - counts.get(key, 0), 0)

    def due_position(self, kind: str, key: str) -> Optional[int]:
        target = (
            self.triad_targets.get(key, 0)
            if kind == "triad"
            else self.extension_targets.get(key, 0)
        )
        if target <= 0:
            return None
        count = (
            self.triad_counts.get(key, 0)
            if kind == "triad"
            else self.extension_counts.get(key, 0)
        )
        if count >= target:
            return None
        schedule_target = target + self.target_buffer
        return (
            (count + 1) * self.total_events + schedule_target - 1
        ) // schedule_target

    def is_due(self, kind: str, key: str) -> bool:
        due = self.due_position(kind, key)
        return due is not None and self.position >= due

    def dense_remaining(self, minimum_active: int) -> int:
        return max(
            self.dense_targets.get(minimum_active, 0)
            - self.dense_counts.get(minimum_active, 0),
            0,
        )

    def dense_due_position(self, minimum_active: int) -> Optional[int]:
        target = self.dense_targets.get(minimum_active, 0)
        count = self.dense_counts.get(minimum_active, 0)
        if target <= 0 or count >= target:
            return None
        schedule_target = target + self.dense_target_buffer
        return (
            (count + 1) * self.total_events + schedule_target - 1
        ) // schedule_target

    def dense_is_due(self, minimum_active: int) -> bool:
        due = self.dense_due_position(minimum_active)
        return due is not None and self.position >= due

    def can_target(self) -> bool:
        return self.position - self.last_target_position >= self.min_target_spacing

    def mark_target(self) -> None:
        self.last_target_position = self.position

    def record(
        self,
        event: dict,
        *,
        targeted_triad: bool = False,
        targeted_extension: Optional[str] = None,
    ) -> None:
        triad = str(event["triad"])
        root = str(event["root_interval"])
        self.triad_counts[triad] += 1
        self.triad_root_counts.setdefault(triad, Counter())[root] += 1
        for slot in EXT_SLOTS:
            value = str(event[slot])
            if value != "N":
                self.extension_counts[value] += 1
        active_count = sum(
            str(event[slot]) != "N"
            for slot in EXT_SLOTS
        )
        for minimum_active in self.dense_targets:
            if active_count >= minimum_active:
                self.dense_counts[minimum_active] += 1
        if targeted_triad:
            self.triad_target_root_counts.setdefault(triad, Counter())[root] += 1
        if targeted_extension is not None:
            if any(
                str(event[slot]) == targeted_extension
                for slot in EXT_SLOTS
            ):
                self.extension_target_root_counts.setdefault(
                    targeted_extension, Counter()
                )[root] += 1
        self.position += 1

    def deficits(self) -> Dict[str, Dict[str, int]]:
        return {
            "triads": {
                key: self.remaining("triad", key)
                for key in self.triad_targets
                if self.remaining("triad", key)
            },
            "extensions": {
                key: self.remaining("extension", key)
                for key in self.extension_targets
                if self.remaining("extension", key)
            },
            "dense": {
                f"{minimum_active}+": self.dense_remaining(minimum_active)
                for minimum_active in self.dense_targets
                if self.dense_remaining(minimum_active)
            },
        }

    def meets_targets(self) -> bool:
        return not any(self.deficits().values())


@dataclass
class TargetGenState(GenState):
    require_observed_successor: bool = False
    pending_target_state: Optional[str] = None


class TargetChordGenerator(ChordGenerator):
    """Chord generator with the §08 target-selection policy enabled."""

    def __init__(
        self,
        *args,
        instrument_profile: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if instrument_profile not in (None, *VOICER_FAMILIES):
            raise ValueError(
                f"instrument_profile must be one of {VOICER_FAMILIES} or None"
            )
        self.instrument_profile = instrument_profile
        self._target_trie_cache: Dict[Tuple[str, str], Optional[dict]] = {}
        self._dense_trie_cache: Dict[Tuple[str, int], Optional[dict]] = {}
        self._reachable_states_cache: Optional[set] = None

    def write_song(
        self,
        path: str,
        events: Optional[List[dict]] = None,
    ) -> None:
        """Write a target song and preserve its voicer-family metadata."""
        if events is None:
            events = self.generate()
        payload = {
            "genre": self.genre,
            "tonic_pc": self.tonic_pc,
            "bpm": self.bpm,
            "num_chords": len(events),
            "duration_total_seconds": sum(
                event["duration_seconds"] for event in events
            ),
            "chords": events,
        }
        if self.instrument_profile is not None:
            payload["voicer_family"] = self.instrument_profile
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _target_extension_trie(
        self,
        state: str,
        triad: str,
        target_value: str,
    ) -> Optional[dict]:
        """Find the narrowest trie that attests a requested extension."""
        if triad in ("1", "5"):
            return None

        cache_key = (state, target_value)
        if cache_key in self._target_trie_cache:
            return self._target_trie_cache[cache_key]

        target_slot = EXTENSION_TARGET_SLOTS[target_value]
        trie_a = self.stage3["trie_A"].get(state)
        if trie_a and _trie_reaches_target(
            trie_a, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
        ):
            self._target_trie_cache[cache_key] = trie_a
            return trie_a

        trie_b = self.stage3["trie_B"].get(triad)
        if trie_b and _trie_reaches_target(
            trie_b, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
        ):
            self._target_trie_cache[cache_key] = trie_b
            return trie_b

        if not trie_b:
            trie_c = self.stage3["trie_C"].get("ALL")
            if trie_c and _trie_reaches_target(
                trie_c, "ROOT", 0, EXT_SLOTS.index(target_slot), target_value
            ):
                self._target_trie_cache[cache_key] = trie_c
                return trie_c

        self._target_trie_cache[cache_key] = None
        return None

    def _dense_extension_trie(
        self,
        state: str,
        triad: str,
        minimum_active: int,
    ) -> Optional[dict]:
        """Find a trie with an attested path at the requested density."""
        if triad in ("1", "5"):
            return None

        cache_key = (state, minimum_active)
        if cache_key in self._dense_trie_cache:
            return self._dense_trie_cache[cache_key]

        candidates = [
            self.stage3["trie_A"].get(state),
            self.stage3["trie_B"].get(triad),
        ]
        if not candidates[1]:
            candidates.append(self.stage3["trie_C"].get("ALL"))
        for trie in candidates:
            if trie and _trie_reaches_density(
                trie, "ROOT", 0, 0, minimum_active
            ):
                self._dense_trie_cache[cache_key] = trie
                return trie

        self._dense_trie_cache[cache_key] = None
        return None

    def _locally_reachable_states(self) -> set:
        """States reachable from the same starts used by natural generation."""
        if self._reachable_states_cache is not None:
            return self._reachable_states_cache

        counts = self.stage1["level0"]["counts"]
        preferred = [
            state
            for state in ("0_major", "0_minor")
            if state in counts
        ]
        starts = preferred[:1] or list(counts)
        reachable = set(starts)
        pending = list(starts)
        while pending:
            state = pending.pop()
            for successor in self.stage1["level1"]["counts"].get(state, {}):
                if successor not in reachable:
                    reachable.add(successor)
                    pending.append(successor)
        self._reachable_states_cache = reachable
        return reachable

    def _target_state(
        self,
        dist1: Dict[str, float],
        ctx1: Optional[str],
        ctx2: Optional[str],
        quota: Optional[GenerationQuota],
    ) -> Tuple[Optional[str], bool, Optional[str]]:
        """Choose a due target only from observed local successor edges."""
        if quota is None or not quota.can_target():
            return None, False, None

        local_counts = _attested_transition_counts(ctx1, ctx2, self.stage1)
        if not local_counts:
            return None, False, None

        due_triads = {
            triad
            for triad in quota.triad_targets
            if quota.is_due("triad", triad)
        }

        def target_root_count(state: str) -> int:
            root, triad = state.rsplit("_", 1)
            return quota.triad_target_root_counts.get(triad, {}).get(root, 0)

        def state_weight(state: str, root_count: Optional[int] = None) -> float:
            if root_count is None:
                root_count = target_root_count(state)
            return (
                dist1.get(state, 0.0)
                * local_counts.get(state, 0)
                / (1.0 + root_count)
            )

        triad_paths = []
        for target_state in dist1:
            if target_state.rsplit("_", 1)[1] not in due_triads:
                continue
            root_count = target_root_count(target_state)
            direct_weight = state_weight(target_state, root_count)
            if target_state in local_counts and direct_weight > 0:
                triad_paths.append(
                    (target_state, None, target_state, root_count, direct_weight)
                )

            if quota.position + 1 >= quota.total_events:
                continue
            for bridge, bridge_count in local_counts.items():
                if bridge == target_state:
                    continue
                edge_count = self.stage1["level1"]["counts"].get(
                    bridge, {}
                ).get(target_state, 0)
                if edge_count <= 0:
                    continue
                bridge_weight = (
                    dist1.get(bridge, 0.0)
                    * bridge_count
                    * edge_count
                    / (1.0 + root_count)
                )
                if bridge_weight > 0:
                    triad_paths.append(
                        (bridge, target_state, target_state, root_count, bridge_weight)
                    )

        if triad_paths:
            reachable = self._locally_reachable_states()
            supported_roots = {
                state.rsplit("_", 1)[0]
                for state in self.stage1["level0"]["counts"]
                if state.rsplit("_", 1)[1] in due_triads
                and state in reachable
            }
            if not supported_roots:
                supported_roots = {path[2].rsplit("_", 1)[0] for path in triad_paths}
            minimum_root_count = min(
                quota.triad_target_root_counts.get(
                    path[2].rsplit("_", 1)[1], {}
                ).get(path[2].rsplit("_", 1)[0], 0)
                for path in triad_paths
                if path[2].rsplit("_", 1)[0] in supported_roots
            )
            balanced = [
                path
                for path in triad_paths
                if path[3] <= minimum_root_count + 1
                and path[2].rsplit("_", 1)[0] in supported_roots
            ]
            if not balanced:
                return None, False, None
            uncovered = [path for path in balanced if path[3] == 0]
            candidates = uncovered or balanced
            chosen_path = self.rng.choices(
                candidates, weights=[path[4] for path in candidates], k=1
            )[0]
            chosen_state, pending_target, _target, _root_count, _weight = chosen_path
            return chosen_state, pending_target is None, pending_target

        due_extensions = (
            []
            if self.instrument_profile == "guitar"
            else [
                value
                for value in quota.extension_targets
                if quota.is_due("extension", value)
            ]
        )
        due_dense = (
            []
            if self.instrument_profile == "guitar"
            else [
                minimum_active
                for minimum_active in quota.dense_targets
                if quota.dense_is_due(minimum_active)
            ]
        )

        extension_candidates = {}
        for state in dist1:
            if state not in local_counts:
                continue
            root, triad = state.rsplit("_", 1)
            supported_extensions = [
                value
                for value in due_extensions
                if self._target_extension_trie(state, triad, value) is not None
            ]
            if supported_extensions:
                root_count = min(
                    quota.extension_target_root_counts.get(value, {}).get(root, 0)
                    for value in supported_extensions
                )
                weight = (
                    dist1.get(state, 0.0)
                    * local_counts.get(state, 0)
                    / (1.0 + root_count)
                )
                if weight > 0:
                    extension_candidates[state] = weight
        if extension_candidates:
            return weighted_choice(extension_candidates, self.rng), False, None

        if not due_dense:
            return None, False, None

        def supports_dense(state: str) -> bool:
            _root, triad = state.rsplit("_", 1)
            return any(
                self._dense_extension_trie(state, triad, minimum_active) is not None
                for minimum_active in due_dense
            )

        dense_paths = []
        for target_state in dist1:
            if not supports_dense(target_state):
                continue
            if target_state in local_counts:
                weight = state_weight(target_state)
                if weight > 0:
                    dense_paths.append((target_state, None, weight))

            if quota.position + 1 >= quota.total_events:
                continue
            for bridge, bridge_count in local_counts.items():
                if bridge == target_state:
                    continue
                edge_count = self.stage1["level1"]["counts"].get(
                    bridge, {}
                ).get(target_state, 0)
                if edge_count <= 0:
                    continue
                weight = (
                    dist1.get(bridge, 0.0)
                    * bridge_count
                    * edge_count
                )
                if weight > 0:
                    dense_paths.append((bridge, target_state, weight))

        if not dense_paths:
            return None, False, None
        chosen_state, pending_target, _weight = self.rng.choices(
            dense_paths,
            weights=[path[2] for path in dense_paths],
            k=1,
        )[0]
        return chosen_state, False, pending_target

    def _target_extension(
        self,
        state: str,
        triad: str,
        quota: Optional[GenerationQuota],
    ) -> Optional[Tuple[str, str, dict]]:
        """Select the most overdue supported extension target for a state."""
        if (
            quota is None
            or not quota.can_target()
            or triad in ("1", "5")
            or self.instrument_profile == "guitar"
        ):
            return None

        candidates = []
        for value in quota.extension_targets:
            if not quota.is_due("extension", value):
                continue
            trie = self._target_extension_trie(state, triad, value)
            if trie is None:
                continue
            due = quota.due_position("extension", value) or quota.position
            candidates.append((value, trie, max(quota.position - due + 1, 1)))
        if not candidates:
            return None

        value = weighted_choice(
            {candidate[0]: candidate[2] for candidate in candidates}, self.rng
        )
        for candidate_value, trie, _weight in candidates:
            if candidate_value == value:
                return EXTENSION_TARGET_SLOTS[value], value, trie
        return None

    def _target_dense_extension(
        self,
        state: str,
        triad: str,
        quota: Optional[GenerationQuota],
    ) -> Optional[Tuple[int, dict]]:
        """Select the densest due extension target supported by this chord."""
        if (
            quota is None
            or not quota.can_target()
            or triad in ("1", "5")
            or self.instrument_profile == "guitar"
        ):
            return None
        due_levels = sorted(
            (
                minimum_active
                for minimum_active in quota.dense_targets
                if quota.dense_is_due(minimum_active)
            ),
            reverse=True,
        )
        for minimum_active in due_levels:
            trie = self._dense_extension_trie(
                state, triad, minimum_active
            )
            if trie is not None:
                return minimum_active, trie
        return None

    def generate(self, quota: Optional[GenerationQuota] = None) -> List[dict]:
        if quota is not None and quota.genre != self.genre:
            raise ValueError(
                f"quota genre {quota.genre!r} does not match generator genre {self.genre!r}"
            )
        state_hist = TargetGenState()
        events: List[dict] = []
        current_time = 0.0
        seconds_per_beat = 60.0 / self.bpm

        for t in range(self.num_chords):
            targeted_triad = False
            if t == 0:
                chosen_state = self._first_state()
            else:
                ctx1 = (
                    state_hist.prev_states[-1]
                    if len(state_hist.prev_states) >= 1
                    else None
                )
                ctx2 = (
                    f"{state_hist.prev_states[-2]}|{state_hist.prev_states[-1]}"
                    if len(state_hist.prev_states) >= 2
                    else None
                )
                dist1 = stage1_distribution(ctx1, ctx2, self.stage1, self.params)
                if not dist1:
                    chosen_state = self._first_state()
                    state_hist.pending_target_state = None
                    state_hist.require_observed_successor = False
                elif state_hist.pending_target_state is not None:
                    pending_state = state_hist.pending_target_state
                    pending_triad = pending_state.rsplit("_", 1)[1]
                    local_counts = _attested_transition_counts(
                        ctx1, ctx2, self.stage1
                    )
                    pending_dense = (
                        quota is not None
                        and any(
                            quota.dense_is_due(minimum_active)
                            and self._dense_extension_trie(
                                pending_state,
                                pending_triad,
                                minimum_active,
                            ) is not None
                            for minimum_active in quota.dense_targets
                        )
                    )
                    if (
                        quota is not None
                        and (
                            quota.is_due("triad", pending_triad)
                            or pending_dense
                        )
                        and local_counts.get(pending_state, 0) > 0
                    ):
                        chosen_state = pending_state
                        targeted_triad = quota.is_due("triad", pending_triad)
                    else:
                        chosen_state = weighted_choice(dist1, self.rng)
                    state_hist.pending_target_state = None
                elif state_hist.require_observed_successor:
                    successor_dist = _attested_successor_distribution(
                        dist1, ctx1, ctx2, self.stage1
                    )
                    if successor_dist:
                        chosen_state = weighted_choice(successor_dist, self.rng)
                    else:
                        (
                            chosen_state,
                            targeted_triad,
                            pending_target,
                        ) = self._target_state(dist1, ctx1, ctx2, quota)
                        state_hist.pending_target_state = pending_target
                        if chosen_state is None:
                            chosen_state = weighted_choice(dist1, self.rng)
                    state_hist.require_observed_successor = False
                else:
                    (
                        chosen_state,
                        targeted_triad,
                        pending_target,
                    ) = self._target_state(dist1, ctx1, ctx2, quota)
                    if pending_target is not None and t == self.num_chords - 1:
                        chosen_state = None
                    else:
                        state_hist.pending_target_state = pending_target
                    if chosen_state is None:
                        chosen_state = weighted_choice(dist1, self.rng)

            root_str, triad = chosen_state.rsplit("_", 1)
            root_interval = int(root_str)

            if triad in ("1", "5"):
                bass_str = "0"
                bass_interval = 0
            else:
                dist2 = stage2_distribution(
                    root_str, state_hist.bass_prev, self.stage2, self.params
                )
                bass_str = weighted_choice(dist2, self.rng) if dist2 else "0"
                bass_interval = int(bass_str)

            targeted_extension_value: Optional[str] = None
            targeted_dense = False
            if triad in ("1", "5"):
                seventh, ninth, eleventh, thirteenth = "N", "N", "N", "N"
            else:
                target_tuple = None
                if state_hist.pending_target_state is None:
                    target = self._target_extension(
                        chosen_state, triad, quota
                    )
                    if target is not None:
                        target_slot, target_value, target_trie = target
                        target_tuple = walk_extension_trie_target(
                            target_trie,
                            bass_str,
                            state_hist.ext_prev_str,
                            target_slot,
                            target_value,
                            self.params,
                            self.rng,
                            self._max_active_extensions(),
                        )

                    if target_tuple is None:
                        dense_target = self._target_dense_extension(
                            chosen_state, triad, quota
                        )
                        if dense_target is not None:
                            minimum_active, dense_trie = dense_target
                            target_tuple = walk_extension_trie_density(
                                dense_trie,
                                bass_str,
                                state_hist.ext_prev_str,
                                minimum_active,
                                self.params,
                                self.rng,
                            )
                            targeted_dense = target_tuple is not None

                if target_tuple is not None:
                    seventh, ninth, eleventh, thirteenth = target_tuple
                    if not targeted_dense:
                        targeted_extension_value = target_value
                else:
                    trie_node_table, _level = select_extension_trie(
                        chosen_state, triad, self.stage3, self.params, self.rng
                    )
                    seventh, ninth, eleventh, thirteenth = walk_extension_trie(
                        trie_node_table,
                        bass_str,
                        state_hist.ext_prev_str,
                        self.params,
                        self.rng,
                        self._max_active_extensions(),
                    )

            root_pc = (root_interval + self.tonic_pc) % 12
            bass_pc = (root_pc + bass_interval) % 12
            root_name = PC_TO_NOTE[root_pc]
            bass_name = PC_TO_NOTE[bass_pc]
            harte = reconstruct_harte(
                root_name,
                triad,
                bass_name,
                seventh,
                ninth,
                eleventh,
                thirteenth,
            )
            duration_token = self.rng.choices(
                list(self.duration_weights),
                weights=list(self.duration_weights.values()),
                k=1,
            )[0]
            duration_beats = DURATION_BEATS[duration_token]
            duration_seconds = duration_beats * seconds_per_beat
            time_start = current_time
            current_time += duration_seconds

            events.append({
                "root_interval": root_interval,
                "triad": triad,
                "bass_interval": bass_interval,
                "seventh": seventh,
                "ninth": ninth,
                "eleventh": eleventh,
                "thirteenth": thirteenth,
                "root": root_name,
                "bass": bass_name,
                "harte": harte,
                "duration_token": duration_token,
                "duration_beats": duration_beats,
                "duration_seconds": duration_seconds,
                "time_start": time_start,
                "time_end": current_time,
            })

            if quota is not None:
                quota.record(
                    events[-1],
                    targeted_triad=targeted_triad,
                    targeted_extension=targeted_extension_value,
                )
                if targeted_triad or targeted_extension_value or targeted_dense:
                    quota.mark_target()
                    if state_hist.pending_target_state is None:
                        state_hist.require_observed_successor = True

            state_hist.prev_states.append(chosen_state)
            if len(state_hist.prev_states) > 2:
                state_hist.prev_states.pop(0)
            state_hist.bass_prev = bass_str
            state_hist.ext_prev_str = "|".join(
                (seventh, ninth, eleventh, thirteenth)
            )

        return events

    def _max_active_extensions(self) -> Optional[int]:
        if self.instrument_profile == "guitar":
            return GUITAR_MAX_ACTIVE_EXTENSIONS
        return None

    def generate_batch(
        self,
        num_songs: int,
        total_events: int,
        quota: Optional[GenerationQuota] = None,
        song_tonics: Optional[List[int]] = None,
        song_bpms: Optional[List[int]] = None,
        song_profiles: Optional[List[Optional[str]]] = None,
    ) -> List[List[dict]]:
        """Generate independently-segmented songs with an exact event total."""
        if num_songs <= 0:
            raise ValueError("num_songs must be positive")
        if total_events < num_songs:
            raise ValueError("total_events must be at least num_songs")
        if quota is not None and quota.total_events != total_events:
            raise ValueError("quota.total_events must equal total_events")
        if song_tonics is not None and len(song_tonics) != num_songs:
            raise ValueError("song_tonics must contain one tonic per song")
        if song_bpms is not None and len(song_bpms) != num_songs:
            raise ValueError("song_bpms must contain one BPM per song")
        if song_bpms is not None and any(bpm <= 0 for bpm in song_bpms):
            raise ValueError("song_bpms must contain only positive BPM values")
        if song_profiles is not None and len(song_profiles) != num_songs:
            raise ValueError("song_profiles must contain one profile per song")
        if song_profiles is not None and any(
            profile not in (None, *VOICER_FAMILIES)
            for profile in song_profiles
        ):
            raise ValueError(
                f"song_profiles must contain only {VOICER_FAMILIES} or None"
            )

        base, remainder = divmod(total_events, num_songs)
        songs = []
        original_num_chords = self.num_chords
        original_tonic_pc = self.tonic_pc
        original_bpm = self.bpm
        original_profile = self.instrument_profile
        try:
            for index in range(num_songs):
                self.num_chords = base + (1 if index < remainder else 0)
                if song_tonics is not None:
                    self.tonic_pc = int(song_tonics[index]) % 12
                if song_bpms is not None:
                    self.bpm = song_bpms[index]
                if song_profiles is not None:
                    self.instrument_profile = song_profiles[index]
                songs.append(self.generate(quota=quota))
        finally:
            self.num_chords = original_num_chords
            self.tonic_pc = original_tonic_pc
            self.bpm = original_bpm
            self.instrument_profile = original_profile
        return songs


def generate_target_corpus(
    events_per_genre: int = TARGET_EVENTS_BY_GENRE["jazz"],
    songs_per_genre: int = 100,
    *,
    events_by_genre: Optional[Dict[str, int]] = None,
    tonic_pc: int | str = 0,
    bpm: int = 120,
    temperature: Optional[float] = None,
    self_transition_discount: Optional[float] = None,
    seed: Optional[int] = None,
    dist_dir: str = _DEFAULT_DIST_DIR,
    params: Optional[GenParams] = None,
    duration_weights: Optional[Dict[str, float]] = None,
    output_dirs: Optional[Dict[str, str]] = None,
    random_tonic: bool = False,
    random_bpm: bool = False,
    random_bpm_range: Tuple[int, int] = (60, 180),
) -> Dict[str, List[List[dict]]]:
    """Generate the quota-driven §08 corpus without mixing genre models."""
    if events_per_genre <= 0:
        raise ValueError("events_per_genre must be positive")
    if songs_per_genre <= 0:
        raise ValueError("songs_per_genre must be positive")
    if events_by_genre is None:
        events_by_genre = {
            genre: events_per_genre
            for genre in VALID_GENRES
        }
    else:
        unknown = set(events_by_genre) - set(VALID_GENRES)
        missing = set(VALID_GENRES) - set(events_by_genre)
        if unknown or missing:
            raise ValueError(
                f"events_by_genre must contain exactly {VALID_GENRES}"
            )
        if any(value <= 0 for value in events_by_genre.values()):
            raise ValueError("events_by_genre values must be positive")
    if any(value < songs_per_genre for value in events_by_genre.values()):
        raise ValueError("each genre event budget must be at least songs_per_genre")
    bpm_lo, bpm_hi = random_bpm_range
    if bpm_lo > bpm_hi:
        bpm_lo, bpm_hi = bpm_hi, bpm_lo
    if random_bpm and bpm_lo <= 0:
        raise ValueError("random_bpm_range must contain only positive BPM values")

    if output_dirs is not None:
        missing = set(VALID_GENRES) - set(output_dirs)
        if missing:
            raise ValueError(f"output_dirs is missing genre(s): {sorted(missing)}")
        for path in output_dirs.values():
            os.makedirs(path, exist_ok=True)

    result: Dict[str, List[List[dict]]] = {}
    for genre_index, genre in enumerate(VALID_GENRES):
        event_budget = events_by_genre[genre]
        quota = GenerationQuota.for_genre(genre, event_budget)
        song_profiles = [
            VOICER_FAMILIES[index % len(VOICER_FAMILIES)]
            for index in range(songs_per_genre)
        ]
        generator_params = (
            GenParams(**vars(params)) if params is not None else GenParams()
        )
        genre_seed = (
            seed + genre_index * 1_000_003
            if seed is not None
            else None
        )
        generator = TargetChordGenerator(
            genre=genre,
            tonic_pc=tonic_pc,
            num_chords=event_budget // songs_per_genre,
            temperature=temperature,
            self_transition_discount=self_transition_discount,
            seed=genre_seed,
            dist_dir=dist_dir,
            bpm=bpm,
            params=generator_params,
            duration_weights=duration_weights,
        )
        property_seed = (
            genre_seed + 7_919
            if genre_seed is not None
            else None
        )
        property_rng = random.Random(property_seed)
        song_tonics = (
            [property_rng.randint(0, 11) for _ in range(songs_per_genre)]
            if random_tonic
            else None
        )
        song_bpms = (
            [
                property_rng.randint(bpm_lo, bpm_hi)
                for _ in range(songs_per_genre)
            ]
            if random_bpm
            else None
        )
        songs = generator.generate_batch(
            songs_per_genre,
            event_budget,
            quota=quota,
            song_tonics=song_tonics,
            song_bpms=song_bpms,
            song_profiles=song_profiles,
        )
        if not quota.meets_targets():
            raise RuntimeError(
                f"Could not meet {genre} generation targets: {quota.deficits()}"
            )
        result[genre] = songs

        if output_dirs is not None:
            for index, events in enumerate(songs):
                if song_tonics is not None:
                    generator.tonic_pc = song_tonics[index]
                if song_bpms is not None:
                    generator.bpm = song_bpms[index]
                generator.instrument_profile = song_profiles[index]
                generator.write_song(
                    os.path.join(output_dirs[genre], f"song_{index}.json"),
                    events,
                )

    return result


def _parse_range(text: str) -> Tuple[int, int]:
    lo_str, hi_str = text.split(",")
    lo, hi = int(lo_str), int(hi_str)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate the quota-aware §08 target chord corpus"
    )
    parser.add_argument(
        "--target-events",
        type=int,
        default=TARGET_TOTAL_EVENTS,
        help="Total quota-aware events across both genres.",
    )
    parser.add_argument(
        "--target-songs",
        "--songs",
        dest="target_songs",
        type=int,
        default=100,
        help="Songs per genre.",
    )
    parser.add_argument(
        "--tonic",
        default="C",
        help="Default tonic note or pitch class; use --random-tonic for per-song values.",
    )
    parser.add_argument("--bpm", type=int, default=120)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Randomize each song's tonic and BPM.",
    )
    parser.add_argument("--random-tonic", action="store_true")
    parser.add_argument("--random-bpm", action="store_true")
    parser.add_argument("--random-bpm-range", default="60,180")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--self-transition-discount", type=float, default=None)
    parser.add_argument("--discount", type=float, default=25)
    parser.add_argument("--add-k", type=float, default=0)
    parser.add_argument("--min-count-a", type=int, default=200)
    parser.add_argument("--min-count-b", type=int, default=500)
    parser.add_argument("--epsilon-floor", type=float, default=0.02)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dist-dir", default=_DEFAULT_DIST_DIR)
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Parent directory for jazz-labels and pop-rock-labels; defaults to gen/target-*.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bpm_range = _parse_range(args.random_bpm_range)
    try:
        fixed_tonic: int | str = int(args.tonic)
    except ValueError:
        fixed_tonic = args.tonic

    if args.target_events <= 0:
        raise ValueError("target event count must be positive")
    if args.target_songs <= 0:
        raise ValueError("target song count must be positive")

    jazz_events = args.target_events // 2
    events_by_genre = {
        "jazz": jazz_events,
        "pop_rock": args.target_events - jazz_events,
    }
    if min(events_by_genre.values()) < args.target_songs:
        raise ValueError("target event count must provide at least one event per song")

    if args.out_dir is None:
        output_dirs = {
            genre: os.path.join("gen", TARGET_LABEL_DIRS[genre])
            for genre in VALID_GENRES
        }
        output_description = "separate target directories below gen/"
    else:
        output_dirs = {
            genre: os.path.join(args.out_dir, GENRE_LABEL_DIRS[genre])
            for genre in VALID_GENRES
        }
        output_description = f"genre-specific directories below {args.out_dir}"

    base_params = GenParams(
        discount=args.discount,
        add_k=args.add_k,
        min_count_a=args.min_count_a,
        min_count_b=args.min_count_b,
        epsilon_floor=args.epsilon_floor,
    )
    random_tonic = args.random_tonic or args.debug
    random_bpm = args.random_bpm or args.debug
    print(
        f"Generating {args.target_events} target event(s) "
        f"({jazz_events} jazz, {args.target_events - jazz_events} pop/rock) "
        f"-> {output_description}"
    )
    generate_target_corpus(
        songs_per_genre=args.target_songs,
        events_by_genre=events_by_genre,
        tonic_pc=fixed_tonic,
        bpm=args.bpm,
        temperature=args.temperature,
        self_transition_discount=args.self_transition_discount,
        seed=args.seed,
        dist_dir=args.dist_dir,
        params=base_params,
        output_dirs=output_dirs,
        random_tonic=random_tonic,
        random_bpm=random_bpm,
        random_bpm_range=bpm_range,
    )
    print("Done.")


if __name__ == "__main__":
    main()
