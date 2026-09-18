#!/usr/bin/env sh
# Fetch the two label inputs that are too large to vendor in git (~165 MB total).
# app/labels/ingest.py reads both; the label-set version hash is derived from them, so the pinned
# TagPacks commit matters — a different commit is a different label set, by design (§12).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENDOR="$ROOT/vendor"
PACKS="$VENDOR/graphsense-tagpacks"
COMMIT=7f9a5d1f                 # inspected 2026-09-11, see scripts/tagpacks_inspection.md
SDN=https://sanctionslistservice.ofac.treas.gov/api/download/sdn_advanced.xml

mkdir -p "$VENDOR"

if [ -d "$PACKS/.git" ]; then
    echo "tagpacks: present, checking out $COMMIT"
    git -C "$PACKS" fetch --quiet origin && git -C "$PACKS" checkout --quiet "$COMMIT"
else
    echo "tagpacks: cloning into $PACKS"
    git clone --quiet https://github.com/graphsense/graphsense-tagpacks.git "$PACKS"
    git -C "$PACKS" checkout --quiet "$COMMIT"
fi

if [ -s "$VENDOR/sdn_advanced.xml" ]; then
    echo "OFAC SDN: present ($(du -h "$VENDOR/sdn_advanced.xml" | cut -f1))"
else
    # System curl rejects this host's chain on some machines; python+certifi is the reliable path.
    echo "OFAC SDN: downloading"
    python3 - "$SDN" "$VENDOR/sdn_advanced.xml" <<'PY'
import sys, httpx, certifi
url, out = sys.argv[1], sys.argv[2]
with httpx.stream("GET", url, verify=certifi.where(), timeout=120, follow_redirects=True) as r:
    r.raise_for_status()
    with open(out, "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)
print("saved", out)
PY
fi

echo
echo "vendor ready. Next: docker compose exec api python -m app.labels.ingest"
