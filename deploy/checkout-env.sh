#!/bin/bash
# Point an environment's checkout at a commit (issue #111).
#
#   ./deploy/checkout-env.sh staging main
#   ./deploy/checkout-env.sh sandbox <branch-or-sha>
#
# staging and sandbox each have their OWN checkout, and caddy serves their
# static files from it. Until 2026-08-12 all three hostnames served the
# production directory, so editing ./demo or ./deploy/site changed www
# immediately and there was no way to validate a static change first -- which is
# what took the map down on 2026-08-03 and the ⚠️ RULES 13 still warns about.
#
# Deliberately refuses to touch the production checkout: that one is driven by
# the deploy workflow, and a second writer is how the two get out of step.
set -eu
ENV="${1:?usage: checkout-env.sh <staging|sandbox> <ref>}"
REF="${2:?usage: checkout-env.sh <staging|sandbox> <ref>}"

case "$ENV" in
	staging|sandbox) ;;
	*) echo "REFUSING: '$ENV' is not staging or sandbox. The production checkout" >&2
	   echo "  is driven by the deploy workflow and must have one writer." >&2
	   exit 2 ;;
esac

DIR="$HOME/$ENV/confinia"
[ -d "$DIR/.git" ] || { echo "no checkout at $DIR" >&2; exit 1; }

cd "$DIR"
git fetch -q origin "$REF" || git fetch -q origin
git reset --hard -q "$(git rev-parse FETCH_HEAD 2>/dev/null || echo "origin/$REF")"
echo "$ENV -> $(git log --oneline -1)"
echo "  static files now served for $ENV.confinia.io from $DIR"
