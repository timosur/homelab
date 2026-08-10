#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
WORK_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

VALID_HTML="$WORK_DIR/valid.html"
INVALID_HTML="$WORK_DIR/invalid.html"

INPUT_JSON="$ROOT_DIR/tests/valid.json" \
OUTPUT_HTML="$VALID_HTML" \
UNAVAILABLE_HTML="$ROOT_DIR/render/unavailable.html" \
  sh "$ROOT_DIR/render/render.sh"

if ! grep -q "Hauptkontakte / Primary Contacts" "$VALID_HTML"; then
  echo "valid render missing contact section"
  exit 1
fi

if ! grep -q "Google Maps directions" "$VALID_HTML"; then
  echo "valid render missing maps link"
  exit 1
fi

INPUT_JSON="$ROOT_DIR/tests/invalid.json" \
OUTPUT_HTML="$INVALID_HTML" \
UNAVAILABLE_HTML="$ROOT_DIR/render/unavailable.html" \
  sh "$ROOT_DIR/render/render.sh"

if ! grep -q "Notfallinformationen temporaer nicht verfuegbar" "$INVALID_HTML"; then
  echo "invalid render did not fallback to unavailable page"
  exit 1
fi

if grep -q "Hauptkontakte / Primary Contacts" "$INVALID_HTML"; then
  echo "invalid render leaked normal content"
  exit 1
fi

echo "render tests passed"
