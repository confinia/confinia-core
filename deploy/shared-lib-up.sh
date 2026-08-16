#!/bin/bash
# Install the vendored front-end libraries into the VM's SHARED directory, so
# every product serves ONE copy instead of carrying its own (issue #185).
#   ./deploy/shared-lib-up.sh
#
# WHY A SHARED DIRECTORY AND NOT A SHARED HOST. The obvious shape for "an
# internal library service" is lib.confinia.io -- and it would be a slower,
# self-hosted CDN with the same defect that pinned MapLibre to v5 for two
# weeks: v6 loads its worker as a module, and cross-origin that worker never
# starts. No error event, nothing in the console, the basemap still draws and
# only the data is missing (maplibre/maplibre-gl-js#8018, still open).
#
# So each product's own edge mounts THIS directory and serves it under its own
# hostname. One copy on disk, one upgrade, every page same-origin.
#
# The directory lives outside the repo mirror on purpose: it is shared state,
# like ~/confinia-edge-state, and a `git reset --hard` must not take another
# product's library with it.
set -eu
cd "$(dirname "$0")/.."
DEST="${SHARED_LIB_DIR:-$HOME/shared-lib}"

installed=0
for src in demo/lib/*/*/; do
	[ -d "$src" ] || continue
	rel=${src#demo/lib/}
	target="$DEST/$rel"
	mkdir -p "$target"
	# --delete inside the VERSIONED directory only: a version is immutable, so
	# anything extra there is a leftover. Never a level up, which is where
	# another product's library would be.
	rsync -a --delete "$src" "$target"
	echo "  installed $rel -> $target"
	installed=$((installed + 1))
done
[ "$installed" -gt 0 ] || { echo "nothing under demo/lib/*/*/ to install" >&2; exit 1; }

# The failure this guards against is silent: a module served as
# application/octet-stream is refused with no visible error, and a missing
# chunk hangs the map while the basemap still renders.
for f in "$DEST"/maplibre/*/; do
	for chunk in maplibre-gl.mjs maplibre-gl-shared.mjs maplibre-gl-worker.mjs; do
		[ -s "$f$chunk" ] || { echo "MISSING $f$chunk -- the map would hang silently" >&2; exit 1; }
	done
done
echo "OK: shared libraries in $DEST"
