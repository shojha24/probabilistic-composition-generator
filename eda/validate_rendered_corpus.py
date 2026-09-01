"""Validate rendered JFugue scores against their manifest-paired labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicing.dct import (
    compute_dct,
    predicate_isolated,
    predicate_octave,
    predicate_top,
)
from voicing.types import EXT_SLOTS, SLOT_TO_ROLE, Song, resolve_degrees


_SOURCE_NAME = re.compile(r"^song_(\d+)\.json$")
_NOTE = re.compile(r"^([A-G])(#?)(-?\d+)(ww|w\.?|h\.?|q\.?)$")
_NOTE_PC = {
    "C": 0,
    "C#": 1,
    "D": 2,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "G": 7,
    "G#": 8,
    "A": 9,
    "A#": 10,
    "B": 11,
}
_VOICE = re.compile(r"^V\d+$")


def _parse_note(token: str) -> int | None:
    if token.startswith("R"):
        if token[1:] not in {"ww", "w.", "w", "h.", "h", "q.", "q"}:
            raise ValueError(f"invalid rest token {token!r}")
        return None
    match = _NOTE.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid JFugue note token {token!r}")
    name = match.group(1) + match.group(2)
    octave = int(match.group(3))
    return 12 * (octave + 1) + _NOTE_PC[name]


def _parse_track(tokens: list[str]) -> list[list[int]]:
    events = []
    for token in tokens:
        if token.startswith(("T", "V", "I")):
            continue
        notes = []
        for note_token in token.split("+"):
            midi = _parse_note(note_token)
            if midi is not None:
                notes.append(midi)
        events.append(notes)
    return events


def parse_score_line(line: str) -> tuple[list[list[int]], list[list[int]]]:
    """Return chord-track and bass-track MIDI events from one score line."""
    tokens = line.split()
    try:
        v0 = tokens.index("V0")
        v1 = tokens.index("V1")
    except ValueError as error:
        raise ValueError("score line must contain V0 and V1 tracks") from error
    if v1 <= v0:
        raise ValueError("V1 must follow V0 in a score line")
    next_voice = next(
        (
            index for index in range(v1 + 1, len(tokens))
            if _VOICE.fullmatch(tokens[index])
        ),
        len(tokens),
    )
    return (
        _parse_track(tokens[v0 + 1:v1]),
        _parse_track(tokens[v1 + 1:next_voice]),
    )


def parse_score_blocks(path: str | Path) -> dict[int, str]:
    """Parse START_SONG_N/END_SONG blocks without relying on source names."""
    blocks: dict[int, str] = {}
    current_id = None
    payload: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("START_SONG_"):
            if current_id is not None:
                raise ValueError("nested score song block")
            try:
                current_id = int(stripped.removeprefix("START_SONG_"))
            except ValueError as error:
                raise ValueError(f"malformed start marker {stripped!r}") from error
            payload = []
        elif stripped.startswith("END_SONG"):
            if current_id is None:
                raise ValueError("score has an END_SONG without a start marker")
            if current_id in blocks:
                raise ValueError(f"duplicate score block ordinal {current_id}")
            blocks[current_id] = " ".join(payload).strip()
            current_id = None
            payload = []
        elif current_id is not None and stripped:
            payload.append(stripped)
    if current_id is not None:
        raise ValueError(f"unterminated score block {current_id}")
    return blocks


def _label_dict(chord) -> dict:
    return {
        "root_interval": chord.root_interval,
        "triad": chord.triad,
        "bass_interval": chord.bass_interval,
        "seventh": chord.seventh,
        "ninth": chord.ninth,
        "eleventh": chord.eleventh,
        "thirteenth": chord.thirteenth,
    }


def _type_key(chord_type: tuple) -> str:
    return json.dumps(list(chord_type), separators=(",", ":"))


def _new_bucket() -> dict:
    return {
        "events": 0,
        "root_retained": 0,
        "root_omitted": 0,
        "dct_events": 0,
        "dct_exposed": 0,
        "extension_events": 0,
        "extension_retained": 0,
        "extension_drop_events": 0,
        "extension_drop_roles": 0,
        "permitted_omissions": 0,
        "bass_correct": 0,
        "combined_root_present": 0,
        "combined_active_degree_coverage": 0,
        "combined_missing_active_degrees": 0,
        "unrequested_pitch_classes": 0,
        "violations": 0,
    }


def _issue(
    issues: list[dict],
    category: str,
    record: dict,
    event_index: int | None = None,
    label: dict | None = None,
    midi: list[int] | None = None,
    detail: str | None = None,
) -> None:
    issues.append({
        "category": category,
        "source_file": record.get("source_file"),
        "ordinal": record.get("ordinal"),
        "event_index": event_index,
        "label": label,
        "rendered_midi": midi,
        "detail": detail,
    })


def _load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError(f"{path} must contain a records array")
    return data


def _label_roots(
    labels_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
) -> tuple[Path, ...]:
    if isinstance(labels_dir, (str, Path)):
        labels_dir = [labels_dir]
    roots = tuple(Path(directory).resolve() for directory in labels_dir)
    if not roots:
        raise ValueError("at least one labels directory is required")
    if len(set(roots)) != len(roots):
        raise ValueError("labels directories must be unique")
    return roots


def validate_corpus(
    labels_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    score_path: str | Path,
    manifest_path: str | Path | None = None,
    expected_genre: str | None = None,
) -> dict:
    """Validate one rendered corpus and return a JSON-serializable report."""
    labels_roots = _label_roots(labels_dir)
    score_path = Path(score_path).resolve()
    manifest_path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else score_path.with_name(score_path.name + ".manifest.json")
    )
    manifest = _load_manifest(manifest_path)
    records = manifest["records"]
    blocks = parse_score_blocks(score_path)
    issues: list[dict] = []
    metrics = {
        "genre": defaultdict(_new_bucket),
        "voicer_family": defaultdict(_new_bucket),
        "chord_type": defaultdict(_new_bucket),
        "tonic": defaultdict(_new_bucket),
        "relaxation_level": defaultdict(_new_bucket),
    }
    report = {
        "labels_dir": (
            str(labels_roots[0])
            if len(labels_roots) == 1
            else [str(root) for root in labels_roots]
        ),
        "score": str(score_path),
        "manifest": str(manifest_path),
        "records": len(records),
        "score_blocks": len(blocks),
        "events": 0,
        "issues": issues,
        "metrics": metrics,
        "pairing_errors": 0,
        "hard_failure_count": 0,
        "extension_drop_rates": {},
    }
    extension_counts = defaultdict(lambda: {
        "events": 0,
        "extension_events": 0,
        "drop_events": 0,
        "drop_roles": 0,
    })

    seen_ordinals = set()
    if len(records) != len(blocks):
        _issue(
            issues, "manifest_score_block_count", {},
            detail=f"manifest={len(records)} score_blocks={len(blocks)}",
        )
    for record in records:
        ordinal = record.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal in seen_ordinals:
            _issue(issues, "duplicate_or_invalid_manifest_ordinal", record)
            report["pairing_errors"] += 1
        seen_ordinals.add(ordinal)

    for expected_ordinal, record in enumerate(records):
        ordinal = record.get("ordinal")
        if ordinal != expected_ordinal:
            _issue(
                issues, "manifest_ordinal_order", record,
                detail=f"expected {expected_ordinal}",
            )
            report["pairing_errors"] += 1
        if ordinal not in blocks:
            _issue(issues, "missing_score_block", record)
            report["pairing_errors"] += 1
            continue

        source_name = record.get("source_file")
        source_path = None
        if isinstance(source_name, str):
            record_source_dir = record.get("source_dir")
            if isinstance(record_source_dir, str):
                record_root = Path(record_source_dir).resolve()
                if record_root in labels_roots:
                    source_path = record_root / source_name
            elif len(labels_roots) == 1:
                source_path = labels_roots[0] / source_name
            else:
                candidates = [
                    root / source_name
                    for root in labels_roots
                    if (root / source_name).is_file()
                ]
                if len(candidates) == 1:
                    source_path = candidates[0]
        if source_path is None or source_path.name != source_name or \
                source_path.parent.resolve() not in labels_roots:
            _issue(issues, "invalid_manifest_source_path", record)
            report["pairing_errors"] += 1
            continue
        if not source_path.is_file():
            _issue(issues, "missing_source_file", record)
            report["pairing_errors"] += 1
            continue
        raw = source_path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        if source_hash != record.get("source_sha256"):
            _issue(
                issues, "source_hash_mismatch", record,
                detail=f"manifest={record.get('source_sha256')} actual={source_hash}",
            )
            report["pairing_errors"] += 1
        source_match = _SOURCE_NAME.fullmatch(source_name)
        source_id = int(source_match.group(1)) if source_match else None
        if source_id != record.get("source_id"):
            _issue(
                issues, "source_id_mismatch", record,
                detail=f"filename_id={source_id} manifest_id={record.get('source_id')}",
            )
            report["pairing_errors"] += 1

        progression = json.loads(raw.decode("utf-8"))
        song = Song.from_dict(progression)
        actual_genre = progression.get("genre")
        family = record.get("voicer_family") or "unknown"
        if expected_genre is not None and actual_genre != expected_genre:
            _issue(
                issues, "genre_mismatch", record,
                detail=f"expected={expected_genre} actual={actual_genre}",
            )
        for field in ("genre", "tonic_pc", "bpm", "num_chords"):
            expected = len(song.chords) if field == "num_chords" and field not in progression \
                else progression.get(field, 120 if field == "bpm" else None)
            if record.get(field) != expected:
                _issue(
                    issues, "manifest_metadata_mismatch", record,
                    detail=f"{field}: manifest={record.get(field)!r} source={expected!r}",
                )
                report["pairing_errors"] += 1
        if record.get("num_chords") != len(song.chords):
            _issue(
                issues, "label_event_count_mismatch", record,
                detail=f"metadata={record.get('num_chords')} actual={len(song.chords)}",
            )
            report["pairing_errors"] += 1

        try:
            chord_events, bass_events = parse_score_line(blocks[ordinal])
        except ValueError as error:
            _issue(issues, "malformed_score_block", record, detail=str(error))
            report["pairing_errors"] += 1
            continue
        if len(chord_events) != len(song.chords) or len(bass_events) != len(song.chords):
            _issue(
                issues, "score_event_count_mismatch", record,
                detail=(
                    f"labels={len(song.chords)} chord_track={len(chord_events)} "
                    f"bass_track={len(bass_events)}"
                ),
            )
            report["pairing_errors"] += 1

        summary = record.get("voicing_summary") or {}
        for level, count in (summary.get("relaxation_levels") or {}).items():
            metrics["relaxation_level"][str(level)]["events"] += int(count)

        for event_index, chord in enumerate(song.chords):
            if event_index >= len(chord_events) or event_index >= len(bass_events):
                continue
            midi = chord_events[event_index]
            bass_midi = bass_events[event_index]
            label = _label_dict(chord)
            root_pc = (song.tonic_pc + chord.root_interval) % 12
            bass_pc = (root_pc + chord.bass_interval) % 12
            degrees = resolve_degrees(chord)
            degree_by_role = {degree.role: degree for degree in degrees}
            active_pcs = {
                (root_pc + degree.semitone) % 12 for degree in degrees
            }
            chord_pcs = {pitch % 12 for pitch in midi}
            failures: list[str] = []

            if midi != sorted(midi):
                failures.append("unsorted_midi")
                _issue(issues, "unsorted_midi", record, event_index, label, midi)
            if len(midi) != len(set(midi)):
                failures.append("duplicate_midi")
                _issue(issues, "duplicate_midi", record, event_index, label, midi)
            if any(pitch % 12 not in active_pcs for pitch in midi):
                failures.append("unrequested_pitch_class")
                _issue(
                    issues, "unrequested_pitch_class", record, event_index,
                    label, midi, f"active={sorted(active_pcs)}",
                )
            if len(midi) > 1:
                from voicing.spacing import low_interval_limit_ok
                if not low_interval_limit_ok(midi):
                    failures.append("low_interval_limit")
                    _issue(issues, "low_interval_limit", record, event_index, label, midi)

            if len(bass_midi) != 1:
                failures.append("bass_event_shape")
                _issue(
                    issues, "bass_event_shape", record, event_index,
                    label, bass_midi,
                )
                actual_bass_pc = None
            else:
                actual_bass_pc = bass_midi[0] % 12
                if actual_bass_pc != bass_pc:
                    failures.append("bass_pitch_class")
                    _issue(
                        issues, "bass_pitch_class", record, event_index,
                        label, bass_midi, f"expected_pc={bass_pc}",
                    )

            root_retained = root_pc in chord_pcs
            rootless_allowed = (
                actual_genre == "jazz"
                and chord.bass_interval == 0
                and actual_bass_pc == root_pc
            )
            if not root_retained:
                if actual_genre == "jazz" and not rootless_allowed:
                    failures.append("root_omission_gate")
                    _issue(
                        issues, "root_omission_gate", record, event_index,
                        label, midi,
                        "jazz root omission requires an active root bass and root-position label",
                    )

            missing_roles = []
            missing_extension_roles = []
            permitted_omissions = 0
            dct_role, _secondary = compute_dct(chord, degrees)
            extension_roles = {
                SLOT_TO_ROLE[slot] for slot in EXT_SLOTS
                if getattr(chord, slot) != "N" and SLOT_TO_ROLE[slot] in degree_by_role
            }
            for degree in degrees:
                degree_pc = (root_pc + degree.semitone) % 12
                if degree_pc in chord_pcs:
                    continue
                role = degree.role
                if role == "root" and (rootless_allowed or actual_genre != "jazz"):
                    permitted_omissions += 1
                elif role == "5th" and chord.triad in {"major", "minor"}:
                    permitted_omissions += 1
                else:
                    if role in extension_roles:
                        missing_extension_roles.append(role)
                    if role in extension_roles and family == "guitar" and role != \
                            dct_role:
                        permitted_omissions += 1
                        continue
                    missing_roles.append(role)

            dct_pc = None
            if dct_role is not None:
                dct_degree = degree_by_role.get(dct_role)
                if dct_degree is None:
                    failures.append("dct_role_unresolved")
                    _issue(
                        issues, "dct_role_unresolved", record, event_index,
                        label, midi, f"role={dct_role}",
                    )
                    dct_pitches = []
                else:
                    dct_pc = (root_pc + dct_degree.semitone) % 12
                    dct_pitches = [pitch for pitch in midi if pitch % 12 == dct_pc]
                if not dct_pitches:
                    failures.append("dct_missing")
                    _issue(
                        issues, "dct_missing", record, event_index,
                        label, midi, f"role={dct_role} pc={dct_pc}",
                    )
                elif not any(
                    predicate_top(pitch, midi)
                    or predicate_isolated(pitch, midi)
                    or predicate_octave(pitch, midi)
                    for pitch in dct_pitches
                ):
                    failures.append("dct_not_exposed")
                    _issue(
                        issues, "dct_not_exposed", record, event_index,
                        label, midi, f"role={dct_role} pc={dct_pc}",
                    )

            if missing_roles:
                failures.append("missing_active_degree")
                _issue(
                    issues, "missing_active_degree", record, event_index,
                    label, midi, f"roles={missing_roles}",
                )
            extension_seen = not missing_extension_roles
            combined_pcs = set(chord_pcs)
            if actual_bass_pc is not None:
                combined_pcs.add(actual_bass_pc)
            combined_missing_roles = []
            for degree in degrees:
                degree_pc = (root_pc + degree.semitone) % 12
                if degree_pc in combined_pcs:
                    continue
                role = degree.role
                if role == "root" and rootless_allowed:
                    continue
                if role == "5th" and chord.triad in {"major", "minor"}:
                    continue
                if role in extension_roles and family == "guitar" and role != dct_role:
                    continue
                combined_missing_roles.append(role)
            if combined_missing_roles:
                failures.append("combined_missing_active_degree")
                _issue(
                    issues, "combined_missing_active_degree", record,
                    event_index, label, midi,
                    f"roles={combined_missing_roles}",
                )
            if root_pc not in combined_pcs:
                failures.append("combined_root_missing")
                _issue(
                    issues, "combined_root_missing", record,
                    event_index, label, midi,
                    f"expected_pc={root_pc}",
                )

            report["events"] += 1
            type_key = _type_key(chord.chord_type())
            dimension_keys = {
                "genre": str(actual_genre),
                "voicer_family": str(family),
                "chord_type": type_key,
                "tonic": str(song.tonic_pc),
            }
            for dimension, key in dimension_keys.items():
                bucket = metrics[dimension][key]
                bucket["events"] += 1
                bucket["root_retained" if root_retained else "root_omitted"] += 1
                if dct_role is not None:
                    bucket["dct_events"] += 1
                    if "dct_missing" not in failures and "dct_not_exposed" not in failures:
                        bucket["dct_exposed"] += 1
                if extension_roles:
                    bucket["extension_events"] += 1
                    if extension_seen:
                        bucket["extension_retained"] += 1
                    if missing_extension_roles:
                        bucket["extension_drop_events"] += 1
                        bucket["extension_drop_roles"] += len(missing_extension_roles)
                bucket["permitted_omissions"] += permitted_omissions
                if actual_bass_pc == bass_pc:
                    bucket["bass_correct"] += 1
                if root_pc in combined_pcs:
                    bucket["combined_root_present"] += 1
                covered = len(degrees) - len(combined_missing_roles)
                bucket["combined_active_degree_coverage"] += covered
                bucket["combined_missing_active_degrees"] += len(
                    combined_missing_roles
                )
                bucket["unrequested_pitch_classes"] += sum(
                    1 for pitch in chord_pcs if pitch not in active_pcs
                )
                bucket["violations"] += len(failures)
            pair = extension_counts[(str(actual_genre), str(family))]
            pair["events"] += 1
            if extension_roles:
                pair["extension_events"] += 1
                if missing_extension_roles:
                    pair["drop_events"] += 1
                    pair["drop_roles"] += len(missing_extension_roles)
            if failures:
                report["hard_failure_count"] += len(failures)

    for (genre, family), counts in sorted(extension_counts.items()):
        limit = 0.02 if genre == "pop_rock" and family == "guitar" else \
            0.03 if genre == "jazz" and family == "guitar" else None
        denominator = counts["events"]
        rate = counts["drop_events"] / denominator if denominator else 0.0
        key = f"{genre}:{family}"
        report["extension_drop_rates"][key] = {
            **counts,
            "rate": rate,
            "limit": limit,
        }
        if limit is not None and rate >= limit:
            _issue(
                issues, "extension_drop_budget", {},
                detail=f"{key} rate={rate:.6f} limit<{limit:.6f}",
            )

    report["metrics"] = {
        dimension: dict(values) for dimension, values in metrics.items()
    }
    return report


def _default_corpora(root: Path) -> tuple[tuple[str, Path, Path], ...]:
    return (
        ("jazz", root / "gen" / "target-jazz-labels", root / "gen" / "jazz_scores.txt"),
        (
            "pop_rock",
            root / "gen" / "target-pop-rock-labels",
            root / "gen" / "pop_rock_scores.txt",
        ),
    )


def _print_report(report: dict) -> None:
    print(
        f"{report['score']}: {report['records']} blocks, "
        f"{report['events']} events, {report['hard_failure_count']} hard failures"
    )
    for issue in report["issues"]:
        label = json.dumps(issue.get("label"), sort_keys=True, separators=(",", ":"))
        print(
            f"ERROR {issue['category']}: source={issue.get('source_file')} "
            f"ordinal={issue.get('ordinal')} event={issue.get('event_index')} "
            f"label={label} rendered_midi={issue.get('rendered_midi')} "
            f"{issue.get('detail') or ''}".rstrip()
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--labels-dir", nargs="+")
    parser.add_argument("--scores")
    parser.add_argument("--manifest")
    parser.add_argument("--genre", choices=("jazz", "pop_rock"))
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    explicit = any(value is not None for value in (args.labels_dir, args.scores, args.manifest))
    if explicit:
        if args.labels_dir is None or args.scores is None:
            parser.error("--labels-dir and --scores are required together")
        corpora = [(
            args.genre,
            [Path(labels_dir) for labels_dir in args.labels_dir],
            Path(args.scores),
        )]
        manifest_by_corpus = [Path(args.manifest) if args.manifest else None]
    else:
        defaults = _default_corpora(root)
        corpora = [(genre, labels, scores) for genre, labels, scores in defaults]
        manifest_by_corpus = [None] * len(corpora)

    reports = []
    try:
        for (genre, labels, scores), manifest in zip(corpora, manifest_by_corpus):
            reports.append(validate_corpus(labels, scores, manifest, genre))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR validator setup: {error}", file=sys.stderr)
        return 2

    for report in reports:
        _print_report(report)
    if args.json_out:
        output = {
            "reports": reports,
            "hard_failure_count": sum(report["hard_failure_count"] for report in reports),
        }
        Path(args.json_out).write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 1 if any(report["issues"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
