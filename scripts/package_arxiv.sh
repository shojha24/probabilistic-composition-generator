#!/usr/bin/env bash
set -euo pipefail

# Ensure TinyTeX is in PATH if installed in user home
if [ -d "$HOME/.TinyTeX/bin" ]; then
  export PATH="$HOME/.local/bin:$(echo "$HOME"/.TinyTeX/bin/*):$PATH"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAPER_DIR="${REPO_ROOT}/paper"
BUILD_DIR="${REPO_ROOT}/build_arxiv"
ARCHIVE_NAME="arxiv_submission.tar.gz"

echo "=== [1/6] Creating clean staging directory: ${BUILD_DIR} ==="
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/figures"

echo "=== [2/6] Flattening paper/main.tex and stripping % comments ==="
python3 - "${PAPER_DIR}" "${BUILD_DIR}/main.tex" << 'PYEOF'
import os
import re
import sys

paper_dir = sys.argv[1]
out_path = sys.argv[2]

input_pattern = re.compile(r'\\input\{([^}]+)\}')

def flatten_tex(rel_path):
    full_path = os.path.join(paper_dir, rel_path)
    if not os.path.exists(full_path) and not full_path.endswith(".tex"):
        full_path += ".tex"
    with open(full_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out_lines = []
    for line in lines:
        # Remove full-line comments completely (avoiding accidental paragraph breaks)
        if re.match(r'^\s*%', line):
            continue
        # Strip unescaped inline comments
        line_clean = re.sub(r'(?<!\\)%.*$', '', line)
        # Resolve \input{...} recursively
        m = input_pattern.search(line_clean)
        if m:
            sub_rel = m.group(1)
            inlined = flatten_tex(sub_rel)
            out_lines.append(inlined.rstrip("\n") + "\n")
        else:
            out_lines.append(line_clean)
    return "".join(out_lines)

flattened = flatten_tex("main.tex")
# Collapse 3+ consecutive blank lines into at most 2 newlines
flattened = re.sub(r'\n{3,}', '\n\n', flattened).strip() + "\n"

with open(out_path, "w", encoding="utf-8") as f:
    f.write(flattened)
PYEOF

echo "=== [3/6] Copying pre-compiled main.bbl to ${BUILD_DIR}/main.bbl ==="
grep -v '^%' "${PAPER_DIR}/main.bbl" > "${BUILD_DIR}/main.bbl"

echo "=== [4/6] Copying style files and vector figures ==="
cp "${PAPER_DIR}/ismir.sty" "${BUILD_DIR}/"
[ -f "${PAPER_DIR}/cite.sty" ] && cp "${PAPER_DIR}/cite.sty" "${BUILD_DIR}/"
[ -f "${PAPER_DIR}/cc_by.pdf" ] && cp "${PAPER_DIR}/cc_by.pdf" "${BUILD_DIR}/"
cp "${PAPER_DIR}/figures/fig1_chord_distribution.pdf" "${BUILD_DIR}/figures/"
cp "${PAPER_DIR}/figures/fig2_per_class_recall_delta.pdf" "${BUILD_DIR}/figures/"

echo "=== [5/6] Testing standalone compilation inside ${BUILD_DIR} (pdflatex ONLY, no bibtex) ==="
pushd "${BUILD_DIR}" > /dev/null
pdflatex -interaction=nonstopmode main.tex > /dev/null
pdflatex -interaction=nonstopmode main.tex > /dev/null

if grep -E "LaTeX Warning: (Reference|Citation).*undefined" main.log; then
  echo "ERROR: Undefined references or citations found in standalone build!" >&2
  exit 1
fi
echo "Standalone compilation succeeded (zero undefined references/citations)."
popd > /dev/null

echo "=== [6/6] Cleaning temporary build artifacts and packaging ${ARCHIVE_NAME} ==="
rm -f "${BUILD_DIR}"/*.aux "${BUILD_DIR}"/*.log "${BUILD_DIR}"/*.out "${BUILD_DIR}/main.pdf"
tar -czf "${REPO_ROOT}/${ARCHIVE_NAME}" -C "${BUILD_DIR}" .
cp "${REPO_ROOT}/${ARCHIVE_NAME}" "${PAPER_DIR}/${ARCHIVE_NAME}"

echo "=== Contents of ${ARCHIVE_NAME} ==="
tar -ztvf "${REPO_ROOT}/${ARCHIVE_NAME}"
