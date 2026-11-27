#!/usr/bin/env bash
# Runs the importer tests against the PresentationEngine contracts.
#   ENGINE_DIR  path to a PresentationEngine checkout (default: ../PresentationEngine)
#   PP7GEN      optional path to a pp7gen checkout for the real-fixture checks
# Requires: pip install grpcio-tools jsonschema
set -euo pipefail
cd "$(dirname "$0")/.."
ENGINE_DIR="${ENGINE_DIR:-../PresentationEngine}"
export ENGINE_DIR="$(cd "$ENGINE_DIR" && pwd)"
OUT="${TMPDIR:-/tmp}/presentation-macui-gen"
rm -rf "$OUT" && mkdir -p "$OUT"
python3 -m grpc_tools.protoc -I "$ENGINE_DIR/proto" --python_out="$OUT" \
  "$ENGINE_DIR"/proto/engine/v1/*.proto "$ENGINE_DIR"/proto/library/v1/*.proto "$ENGINE_DIR"/proto/importer/v1/*.proto
python3 importers/core.office-import/tests/test_office_import.py "$OUT"
if [[ -n "${PP7GEN:-}" ]]; then
  python3 importers/core.office-import/tests/verify_against_pp7gen.py "$PP7GEN" | tail -3
fi
