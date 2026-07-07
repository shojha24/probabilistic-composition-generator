#!/usr/bin/env python3
"""
combine_lab_files.py

Combines .lab annotation files (e.g. MIR chord/beat/segment labels) by
concatenating their raw text content, and writes the result out as .txt.

Usage examples:
    # Combine every .lab file in a folder into one merged .txt file
    python combine_lab_files.py /path/to/lab_folder --output combined.txt

    # Recurse into subfolders too
    python combine_lab_files.py /path/to/lab_folder --output combined.txt --recursive

    # Instead of one merged file, just convert each .lab to its own .txt
    # (same content, new extension) in an output folder
    python combine_lab_files.py /path/to/lab_folder --mode per-file --output-dir txt_out

    # Explicit file list instead of a folder
    python combine_lab_files.py file1.lab file2.lab file3.lab --output combined.txt
"""

import argparse
import sys
from pathlib import Path


def find_lab_files(paths, recursive=False):
    """Resolve input paths (files or folders) into a sorted list of .lab files."""
    lab_files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            pattern = "**/*.lab" if recursive else "*.lab"
            lab_files.extend(sorted(p.glob(pattern)))
        elif p.is_file():
            lab_files.append(p)
        else:
            print(f"Warning: '{p}' not found, skipping.", file=sys.stderr)
    # de-dupe while preserving order
    seen = set()
    unique = []
    for f in lab_files:
        if f.resolve() not in seen:
            seen.add(f.resolve())
            unique.append(f)
    return unique


def combine_into_one(lab_files, output_path, add_headers=True):
    """Concatenate raw content of all lab files into a single .txt file."""
    with open(output_path, "w", encoding="utf-8") as out:
        for i, f in enumerate(lab_files):
            content = f.read_text(encoding="utf-8", errors="replace")
            if add_headers:
                out.write(f"# --- {f.name} ---\n")
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            if add_headers and i != len(lab_files) - 1:
                out.write("\n")
    print(f"Combined {len(lab_files)} .lab file(s) into: {output_path}")


def convert_per_file(lab_files, output_dir):
    """Write each .lab file's content out as its own .txt file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for f in lab_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        out_path = output_dir / (f.stem + ".txt")
        out_path.write_text(content, encoding="utf-8")
    print(f"Converted {len(lab_files)} .lab file(s) to .txt in: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Combine or convert .lab files to .txt")
    parser.add_argument("inputs", nargs="+", help="Folder(s) and/or .lab file(s) to process")
    parser.add_argument(
        "--mode",
        choices=["combine", "per-file"],
        default="combine",
        help="'combine' merges all files into one .txt (default); "
             "'per-file' converts each .lab to its own .txt",
    )
    parser.add_argument("--output", default="combined.txt", help="Output .txt path (combine mode)")
    parser.add_argument("--output-dir", default="txt_output", help="Output folder (per-file mode)")
    parser.add_argument("--recursive", action="store_true", help="Search subfolders for .lab files")
    parser.add_argument(
        "--no-headers",
        action="store_true",
        help="Combine mode only: omit '# --- filename ---' separators between files",
    )
    args = parser.parse_args()

    lab_files = find_lab_files(args.inputs, recursive=args.recursive)
    if not lab_files:
        print("No .lab files found.", file=sys.stderr)
        sys.exit(1)

    if args.mode == "combine":
        combine_into_one(lab_files, args.output, add_headers=not args.no_headers)
    else:
        convert_per_file(lab_files, args.output_dir)


if __name__ == "__main__":
    main()