#!/bin/bash
#===============================================================================
# Gaussian Worker Wrapper Script
# ===============================
#
# Purpose: Safely run Gaussian calculations with proper resource management
# Features:
#   - Isolated scratch directory for each job
#   - Automatic cleanup on exit
#   - Disk space checking
#   - Error handling
#
# Usage: run_g16_worker.sh <input.gjf> <output.log>
#
# Author: QCcalc Team (adapted from RPH)
#===============================================================================

set -euo pipefail

INPUT_SRC="${1:-}"
OUTPUT_DST="${2:-}"

if [[ -z "$INPUT_SRC" || -z "$OUTPUT_DST" ]]; then
    echo "Usage: run_g16_worker.sh <input.gjf> <output.log>"
    exit 1
fi

if [[ ! -f "$INPUT_SRC" ]]; then
    echo "ERROR: Input file not found: $INPUT_SRC"
    exit 1
fi

INPUT_NAME="$(basename "$INPUT_SRC")"
OUTPUT_NAME="$(basename "$OUTPUT_DST")"

GAUSSIAN_ROOT="${GAUSSIAN_ROOT:-/opt/software/gaussian/g16}"
GAUSS_EXEDIR="${GAUSS_EXEDIR:-$GAUSSIAN_ROOT}"
export GAUSS_EXEDIR

GAUSS_SCRDIR="${GAUSS_SCRDIR:-$(mktemp -d /tmp/gaussian_scratch_XXXXXX)}"
GAUSS_TMPDIR="${GAUSS_TMPDIR:-$GAUSS_SCRDIR}"

mkdir -p "$GAUSS_SCRDIR"
if [[ "$GAUSS_TMPDIR" != "$GAUSS_SCRDIR" ]]; then
    mkdir -p "$GAUSS_TMPDIR"
fi

cleanup() {
    local exit_code=$?
    rm -rf "$GAUSS_SCRDIR" 2>/dev/null || true
    if [[ -n "$GAUSS_TMPDIR" && "$GAUSS_TMPDIR" != "$GAUSS_SCRDIR" ]]; then
        rm -rf "$GAUSS_TMPDIR" 2>/dev/null || true
    fi
    return $exit_code
}
trap cleanup EXIT INT TERM

check_disk_space() {
    local min_space_mb=2048
    local available
    available=$(df -m "$GAUSS_SCRDIR" | awk 'NR==2 {print $4}')
    if [[ -n "$available" && "$available" -lt "$min_space_mb" ]]; then
        echo "ERROR: Insufficient disk space in $GAUSS_SCRDIR (${available}MB available, ${min_space_mb}MB required)"
        exit 1
    fi
}

check_disk_space

cp "$INPUT_SRC" "$GAUSS_TMPDIR/$INPUT_NAME"
cd "$GAUSS_TMPDIR"

"${GAUSS_EXEDIR}/g16" < "$INPUT_NAME" > "$OUTPUT_NAME" 2>&1

exit_code=$?

if [[ -f "$GAUSS_TMPDIR/$OUTPUT_NAME" ]]; then
    OUTPUT_DIR="$(dirname "$OUTPUT_DST")"
    mkdir -p "$OUTPUT_DIR" 2>/dev/null || true
    mv "$GAUSS_TMPDIR/$OUTPUT_NAME" "$OUTPUT_DST" 2>/dev/null || true
fi

exit $exit_code
