#!/bin/sh
# Upserts every persona file (Markdown with YAML-like front matter) into the Dark Factory database.
# Personas are matched by name. Personas removed from Git are left in the database: tasks
# reference them, so delete them in the UI if needed.
set -eu

field() {
  awk -v key="$1" '
    NR == 1 && $0 == "---" { inside = 1; next }
    inside && $0 == "---" { exit }
    inside {
      i = index($0, ":")
      if (i > 0 && substr($0, 1, i - 1) == key) {
        value = substr($0, i + 1)
        sub(/^[ \t]+/, "", value)
        print value
      }
    }' "$2"
}

body() {
  awk 'NR == 1 && $0 == "---" { fm = 1; next } fm == 1 && $0 == "---" { fm = 2; next } fm == 2' "$1"
}

count=0
for file in /personas/*.md; do
  name="$(field name "$file")"
  description="$(field description "$file")"
  harness="$(field harness "$file")"
  provider="$(field provider "$file")"
  model="$(field model "$file")"
  wire_api="$(field wire_api "$file")"
  context="$(field context "$file")"
  max_output="$(field max_output "$file")"
  prompt_from="$(field prompt_from "$file")"
  if [ -n "$prompt_from" ]; then
    prompt="$(body "/personas/$prompt_from")"
  else
    prompt="$(body "$file")"
  fi
  if { [ -n "$context" ] && [ -z "$max_output" ]; } || { [ -z "$context" ] && [ -n "$max_output" ]; }; then
    echo "$file: set context and max_output together" >&2
    exit 1
  fi
  for required in name harness provider model prompt; do
    eval "value=\${$required}"
    if [ -z "$value" ]; then
      echo "$file: missing $required" >&2
      exit 1
    fi
  done

  psql --no-psqlrc --quiet \
    -v ON_ERROR_STOP=1 \
    -v name="$name" \
    -v description="$description" \
    -v harness="$harness" \
    -v provider="$provider" \
    -v model="$model" \
    -v wire_api="${wire_api:-anthropic-messages}" \
    -v context="$context" \
    -v max_output="$max_output" \
    -v prompt="$prompt" \
    -f /sync/upsert.sql
  echo "synced persona: $name ($harness, $model)"
  count=$((count + 1))
done
echo "synced $count personas"
