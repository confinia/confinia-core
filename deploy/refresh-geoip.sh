#!/bin/sh
# Rafraîchit la base GeoIP pays (DB-IP Country Lite, CC BY 4.0) — mensuel.
# Cron (VM) : 12 4 3 * * $HOME/projects/confinia/deploy/refresh-geoip.sh
set -eu
DIR="$HOME/projects/confinia/data/geoip"
YM=$(date +%Y-%m)
mkdir -p "$DIR"
TMP="$DIR/dbip.$YM.mmdb.gz"
wget -q -O "$TMP" "https://download.db-ip.com/free/dbip-country-lite-$YM.mmdb.gz" || exit 0
gunzip -f "$TMP"
mv "$DIR/dbip.$YM.mmdb" "$DIR/dbip-country-lite.mmdb"
# L'API charge la base au démarrage : redémarrage doux du conteneur.
podman restart confinia_api_1 >/dev/null 2>&1 || true
echo "$(date -Is) geoip refreshed ($YM)" >> "$DIR/refresh.log"
