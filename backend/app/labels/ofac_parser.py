"""OFAC SDN → labels/ofac_sdn_crypto.csv (every "Digital Currency Address - *" feature).

Parses sdn_advanced.xml rather than sdn.csv: sdn.csv packs all addresses into a free-text
remarks field; the advanced XML carries them as typed features.

    python -m app.labels.ofac_parser [--xml path] [--out path]     (downloads if --xml absent)
"""
import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML"
REPO = Path(__file__).resolve().parents[3]
FIELDS = ["address", "chain", "entity_name", "category", "date_added", "source"]
CHAIN = {"XBT": "btc", "ETH": "eth", "TRX": "tron", "BSC": "bsc", "ARB": "arb"}
EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def chain_for(code: str, addr: str) -> str:
    if code in ("USDT", "USDC"):  # token listings: chain is implied by address format
        return "eth" if addr.startswith("0x") else "tron" if addr.startswith("T") else code.lower()
    return CHAIN.get(code, code.lower())


def _date(el, ns):
    y, m, d = (el.findtext(f"{ns}{k}") for k in ("Year", "Month", "Day"))
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def parse(xml_path: Path) -> list[dict]:
    ns = ""
    feature_code: dict[str, str] = {}   # FeatureTypeID -> "XBT"
    party_type: dict[str, str] = {}     # PartySubTypeID -> "individual"/"entity"/...
    type_name: dict[str, str] = {}      # PartyTypeID -> name
    subtype_parent: dict[str, str] = {}
    parties: dict[str, dict] = {}       # ProfileID -> {name, type, addrs}
    entries: dict[str, dict] = {}       # ProfileID -> {date, programs}
    issued = ""

    for event, el in ET.iterparse(xml_path, events=("end",)):
        tag = el.tag.split("}")[-1]
        if not ns and "}" in el.tag:
            ns = el.tag.split("}")[0] + "}"
        if tag == "DateOfIssue":
            issued = _date(el, ns)
        elif tag == "FeatureType" and (el.text or "").startswith("Digital Currency Address - "):
            feature_code[el.get("ID")] = el.text.rsplit("- ", 1)[1].strip()
        elif tag == "PartySubType":
            subtype_parent[el.get("ID")] = el.get("PartyTypeID")
        elif tag == "PartyType":
            type_name[el.get("ID")] = el.text.lower()
        elif tag == "DistinctParty":
            profile = el.find(f"{ns}Profile")
            addrs = []
            for f in profile.iter(f"{ns}Feature"):
                code = feature_code.get(f.get("FeatureTypeID"))
                if code:
                    addr = (f.findtext(f"{ns}FeatureVersion/{ns}VersionDetail") or "").strip()
                    if addr:
                        addrs.append((code, addr))
            if addrs:
                alias = next(a for a in profile.iter(f"{ns}Alias") if a.get("Primary") == "true")
                parts = alias.find(f"{ns}DocumentedName").iter(f"{ns}NamePartValue")
                if not party_type:
                    party_type = {k: type_name.get(v, "?") for k, v in subtype_parent.items()}
                parties[profile.get("ID")] = {
                    "name": " ".join(p.text for p in parts),
                    "type": party_type.get(profile.get("PartySubTypeID"), "?"),
                    "addrs": addrs,
                }
            el.clear()
        elif tag == "SanctionsEntry":
            pid = el.get("ProfileID")
            if pid in parties:
                dates = [_date(d, ns) for d in el.iter(f"{ns}Date")]
                progs = [c.text for m in el.iter(f"{ns}SanctionsMeasure")
                         for c in m.iter(f"{ns}Comment") if c.text]
                entries[pid] = {"date": min(dates) if dates else "", "programs": progs}
            el.clear()

    source = f"OFAC SDN sdn_advanced.xml (issued {issued})"
    rows = []
    for pid, p in parties.items():
        e = entries.get(pid, {"date": "", "programs": []})
        for code, addr in p["addrs"]:
            rows.append({
                "address": addr,
                "chain": chain_for(code, addr),
                "entity_name": p["name"],
                "category": f"{p['type']}:{'|'.join(e['programs'])}",
                "date_added": e["date"],  # party's listing date; OFAC doesn't date individual addresses
                "source": source,
            })
    # One address can be listed under two currency features (e.g. ETH and USDT) — keep one row.
    rows = list({(r["address"], r["chain"], r["entity_name"]): r for r in rows}.values())
    rows.sort(key=lambda r: (r["chain"], r["address"].lower()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", type=Path)
    ap.add_argument("--out", type=Path, default=REPO / "labels" / "ofac_sdn_crypto.csv")
    a = ap.parse_args()
    xml = a.xml
    if not xml:
        xml = REPO / "vendor" / "sdn_advanced.xml"
        xml.parent.mkdir(exist_ok=True)
        print(f"downloading {URL} ...")
        with httpx.stream("GET", URL, follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            with open(xml, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    rows = parse(xml)
    evm_bad = [r["address"] for r in rows if r["address"].startswith("0x") and not EVM_RE.match(r["address"])]
    ok = len(rows) > 0 and not evm_bad and all(r["entity_name"] and r["date_added"] for r in rows)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    by_chain: dict[str, int] = {}
    for r in rows:
        by_chain[r["chain"]] = by_chain.get(r["chain"], 0) + 1
    print(f"{len(rows)} addresses, {len({r['entity_name'] for r in rows})} parties -> {a.out}")
    print("by chain:", dict(sorted(by_chain.items(), key=lambda kv: -kv[1])))
    if evm_bad:
        print("malformed EVM addresses:", evm_bad[:5])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
