#!/usr/bin/env python3
"""Create a deterministic, diversity-aware subset of the rendered target corpus."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import tempfile

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instruments import BASS_INSTRUMENTS, CHORD_INSTRUMENTS


GENRES = ("jazz", "pop_rock")
MODES = ("arpeggios", "pads")
FAMILIES = ("piano", "guitar", "synth")
SOURCE_MANIFEST = "gen/target-scores.txt.manifest.json"
SOURCE_SCORE = "gen/target-scores.txt"
SOURCE_MIDI_DIR = "gen/target-output"
DEFAULT_OUTPUT = "gen/curated-songs"
DEFAULT_SEED = 20250308
SOURCE_LABEL_DIRS = {
    "jazz": "gen/target-jazz-labels",
    "pop_rock": "gen/target-pop-rock-labels",
}
_SCORE_START = re.compile(r"^START_SONG_(\d+)$")
_DRUM_TOKEN = re.compile(r"^\[[A-Z0-9_]+\]s$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _score_blocks(score_path: Path) -> dict[int, tuple[str, str]]:
    lines = score_path.read_text(encoding="utf-8").splitlines()
    blocks = {}
    index = 0
    while index < len(lines):
        match = _SCORE_START.fullmatch(lines[index])
        if match is None:
            raise ValueError(
                f"expected a song marker at score line {index + 1}"
            )
        ordinal = int(match.group(1))
        if ordinal in blocks:
            raise ValueError(f"duplicate score ordinal {ordinal}")
        if index + 2 >= len(lines):
            raise ValueError(f"incomplete score block for ordinal {ordinal}")
        if lines[index + 2] != f"END_SONG":
            raise ValueError(f"missing END_SONG for ordinal {ordinal}")
        blocks[ordinal] = (
            "\n".join(lines[index:index + 3]) + "\n",
            lines[index + 1],
        )
        index += 3
    return blocks


def _track_parts(score_line: str) -> dict[str, str]:
    tokens = score_line.split()
    voices = {}
    indexes = {}
    for voice in ("V0", "V1", "V9"):
        try:
            indexes[voice] = tokens.index(voice)
        except ValueError as exc:
            raise ValueError(f"score line is missing {voice}") from exc
    if not indexes["V0"] < indexes["V1"] < indexes["V9"]:
        raise ValueError("score voices are out of order")
    for voice, start in indexes.items():
        end = min(
            (
                candidate
                for candidate in indexes.values()
                if candidate > start
            ),
            default=len(tokens),
        )
        voices[voice] = " ".join(tokens[start:end])
    return voices


def _program(track: str) -> int:
    tokens = track.split()
    if len(tokens) < 2 or not tokens[1].startswith("I"):
        raise ValueError(f"track has no instrument program: {track[:40]!r}")
    return int(tokens[1][1:])


def _extension_values(label: dict) -> tuple[set[str], dict[str, set[str]]]:
    values = set()
    by_slot = defaultdict(set)
    for event in label.get("chords", ()):
        if event.get("is_no_chord", event.get("harte") == "N"):
            continue
        for slot in ("seventh", "ninth", "eleventh", "thirteenth"):
            value = event.get(slot, "N")
            if value == "N":
                continue
            values.add(value)
            by_slot[slot].add(value)
    return values, dict(by_slot)


def _label_features(label: dict) -> dict:
    chords = label.get("chords", ())
    real_chords = [
        event
        for event in chords
        if not event.get("is_no_chord", event.get("harte") == "N")
    ]
    extensions, extension_slots = _extension_values(label)
    densities = {
        sum(
            event.get(slot, "N") != "N"
            for slot in ("seventh", "ninth", "eleventh", "thirteenth")
        )
        for event in real_chords
    }
    return {
        "triads": sorted({event["triad"] for event in real_chords}),
        "extensions": sorted(extensions),
        "extension_slots": {
            slot: sorted(values)
            for slot, values in sorted(extension_slots.items())
        },
        "extension_density": sorted(densities),
        "duration_tokens": sorted({
            event["duration_token"] for event in real_chords
        }),
        "root_intervals": sorted({
            event["root_interval"] for event in real_chords
        }),
        "bass_intervals": sorted({
            event["bass_interval"] for event in real_chords
        }),
        "harte_chords": sorted({
            event.get("harte")
            for event in real_chords
            if event.get("harte") is not None
        }),
        "no_chord": {
            "has_no_chord": len(real_chords) != len(chords),
            "event_count": len(chords) - len(real_chords),
            "event_rate": (
                (len(chords) - len(real_chords)) / len(chords)
                if chords else 0.0
            ),
            "duration_seconds": sum(
                float(event.get("duration_seconds", 0.0))
                for event in chords
                if event.get("is_no_chord", event.get("harte") == "N")
            ),
        },
    }


def _voicing_features(record: dict) -> set[str]:
    summary = record.get("voicing_summary") or {}
    features = set()
    for key in (
        "relaxation_levels",
        "root_omission",
        "dct_exposure",
        "omitted_roles",
    ):
        for value, count in (summary.get(key) or {}).items():
            if count and value != "unknown":
                features.add(f"{key}:{value}")
    features.add(
        "extension_drops:yes"
        if summary.get("extension_drop_count", 0)
        else "extension_drops:no"
    )
    return features


def _percussion_summary(track: str) -> dict:
    tokens = track.split()
    events = tokens[2:]
    kit_tokens = sorted({
        part
        for token in events
        for part in token.split("+")
        if _DRUM_TOKEN.fullmatch(part)
    })
    return {
        "track_event_count": len(events),
        "kit_tokens": kit_tokens,
        "pattern_signature_sha256": hashlib.sha256(
            " ".join(events).encode("utf-8")
        ).hexdigest(),
    }


def _candidate(
    record: dict,
    score_line: str,
    repo_root: Path,
    source_manifest_path: Path,
    midi_dir: Path,
) -> dict:
    label_path = Path(record["source_dir"]) / record["source_file"]
    if not label_path.exists():
        raise FileNotFoundError(f"label file does not exist: {label_path}")
    label_raw = label_path.read_bytes()
    if hashlib.sha256(label_raw).hexdigest() != record["source_sha256"]:
        raise ValueError(
            f"source hash mismatch for ordinal {record['ordinal']}"
        )
    label = json.loads(label_raw.decode("utf-8"))
    tracks = _track_parts(score_line)
    chord_program = _program(tracks["V0"])
    bass_program = _program(tracks["V1"])
    chord_names = {
        instrument.program: instrument.name
        for instruments in CHORD_INSTRUMENTS.values()
        for instrument in instruments
    }
    bass_names = {
        instrument.program: instrument.name
        for instrument in BASS_INSTRUMENTS
    }
    if chord_program not in chord_names:
        raise ValueError(
            f"unknown chord program I{chord_program} at "
            f"ordinal {record['ordinal']}"
        )
    collapsed = (
        record["render_mode"] == "pads"
        and bass_program not in bass_names
    )
    rendered_bass_name = (
        chord_names[chord_program]
        if collapsed
        else bass_names.get(bass_program)
    )
    if rendered_bass_name is None:
        raise ValueError(
            f"unknown bass program I{bass_program} at "
            f"ordinal {record['ordinal']}"
        )
    label_features = _label_features(label)
    features = {
        f"genre:{record['genre']}",
        f"mode:{record['render_mode']}",
        f"family:{record['voicer_family']}",
        f"chord_instrument:{chord_names[chord_program]}",
        f"bass_instrument:{rendered_bass_name}",
        f"pad_collapse:{'yes' if collapsed else 'no'}",
        f"percussion:{'on' if record['percussion_included'] else 'off'}",
        f"percussion_feel:{record['percussion_feel']}",
        *(
            f"triad:{value}"
            for value in label_features["triads"]
        ),
        *(
            f"extension:{value}"
            for value in label_features["extensions"]
        ),
        *(
            f"extension_density:{value}"
            for value in label_features["extension_density"]
        ),
        *(
            f"duration:{value}"
            for value in label_features["duration_tokens"]
        ),
        *(
            f"root_interval:{value}"
            for value in label_features["root_intervals"]
        ),
        *(
            f"bass_interval:{value}"
            for value in label_features["bass_intervals"]
        ),
        (
            "no_chord:yes"
            if label_features["no_chord"]["has_no_chord"]
            else "no_chord:no"
        ),
    }
    features.update(_voicing_features(record))
    if record.get("arpeggio") is not None:
        arpeggio = record["arpeggio"]
        subdivisions = {
            value
            for value in arpeggio["event_subdivisions"]
            if value
        }
        features.add(
            f"profile:{arpeggio['performance_profile']}"
        )
        features.update(
            f"subdivision:{value}" for value in subdivisions
        )
        features.update(
            f"pattern:{value}"
            for value in arpeggio["event_pattern_families"]
            if value
        )
        features.update(
            f"motif:{value}"
            for value in arpeggio["event_motif_families"]
            if value
        )
        features.add(
            "arpeggio_fallback:yes"
            if any(arpeggio["event_pad_fallbacks"])
            else "arpeggio_fallback:no"
        )
        features.add(
            "arpeggio_eighth:yes"
            if "eighth" in subdivisions
            else "arpeggio_eighth:no"
        )
        features.add(
            "arpeggio_sixteenth:yes"
            if "sixteenth" in subdivisions
            else "arpeggio_sixteenth:no"
        )
    midi_path = midi_dir / f"START_SONG_{record['ordinal']}.mid"
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI file does not exist: {midi_path}")
    return {
        "record": record,
        "label": label,
        "label_raw": label_raw,
        "label_path": label_path,
        "score_line": score_line,
        "tracks": tracks,
        "chord_program": chord_program,
        "chord_name": chord_names[chord_program],
        "bass_program": bass_program,
        "bass_name": rendered_bass_name,
        "pad_collapse": collapsed,
        "percussion": _percussion_summary(tracks["V9"]),
        "midi_path": midi_path,
        "features": features,
        "label_features": label_features,
        "cell": (
            record["genre"],
            record["render_mode"],
            record["voicer_family"],
        ),
        "midi_sha256": _sha256(midi_path),
        "repo_root": repo_root,
        "source_manifest_path": source_manifest_path,
    }


def _quotas() -> dict[tuple[str, str, str], int]:
    quotas = {}
    for genre in GENRES:
        for mode in MODES:
            family_counts = (5, 5, 5) if mode == "arpeggios" else (
                12, 12, 11
            )
            for family, count in zip(FAMILIES, family_counts):
                quotas[(genre, mode, family)] = count
    return quotas


def _mandatory_features(candidates: list[dict]) -> set[str]:
    observed = set().union(*(candidate["features"] for candidate in candidates))
    mandatory = {
        feature
        for feature in observed
        if feature.startswith((
            "triad:",
            "extension:",
            "extension_density:",
            "duration:",
            "root_interval:",
            "bass_interval:",
            "no_chord:",
            "chord_instrument:",
            "bass_instrument:",
            "percussion:",
            "percussion_feel:",
            "pad_collapse:",
            "relaxation_levels:",
            "root_omission:",
            "dct_exposure:",
            "omitted_roles:",
            "extension_drops:",
            "profile:",
            "subdivision:",
            "pattern:",
            "motif:",
            "arpeggio_fallback:",
            "arpeggio_eighth:",
            "arpeggio_sixteenth:",
        ))
    }
    return mandatory


def _select(candidates: list[dict], seed: int) -> list[dict]:
    quotas = _quotas()
    by_cell = defaultdict(list)
    for candidate in candidates:
        by_cell[candidate["cell"]].append(candidate)
    for cell, quota in quotas.items():
        if len(by_cell[cell]) < quota:
            raise ValueError(
                f"cell {cell} has {len(by_cell[cell])} candidates, "
                f"needs {quota}"
            )

    mandatory = _mandatory_features(candidates)
    feature_counts = Counter(
        feature
        for candidate in candidates
        for feature in candidate["features"]
    )
    weights = {
        feature: max(1.0, 1000 / feature_counts[feature])
        for feature in mandatory
    }
    for feature in (
        "extension_drops:yes",
        "omitted_roles:9th",
        "relaxation_levels:3",
        "arpeggio_sixteenth:no",
    ):
        if feature in weights:
            weights[feature] = 10000.0

    rng = random.Random(seed)
    tie_breakers = {
        candidate["record"]["ordinal"]: rng.random()
        for candidate in candidates
    }
    selected = []
    selected_ordinals = set()
    used = Counter()
    covered = set()

    for feature in (
        "extension_drops:yes",
        "relaxation_levels:3",
        "arpeggio_sixteenth:no",
    ):
        options = [
            candidate
            for candidate in candidates
            if feature in candidate["features"]
            and candidate["record"]["ordinal"] not in selected_ordinals
            and used[candidate["cell"]] < quotas[candidate["cell"]]
        ]
        if not options:
            raise ValueError(f"cannot select anchor for {feature}")
        selected_candidate = max(
            options,
            key=lambda candidate: (
                len(candidate["features"] & mandatory),
                tie_breakers[candidate["record"]["ordinal"]],
                -candidate["record"]["ordinal"],
            ),
        )
        selected.append(selected_candidate)
        selected_ordinals.add(selected_candidate["record"]["ordinal"])
        used[selected_candidate["cell"]] += 1
        covered.update(selected_candidate["features"])

    while len(selected) < sum(quotas.values()):
        available = [
            candidate
            for candidate in candidates
            if candidate["record"]["ordinal"] not in selected_ordinals
            and used[candidate["cell"]] < quotas[candidate["cell"]]
        ]
        if not available:
            raise ValueError("selection exhausted candidates before quotas")
        needed = mandatory - covered

        def score(candidate: dict) -> tuple[float, int, int, float, int]:
            new_features = candidate["features"] & needed
            novel_features = candidate["features"] - covered
            label_features = sum(
                feature.startswith((
                    "triad:",
                    "extension:",
                    "duration:",
                    "root_interval:",
                    "bass_interval:",
                ))
                for feature in novel_features
            )
            return (
                sum(weights.get(feature, 0.0) for feature in new_features),
                len(novel_features),
                label_features,
                tie_breakers[candidate["record"]["ordinal"]],
                -candidate["record"]["ordinal"],
            )

        selected_candidate = max(available, key=score)
        selected.append(selected_candidate)
        selected_ordinals.add(selected_candidate["record"]["ordinal"])
        used[selected_candidate["cell"]] += 1
        covered.update(selected_candidate["features"])

    missing = mandatory - covered
    if missing:
        raise ValueError(
            "selection did not cover mandatory features: "
            + ", ".join(sorted(missing))
        )
    return selected


def _coverage_map(selected: list[dict], sample_ids: dict[int, str]) -> dict:
    categories = defaultdict(lambda: defaultdict(list))

    def add(category: str, value: object, candidate: dict) -> None:
        categories[category][str(value)].append(
            sample_ids[candidate["record"]["ordinal"]]
        )

    for candidate in selected:
        record = candidate["record"]
        label_features = candidate["label_features"]
        add("genres", record["genre"], candidate)
        add("render_modes", record["render_mode"], candidate)
        add("voicers", record["voicer"], candidate)
        add("voicer_families", record["voicer_family"], candidate)
        add("chord_instruments", candidate["chord_name"], candidate)
        add("bass_instruments", candidate["bass_name"], candidate)
        add("percussion_included", record["percussion_included"], candidate)
        add("percussion_feel", record["percussion_feel"], candidate)
        add("pad_collapse_inferred", candidate["pad_collapse"], candidate)
        for category in (
            "triads",
            "extensions",
            "duration_tokens",
            "root_intervals",
            "bass_intervals",
        ):
            for value in label_features[category]:
                add(category, value, candidate)
        for value in label_features["extension_density"]:
            add("extension_density", value, candidate)
        add(
            "no_chord",
            label_features["no_chord"]["has_no_chord"],
            candidate,
        )
        summary = record.get("voicing_summary") or {}
        for category in (
            "relaxation_levels",
            "root_omission",
            "dct_exposure",
            "omitted_roles",
        ):
            for value, count in (summary.get(category) or {}).items():
                if count and value != "unknown":
                    add(category, value, candidate)
        add(
            "extension_drops",
            bool(summary.get("extension_drop_count")),
            candidate,
        )
        if record.get("arpeggio") is None:
            continue
        arpeggio = record["arpeggio"]
        add("arpeggio_profiles", arpeggio["performance_profile"], candidate)
        for value in {
            value for value in arpeggio["event_subdivisions"] if value
        }:
            add("arpeggio_subdivisions", value, candidate)
        for category, key in (
            ("arpeggio_patterns", "event_pattern_families"),
            ("arpeggio_motifs", "event_motif_families"),
        ):
            for value in {value for value in arpeggio[key] if value}:
                add(category, value, candidate)
        add(
            "arpeggio_fallbacks",
            any(arpeggio["event_pad_fallbacks"]),
            candidate,
        )
        add(
            "arpeggio_all_eighth",
            "sixteenth" not in {
                value
                for value in arpeggio["event_subdivisions"]
                if value
            },
            candidate,
        )
    return {
        category: {
            value: sorted(sample_ids)
            for value, sample_ids in sorted(values.items())
        }
        for category, values in sorted(categories.items())
    }


def _counts(selected: list[dict]) -> dict:
    def count_values(values) -> dict:
        return dict(sorted(Counter(values).items(), key=lambda item: str(item[0])))

    return {
        "total": len(selected),
        "genres": count_values(
            candidate["record"]["genre"] for candidate in selected
        ),
        "render_modes": count_values(
            candidate["record"]["render_mode"] for candidate in selected
        ),
        "voicers": count_values(
            candidate["record"]["voicer"] for candidate in selected
        ),
        "voicer_families": count_values(
            (
                f"{candidate['record']['genre']}/"
                f"{candidate['record']['voicer_family']}"
            )
            for candidate in selected
        ),
        "chord_instruments": count_values(
            candidate["chord_name"] for candidate in selected
        ),
        "bass_instruments": count_values(
            candidate["bass_name"] for candidate in selected
        ),
        "percussion_included": count_values(
            candidate["record"]["percussion_included"]
            for candidate in selected
        ),
        "percussion_feel": count_values(
            candidate["record"]["percussion_feel"]
            for candidate in selected
        ),
        "pad_collapse_inferred": count_values(
            candidate["pad_collapse"] for candidate in selected
        ),
    }


def _metadata(candidate: dict, sample_id: str, files: dict) -> dict:
    record = candidate["record"]
    label = candidate["label"]
    label_features = candidate["label_features"]
    summary = record.get("voicing_summary") or {}
    return {
        "sample_id": sample_id,
        "source": {
            "manifest_record": record,
            "score_ordinal": record["ordinal"],
            "label": {
                "path": str(
                    Path(SOURCE_LABEL_DIRS[record["genre"]])
                    / record["source_file"]
                ),
                "sha256": record["source_sha256"],
            },
            "midi": {
                "path": str(
                    Path(SOURCE_MIDI_DIR)
                    / f"START_SONG_{record['ordinal']}.mid"
                ),
                "sha256": candidate["midi_sha256"],
            },
        },
        "label_summary": {
            "genre": label.get("genre"),
            "tonic_pc": label.get("tonic_pc"),
            "bpm": label.get("bpm", 120),
            "num_chords": label.get("num_chords", len(label.get("chords", ()))),
            "duration_total_seconds": label.get("duration_total_seconds"),
            **label_features,
        },
        "chord_pad": {
            "render_mode": record["render_mode"],
            "track_file": files["chord_track"],
            "instrument": {
                "name": candidate["chord_name"],
                "program": candidate["chord_program"],
            },
            "pad_collapse_inferred": candidate["pad_collapse"],
        },
        "arpeggio": (
            None
            if record.get("arpeggio") is None
            else {
                "track_file": files["arpeggio_track"],
                "performance": record["arpeggio"],
            }
        ),
        "bass": {
            "track_file": files["bass_track"],
            "rendered_instrument": {
                "name": candidate["bass_name"],
                "program": candidate["bass_program"],
            },
            "pad_collapse_inferred": candidate["pad_collapse"],
        },
        "percussion": {
            "track_file": files["percussion_track"],
            "included": record["percussion_included"],
            "feel": record["percussion_feel"],
            **candidate["percussion"],
        },
        "voicing_style": {
            "voicer": record["voicer"],
            "voicer_genre": record["voicer_genre"],
            "voicer_family": record["voicer_family"],
            "preferred_voicer": record["preferred_voicer"],
            "preferred_voicer_family": record["preferred_voicer_family"],
            "voicer_order": record["voicer_order"],
            "summary": summary,
        },
        "instruments_used": {
            "chord": {
                "name": candidate["chord_name"],
                "program": candidate["chord_program"],
                "track": "V0",
            },
            "bass": {
                "name": candidate["bass_name"],
                "program": candidate["bass_program"],
                "track": "V1",
            },
            "percussion": {
                "included": record["percussion_included"],
                "feel": record["percussion_feel"],
                "track": "V9",
            },
        },
        "track_programs": {
            "V0": candidate["chord_program"],
            "V1": candidate["bass_program"],
            "V9": 9,
        },
        "files": files,
        "selection_features": sorted(candidate["features"]),
    }


def curate(
    manifest_path: Path,
    score_path: Path,
    midi_dir: Path,
    output_dir: Path,
    seed: int,
) -> dict:
    if output_dir.exists():
        raise FileExistsError(
            f"output directory already exists: {output_dir}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = sorted(manifest["records"], key=lambda record: record["ordinal"])
    score_blocks = _score_blocks(score_path)
    if len(records) != len(score_blocks):
        raise ValueError(
            f"manifest has {len(records)} records but score has "
            f"{len(score_blocks)} blocks"
        )
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        _candidate(
            record,
            score_blocks[record["ordinal"]][1],
            repo_root,
            manifest_path,
            midi_dir,
        )
        for record in records
    ]
    selected = _select(candidates, seed)
    selected = sorted(
        selected,
        key=lambda candidate: (
            GENRES.index(candidate["record"]["genre"]),
            MODES.index(candidate["record"]["render_mode"]),
            FAMILIES.index(candidate["record"]["voicer_family"]),
            candidate["record"]["source_id"],
        ),
    )
    sample_ids = {
        candidate["record"]["ordinal"]: f"song_{index:03d}"
        for index, candidate in enumerate(selected, start=1)
    }
    temp_dir = Path(tempfile.mkdtemp(
        prefix=f".{output_dir.name}.",
        dir=output_dir.parent,
    ))
    try:
        songs = []
        for candidate in selected:
            record = candidate["record"]
            sample_id = sample_ids[record["ordinal"]]
            sample_dir = temp_dir / sample_id
            tracks_dir = sample_dir / "tracks"
            midi_output_dir = sample_dir / "midi"
            tracks_dir.mkdir(parents=True)
            midi_output_dir.mkdir()
            score_block = score_blocks[record["ordinal"]][0]
            (sample_dir / "score.txt").write_text(
                score_block,
                encoding="utf-8",
            )
            shutil.copyfile(
                candidate["label_path"],
                sample_dir / "labels.json",
            )
            shutil.copyfile(
                candidate["midi_path"],
                midi_output_dir / "song.mid",
            )
            files = {
                "labels": "labels.json",
                "score": "score.txt",
                "midi": "midi/song.mid",
                "chord_track": "tracks/chord.txt",
                "bass_track": "tracks/bass.txt",
                "percussion_track": "tracks/percussion.txt",
                "arpeggio_track": None,
            }
            (tracks_dir / "chord.txt").write_text(
                candidate["tracks"]["V0"] + "\n",
                encoding="utf-8",
            )
            (tracks_dir / "bass.txt").write_text(
                candidate["tracks"]["V1"] + "\n",
                encoding="utf-8",
            )
            (tracks_dir / "percussion.txt").write_text(
                candidate["tracks"]["V9"] + "\n",
                encoding="utf-8",
            )
            if record.get("arpeggio") is not None:
                files["arpeggio_track"] = "tracks/arpeggio.txt"
                (tracks_dir / "arpeggio.txt").write_text(
                    candidate["tracks"]["V0"] + "\n",
                    encoding="utf-8",
                )
            _write_json(
                sample_dir / "metadata.json",
                _metadata(candidate, sample_id, files),
            )
            songs.append({
                "sample_id": sample_id,
                "path": sample_id,
                "source_ordinal": record["ordinal"],
                "source_genre": record["genre"],
                "source_file": record["source_file"],
                "render_mode": record["render_mode"],
                "voicer": record["voicer"],
                "files": {
                    key: f"{sample_id}/{value}"
                    if value is not None else None
                    for key, value in files.items()
                },
            })

        coverage = _coverage_map(selected, sample_ids)
        counts = _counts(selected)
        mandatory = sorted(_mandatory_features(candidates))
        observed_bpm = sorted({
            candidate["label"].get("bpm", 120)
            for candidate in candidates
        })
        observed_tonic = sorted({
            candidate["label"].get("tonic_pc")
            for candidate in candidates
        })
        curation_manifest = {
            "manifest_version": 1,
            "curation": {
                "algorithm": (
                    "deterministic greedy set cover with fixed rare-case "
                    "anchors and exact genre/mode/family quotas"
                ),
                "selection_seed": seed,
                "sample_count": len(selected),
                "genre_quota": {"jazz": 50, "pop_rock": 50},
                "render_mode_quota": {
                    "arpeggios": 30,
                    "pads": 70,
                },
                "cell_quota": {
                    f"{genre}/{mode}/{family}": count
                    for (genre, mode, family), count
                    in sorted(_quotas().items())
                },
                "mandatory_feature_count": len(mandatory),
                "mandatory_features": mandatory,
            },
            "source_artifacts": {
                "render_manifest": str(
                    Path("..") / Path(SOURCE_MANIFEST).relative_to("gen")
                ),
                "score": str(
                    Path("..") / Path(SOURCE_SCORE).relative_to("gen")
                ),
                "midi_directory": str(
                    Path("..") / Path(SOURCE_MIDI_DIR).relative_to("gen")
                ),
                "label_directories": {
                    genre: str(
                        Path("..") / Path(path).relative_to("gen")
                    )
                    for genre, path in SOURCE_LABEL_DIRS.items()
                },
                "validation_report": "../target-validation.json",
            },
            "observed_source_limits": {
                "bpm": observed_bpm,
                "tonic_pc": observed_tonic,
                "note": (
                    "The target artifacts provide no BPM or tonic variation "
                    "beyond these observed values."
                ),
            },
            "counts": counts,
            "coverage": coverage,
            "songs": songs,
        }
        validation = {
            "passed": True,
            "sample_count": len(songs),
            "unique_source_ordinals": len({
                song["source_ordinal"] for song in songs
            }),
            "unique_source_labels": len({
                (song["source_genre"], song["source_file"])
                for song in songs
            }),
            "counts": counts,
            "coverage_categories": {
                category: len(values)
                for category, values in coverage.items()
            },
        }
        _write_json(temp_dir / "manifest.json", curation_manifest)
        _write_json(temp_dir / "validation.json", validation)
        os.replace(temp_dir, output_dir)
        return curation_manifest
    except BaseException:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=SOURCE_MANIFEST)
    parser.add_argument("--score", default=SOURCE_SCORE)
    parser.add_argument("--midi-dir", default=SOURCE_MIDI_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    result = curate(
        Path(args.manifest).resolve(),
        Path(args.score).resolve(),
        Path(args.midi_dir).resolve(),
        Path(args.out_dir).resolve(),
        args.seed,
    )
    print(
        f"Curated {result['counts']['total']} songs to {args.out_dir} "
        f"({result['counts']['genres']}, {result['counts']['render_modes']})"
    )


if __name__ == "__main__":
    main()
