#!/usr/bin/env bash
# Offline checks for fetch-model.sh using file:// URLs.
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/scripts/fetch-model.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

printf 'synthetic model' > "$work/source.gguf"
sha=$(sha256sum "$work/source.gguf" | cut -d ' ' -f 1)
size=$(wc -c < "$work/source.gguf" | tr -d ' ')

sh "$script" "$work/model.gguf" "file://$work/source.gguf" "$sha" "$size" | grep -q '^verified /'
cmp "$work/source.gguf" "$work/model.gguf"

rm "$work/source.gguf"
sh "$script" "$work/model.gguf" "file://$work/missing.gguf" "$sha" "$size" | grep -q '^verified existing'

printf 'tampered' > "$work/source.gguf"
rm "$work/model.gguf"
if sh "$script" "$work/model.gguf" "file://$work/source.gguf" "$sha" "$size" 2> "$work/error"; then
  echo "expected checksum failure" >&2
  exit 1
fi
grep -q 'mismatch' "$work/error"
[ ! -e "$work/model.gguf" ] && [ ! -e "$work/model.gguf.part" ]
echo "fetch-model tests passed"
