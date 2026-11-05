#!/bin/sh
# Download a model file once into the shared models volume and verify its size and SHA-256.
# Usage: fetch-model.sh DESTINATION URL SHA256 SIZE_BYTES
set -eu

destination=$1
url=$2
expected_sha=$3
expected_size=$4

verify() {
  [ "$(wc -c < "$1" | tr -d ' ')" = "$expected_size" ] &&
    [ "$(sha256sum "$1" | cut -d ' ' -f 1)" = "$expected_sha" ]
}

if [ -f "$destination" ] && verify "$destination"; then
  echo "verified existing $destination"
  exit 0
fi

partial="$destination.part"
rm -f "$partial"
echo "downloading $url"
curl --fail --location --silent --show-error --retry 5 --retry-delay 5 --connect-timeout 30 \
  --output "$partial" "$url"
if ! verify "$partial"; then
  rm -f "$partial"
  echo "checksum or size mismatch for $url" >&2
  exit 1
fi
mv "$partial" "$destination"
echo "verified $destination"
