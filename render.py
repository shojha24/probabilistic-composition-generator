"""Render generated progression JSON files as synchronized JFugue score text."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from bass_module import BassModule
from chord_module import ChordModule, POLICIES
from percussion_module import PercussionModule


_SONG_FILENAME = re.compile(r"^song_(\d+)\.json$")
_SOURCE_GENRES = ("jazz", "pop_rock")
_VOICER_FAMILIES = ("piano", "guitar", "synth")
_VOICER_ID_BY_GENRE_FAMILY = {
    (genre, family): policy.voicer_id
    for (genre, family), policy in POLICIES.items()
}
_VOICER_FAMILY_BY_ID = {
    voicer: family
    for (genre, family), voicer in _VOICER_ID_BY_GENRE_FAMILY.items()
}
_VOICER_IDS = tuple(
    _VOICER_ID_BY_GENRE_FAMILY[(genre, family)]
    for genre in _SOURCE_GENRES
    for family in _VOICER_FAMILIES
)
_VOICER_ORDER = {
    voicer: index for index, voicer in enumerate(_VOICER_IDS)
}


def _input_dir_list(
    input_dirs: str | Path | list[str] | tuple[str, ...],
) -> list[str]:
    if isinstance(input_dirs, (str, Path)):
        input_dirs = [str(input_dirs)]
    else:
        input_dirs = [str(input_dir) for input_dir in input_dirs]
    if not input_dirs:
        raise ValueError("at least one input directory is required")
    resolved = [str(Path(input_dir).resolve()) for input_dir in input_dirs]
    if len(set(resolved)) != len(resolved):
        raise ValueError("input directories must be unique")
    return input_dirs


def _source_files(
    input_dir: str | Path | list[str] | tuple[str, ...],
) -> list[tuple[int, Path, bytes, dict, Path]]:
    records = []
    for input_dir in _input_dir_list(input_dir):
        input_path = Path(input_dir)
        candidates = [
            path for path in input_path.glob("song_*.json")
            if path.is_file()
        ]
        if not candidates:
            raise FileNotFoundError(
                f"No song_*.json files found in {input_dir}"
            )

        seen_ids = {}
        directory_records = []
        for path in candidates:
            match = _SONG_FILENAME.fullmatch(path.name)
            if match is None:
                raise ValueError(
                    f"Malformed song filename {path.name!r}; "
                    "expected song_<integer>.json"
                )
            source_id = int(match.group(1))
            if source_id in seen_ids:
                raise ValueError(
                    f"Duplicate numeric song id {source_id}: "
                    f"{seen_ids[source_id].name} and {path.name}"
                )
            seen_ids[source_id] = path
            raw = path.read_bytes()
            progression = json.loads(raw.decode("utf-8"))
            if not isinstance(progression, dict):
                raise ValueError(f"{path} must contain a JSON object")
            directory_records.append((
                source_id, path, raw, progression, input_path,
            ))
        records.extend(sorted(
            directory_records, key=lambda item: item[0]
        ))
    return records


def _generator_revision() -> tuple[str, bool]:
    repo_root = Path(__file__).resolve().parent
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            [
                "git", "status", "--porcelain", "--untracked-files=normal",
                "--", "chord_module.py", "render.py", "voicing", "eda",
                "percussion_module.py",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() != ""
        return revision, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def _manifest_path(output: str) -> Path:
    output_path = Path(output)
    return output_path.with_name(output_path.name + ".manifest.json")


def _render_command(
    input_dirs: list[str],
    output: str,
    seed: int,
    mode: str,
) -> str:
    return shlex.join([
        sys.executable, "render.py", "--in-dir", *input_dirs,
        "--out", output, "--mode", mode, "--seed", str(seed),
    ])


def _voicing_summary(chord_module: ChordModule) -> dict:
    diagnostics = chord_module.last_voicing_diagnostics
    levels = Counter(str(item.get("level", "unknown")) for item in diagnostics)
    omission = Counter(
        item.get("root_omission", "unknown") for item in diagnostics
    )
    dct_exposure = Counter(
        item.get("dct_exposure") or "none" for item in diagnostics
    )
    omitted_roles = Counter()
    extension_drops = 0
    extension_drop_records = []
    for event_index, item in enumerate(diagnostics):
        omitted_roles.update(item.get("omitted_roles") or ())
        dropped_roles = item.get("extensions_dropped") or ()
        extension_drops += len(dropped_roles)
        for role in dropped_roles:
            extension_drop_records.append({
                "event_index": event_index,
                "chord_type": item.get("chord_type"),
                "shape_id": item.get("shape_id"),
                "role": role,
            })
    return {
        "relaxation_levels": dict(sorted(levels.items())),
        "root_omission": dict(sorted(omission.items())),
        "dct_exposure": dict(sorted(dct_exposure.items())),
        "omitted_roles": dict(sorted(omitted_roles.items())),
        "extension_drop_count": extension_drops,
        "extension_drops": extension_drop_records,
    }


def _least_used_voicer(voicers: list[str], counts: Counter) -> str:
    return min(
        voicers,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )


def _voicers_for_genre(genre: str) -> list[str]:
    try:
        return [
            _VOICER_ID_BY_GENRE_FAMILY[(genre, family)]
            for family in _VOICER_FAMILIES
        ]
    except KeyError:
        raise ValueError(
            f"Unsupported source genre {genre!r}; "
            f"expected one of {_SOURCE_GENRES}"
        ) from None


def _voicer_family(voicer: str) -> str:
    try:
        return _VOICER_FAMILY_BY_ID[voicer]
    except KeyError:
        raise ValueError(f"Unknown voicer {voicer!r}") from None


def _preferred_family(progression: dict) -> str | None:
    hint = progression.get("voicer_family")
    if not isinstance(hint, str):
        return None
    if hint in _VOICER_FAMILIES:
        return hint
    return _VOICER_FAMILY_BY_ID.get(hint)


def _unique_in_order(values: list[str | None]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value is not None and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _voicer_order(
    genre: str,
    counts: Counter,
    preferred_family: str | None,
) -> list[str]:
    own_genre = _voicers_for_genre(genre)
    other_genre = _voicers_for_genre(
        _SOURCE_GENRES[1 - _SOURCE_GENRES.index(genre)]
    )
    own_ranked = sorted(
        own_genre,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )
    other_ranked = sorted(
        other_genre,
        key=lambda voicer: (counts[voicer], _VOICER_ORDER[voicer]),
    )
    preferred_own = (
        _VOICER_ID_BY_GENRE_FAMILY.get((genre, preferred_family))
        if preferred_family is not None else None
    )
    other_genre_name = (
        _SOURCE_GENRES[1 - _SOURCE_GENRES.index(genre)]
    )
    preferred_other = (
        _VOICER_ID_BY_GENRE_FAMILY.get((other_genre_name, preferred_family))
        if preferred_family is not None else None
    )
    return _unique_in_order([
        own_ranked[0],
        preferred_own,
        *own_ranked,
        other_ranked[0],
        preferred_other,
        *other_ranked,
    ])


def _render_source(args: tuple) -> dict:
    """Render one source record in an isolated worker."""
    index, raw, seed, mode, *voicer_orders = args
    progression = json.loads(raw.decode("utf-8"))
    chord_module = ChordModule(mode=mode, seed=seed + index)
    if voicer_orders:
        requested_order = voicer_orders[0]
        if isinstance(requested_order, (list, tuple)):
            chord_track = chord_module.render(
                progression,
                bass_module_active=True,
                voicer_order=requested_order,
            )
        else:
            chord_track = chord_module.render(
                progression,
                preferred_family=requested_order,
                bass_module_active=True,
            )
    else:
        chord_track = chord_module.render(
            progression,
            preferred_family=progression.get("voicer_family"),
            bass_module_active=True,
        )
    bass_module = BassModule(seed=seed + index)
    bass_track = bass_module.render(
        progression,
        pad_instrument=chord_module.selected_instrument,
        pad_mode=mode == "pads",
        chord_midis=chord_module.last_voiced_midis,
    )
    percussion_module = PercussionModule(seed=seed + index)
    percussion_track = percussion_module.render(progression)
    return {
        "chord_track": chord_track,
        "bass_track": bass_track,
        "percussion_track": percussion_track,
        "percussion_included": percussion_module.last_included,
        "percussion_feel": percussion_module.last_feel,
        "voicer": chord_module.last_voicer or "unknown",
        "voicer_genre": chord_module.last_voicer_genre,
        "voicer_family": chord_module.last_voicer_family,
        "instrument": chord_module.last_instrument,
        "instrument_program": chord_module.last_instrument_program,
        "bass_instrument": bass_module.last_instrument.name,
        "bass_program": bass_module.last_instrument.program,
        "pad_collapse": bass_module.collapsed_to_pad,
        "voicing_summary": _voicing_summary(chord_module),
    }


def render_song(progression: dict, seed: int | None = None, mode: str = "pads") -> str:
    chords = ChordModule(mode=mode, seed=seed).render(
        progression,
        preferred_family=progression.get("voicer_family"),
        bass_module_active=True,
    )
    bass = BassModule(seed=seed).render(progression)
    percussion = PercussionModule(seed=seed).render(progression)
    return "  ".join((chords, bass, percussion))


def render_directory(
    input_dir: str | Path | list[str] | tuple[str, ...],
    output: str,
    seed: int | None = None,
    mode: str = "pads",
) -> None:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("render_directory requires an explicit integer seed")
    input_dir_list = _input_dir_list(input_dir)
    files = _source_files(input_dir_list)
    source_dirs = [
        str(Path(input_dir).resolve()) for input_dir in input_dir_list
    ]
    revision, dirty = _generator_revision()
    output_path = Path(output)
    manifest_path = _manifest_path(output)
    voicer_counts = Counter()
    family_counts_by_genre = {}
    manifest_records = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_path.parent,
        prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
    )
    temporary_output_path = Path(temporary_output.name)
    try:
        with temporary_output as out:
            workers = min(8, len(files), os.cpu_count() or 1)
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for batch_start in range(0, len(files), workers):
                    batch = files[batch_start:batch_start + workers]
                    projected_counts = Counter(voicer_counts)
                    voicer_orders = []
                    for (
                        _source_id, _path, _raw, progression, _source_dir
                    ) in batch:
                        genre = progression.get("genre")
                        order = _voicer_order(
                            genre,
                            projected_counts,
                            _preferred_family(progression),
                        )
                        projected_counts[order[0]] += 1
                        voicer_orders.append(order)
                    render_args = (
                        (batch_start + offset, raw, seed, mode, voicer_order)
                        for offset, (
                            (_source_id, _path, raw, _progression, _source_dir),
                            voicer_order,
                        ) in enumerate(zip(batch, voicer_orders))
                    )
                    rendered = executor.map(_render_source, render_args, chunksize=1)
                    for offset, result in enumerate(rendered):
                        index = batch_start + offset
                        (
                            source_id, path, raw, progression, source_dir
                        ) = batch[offset]
                        voicer = result["voicer"]
                        voicer_counts[voicer] += 1
                        genre = progression.get("genre")
                        family_counts_by_genre.setdefault(
                            genre, Counter()
                        )[result["voicer_family"]] += 1
                        chord_track = result["chord_track"]
                        bass_track = result["bass_track"]
                        percussion_track = result["percussion_track"]
                        out.write(f"START_SONG_{index}\n")
                        out.write(
                            f"{chord_track}  {bass_track}  {percussion_track}\n"
                        )
                        out.write("END_SONG\n")
                        manifest_records.append({
                            "ordinal": index,
                            "source_dir": str(source_dir.resolve()),
                            "source_file": path.name,
                            "source_id": source_id,
                            "source_sha256": hashlib.sha256(raw).hexdigest(),
                            "genre": progression.get("genre"),
                            "tonic_pc": progression.get("tonic_pc"),
                            "bpm": progression.get("bpm", 120),
                            "num_chords": progression.get(
                                "num_chords", len(progression.get("chords", ()))
                            ),
                            "preferred_voicer": voicer_orders[offset][0],
                            "preferred_voicer_family": _voicer_family(
                                voicer_orders[offset][0]
                            ),
                            "voicer_order": voicer_orders[offset],
                            "voicer": voicer,
                            "voicer_genre": result["voicer_genre"],
                            "voicer_family": result["voicer_family"],
                            "percussion_included": result["percussion_included"],
                            "percussion_feel": result["percussion_feel"],
                            "seed": seed + index,
                            "voicing_summary": result["voicing_summary"],
                        })
                        print(
                            f"Rendered song {index}: {source_dir.name}/{path.name} "
                            f"({voicer}, chord={result['instrument']} "
                            f"[I{result['instrument_program']}], "
                            f"bass={result['bass_instrument']} "
                            f"[I{result['bass_program']}]"
                            f"{' (pad collapse)' if result['pad_collapse'] else ''}, {mode})"
                            f", percussion={'on' if result['percussion_included'] else 'off'}"
                        )

        manifest = {
            "manifest_version": 1,
            "command": _render_command(input_dir_list, output, seed, mode),
            "seed": seed,
            "source_dir": source_dirs[0] if len(source_dirs) == 1 else None,
            "source_dirs": source_dirs,
            "output": str(output_path.resolve()),
            "generator_revision": revision,
            "generator_revision_dirty": dirty,
            "voicer_counts": {
                voicer: voicer_counts.get(voicer, 0)
                for voicer in _VOICER_IDS
            },
            "voicer_family_counts": {
                genre: {
                    family: counts.get(family, 0)
                    for family in _VOICER_FAMILIES
                }
                for genre, counts in sorted(family_counts_by_genre.items())
            },
            "records": manifest_records,
        }
        temporary_manifest = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.", suffix=".tmp", delete=False,
        )
        temporary_manifest_path = Path(temporary_manifest.name)
        try:
            with temporary_manifest as manifest_file:
                json.dump(manifest, manifest_file, indent=2, sort_keys=True)
                manifest_file.write("\n")
        except BaseException:
            if temporary_manifest_path.exists():
                temporary_manifest_path.unlink()
            raise

        def reserve_backup(path: Path) -> Path:
            handle = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".bak", delete=False,
            )
            backup = Path(handle.name)
            handle.close()
            backup.unlink()
            return backup

        output_backup = None
        manifest_backup = None
        output_backed_up = False
        manifest_backed_up = False
        output_installed = False
        manifest_installed = False
        try:
            if output_path.exists():
                output_backup = reserve_backup(output_path)
                os.replace(output_path, output_backup)
                output_backed_up = True
            if manifest_path.exists():
                manifest_backup = reserve_backup(manifest_path)
                os.replace(manifest_path, manifest_backup)
                manifest_backed_up = True
            os.replace(temporary_output_path, output_path)
            output_installed = True
            os.replace(temporary_manifest_path, manifest_path)
            manifest_installed = True
        except BaseException:
            if manifest_installed and manifest_path.exists():
                manifest_path.unlink()
            if output_installed and output_path.exists():
                output_path.unlink()
            if manifest_backed_up and manifest_backup is not None:
                os.replace(manifest_backup, manifest_path)
                manifest_backed_up = False
            if output_backed_up and output_backup is not None:
                os.replace(output_backup, output_path)
                output_backed_up = False
            raise
        finally:
            if temporary_manifest_path.exists():
                temporary_manifest_path.unlink()
            if temporary_output_path.exists():
                temporary_output_path.unlink()
            if manifest_backed_up and manifest_backup is not None \
                    and manifest_backup.exists():
                manifest_backup.unlink()
            if output_backed_up and output_backup is not None \
                    and output_backup.exists():
                output_backup.unlink()
    except BaseException:
        if temporary_output_path.exists():
            temporary_output_path.unlink()
        raise

    print(f"Rendered {len(files)} song(s) to {output}")
    print("Voicer summary:")
    for voicer in _VOICER_IDS:
        print(f"  {voicer}: {voicer_counts.get(voicer, 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in-dir",
        action="extend",
        nargs="+",
        required=True,
        metavar="DIR",
        help="One or more directories containing song_<integer>.json files.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--mode", choices=("pads", "arpeggios"), default="pads")
    args = parser.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    render_directory(args.in_dir, args.out, args.seed, args.mode)


if __name__ == "__main__":
    main()
