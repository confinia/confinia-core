#!/bin/bash
# Watch for the ISTAT Serie Storiche population page to come back (issue #91).
#
# The spike is finished: geography basis, successor routing, dated events and
# licence are all settled, and none of them blocks anything. What blocks is the
# FILE. Two distribution routes, both measured 2026-08-22:
#
#   SDMX  DF_BULK_CPO2011_DS_SSP_TEST -- metadata 200, data 404. The flow is
#         declared and not published; the _TEST suffix meant what it said.
#   Web   the Serie Storiche "Popolazione" category page returns HTTP 500,
#         while the portal root and other files on the same server return 200.
#         An ISTAT-side breakage on that page, not something to work around.
#
# So: check, and say plainly whether it is still broken. This is TEMPORARY --
# delete it, and its timer, the day the ingestion lands. A watcher left running
# after its reason has gone is just noise that people learn to ignore, which is
# how the bounce mailbox stopped being read.
set -euo pipefail
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
PAGE="https://seriestoriche.istat.it/index.php?id=1&tx_usercento_centofe%5Bcategoria%5D=2&tx_usercento_centofe%5Baction%5D=show&tx_usercento_centofe%5Bcontroller%5D=Categoria"
ROOT="https://seriestoriche.istat.it/"

root_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 40 -A "$UA" -L "$ROOT" || echo 000)
page_code=$(curl -s -o /tmp/istat-page.$$ -w "%{http_code}" --max-time 40 -A "$UA" -L "$PAGE" || echo 000)
# grep finding nothing exits 1, and under `pipefail` that fails the whole
# pipeline -- the same trap that made the backup script reject a good dump.
# "no files listed" is the EXPECTED answer here, not an error.
files=$( { grep -oiE 'href="[^"]+\.(xls|xlsx|csv|zip)"' /tmp/istat-page.$$ || true; } 2>/dev/null | wc -l | tr -d ' ')
rm -f /tmp/istat-page.$$

echo "portal root: HTTP $root_code · population page: HTTP $page_code · downloadable files listed: $files"
if [ "$page_code" = "200" ] && [ "$files" -gt 0 ]; then
	echo "AVAILABLE: the page is back and lists $files file(s) — issue #91 can proceed"
	exit 0
fi
# Still broken is the expected state, not a failure of this machine: exit 0 so a
# timer does not page anyone. The message is the product, not the exit code.
echo "still unavailable — nothing to do yet"
