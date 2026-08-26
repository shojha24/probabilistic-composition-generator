# EDA and data preparation

## Purpose

The `eda` directory contains data preparation, inspection, and analysis
scripts.

The main data path is:

```text
data/
  |
  v
eda/normalize_datasets.py
  |
  v
data_normalized/
  |
  v
extract_distributions.py
  |
  v
distributions/
```

The scripts in `eda` do not generate voicings. They prepare and inspect the
source data that the chord generator uses.

## Create `data_normalized`

`normalize_datasets.py` is the main normalization script.

It:

1. Finds all `.jams` files below the repository `data/` directory.
2. Reads each file as JSON.
3. Clamps negative observation times to `0.0`.
4. Clamps negative observation durations to `0.0`.
5. Normalizes chord root spellings.
6. Converts enharmonic spellings to standard pitch names.
7. Removes `*1` root-omission markers.
8. Removes `*` from explicit omission markers such as `*3` and `*5`.
9. Converts interval clusters to a structural chord shorthand.
10. Writes the result under `data_normalized/`.

The script preserves the directory structure below `data/`.

Run it from the repository root:

```bash
python eda/normalize_datasets.py
```

The output is:

```text
data_normalized/<same relative path>.jams
```

The script writes files even when no change is required. This creates one
complete normalized tree for downstream tools.

If a JSON file cannot be read, the script reports it and continues.
If an output file cannot be written, the script records its relative path in:

```text
eda/corrupt_files_log.txt
```

## Extract training distributions

After normalization, run:

```bash
python extract_distributions.py \
  --data-root ./data_normalized \
  --out-dir ./distributions
```

The extractor reads normalized JAMS files. It creates Stage 1, Stage 2, and
Stage 3 count tables for `pop_rock` and `jazz`.

The extractor is the bridge from normalized files to chord generation.
`chord_gen.py` then reads the generated tables.

Do not point the extractor at raw `data/` when the raw files contain known
bad timestamps or inconsistent chord labels. Normalize first.

## Chord normalization helpers

### `normalize_chords.py`

This script works on the CSV chord-count artifact:

```text
eda/chord_dataset_counts.csv
```

It writes:

```text
eda/chord_dataset_counts_fixed.csv
```

It applies the same main chord-label rules as the JAMS normalizer. It is
useful for CSV inspection and comparison. It does not create
`data_normalized/`.

### `CHORD_GEN_DOCUMENTATION.md`

This document describes the chord-generation data model and the older
extraction workflow. Use the root `README.md` for the current end-to-end
pipeline.

## Inspection and analysis scripts

### `count_files.py`

Counts files in the dataset tree. Use it to check source and normalized file
counts.

### `read_keys.py`

Reads key annotations from JAMS files. Use it to inspect key coverage and
key-mode values.

### `find_missing_keys.py`

Finds files or records that do not contain usable key information.

### `find_skipped_keys.py`

Uses extraction-style parsing to report keys that the data pipeline skips.
Use it to compare source annotations with extraction behavior.

### `find_modulations.py`

Scans JAMS annotations for key changes and reports possible modulations.

### `get_all_chords.py`

Collects chord labels from source JAMS files and writes a CSV summary.
Use it to inspect raw chord vocabulary.

### `get_all_norm_chords.py`

Collects chord labels from normalized JAMS files and writes a CSV summary.
Use it to inspect the vocabulary after normalization.

### `analyze_transitions.py`

Reads chord data and reports transition behavior. Use it to inspect
successive-chord patterns before or after distribution extraction.

### `check_bass_freq.py`

Reads Stage 2 bass distributions and reports bass-frequency behavior.
Run it after `extract_distributions.py`.

### `check_later.py`

Provides a later-stage inspection helper for dataset analysis. Read the
script's local options and input paths before use.

### `combine_labs.py`

Combines `.lab` files into one text file or converts each `.lab` file to a
separate `.txt` file.

Examples:

```bash
python eda/combine_labs.py ./labels --output combined.txt
python eda/combine_labs.py ./labels --recursive --mode per-file \
  --output-dir ./label_text
```

This script is a file-format helper. It is not part of the JAMS
normalization-to-distribution path.

## CSV artifacts

These files are analysis outputs or intermediate snapshots:

- `chord_dataset_counts.csv`: raw chord-count table;
- `chord_dataset_counts_fixed.csv`: CSV after structural label fixes;
- `chord_dataset_normalized_counts.csv`: normalized count snapshot.

The CSV files do not replace the normalized JAMS tree. The current extractor
uses normalized JAMS files.

## Recommended order

Use this order for a new dataset:

1. Place raw JAMS files under `data/`.
2. Run `python eda/normalize_datasets.py`.
3. Inspect counts with `count_files.py`.
4. Inspect keys and skipped records with the key scripts.
5. Inspect raw and normalized chord vocabulary.
6. Run `python extract_distributions.py`.
7. Inspect transitions and bass frequencies.
8. Run `python chord_gen.py`.

Keep raw data and normalized data separate. Do not edit normalized files by
hand. Change the normalization rule and regenerate the tree when a rule
needs to change.

## Important limits

Normalization is structural cleanup. It does not prove that a chord label is
musically correct.

The scripts can preserve a label when the parser cannot safely classify it.
The extraction report remains the authority for skipped or malformed input.

The next planned stage is JFugue integration. That stage starts after chord
events and voicings exist. It is separate from the EDA preparation path.
