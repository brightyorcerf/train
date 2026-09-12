"""Cross-chain bridge boundary (§9.3 BROKEN_AT_BRIDGE).

A bridge is a STOP: the funds leave this chain and following them is cross-chain correlation, which
this prototype does not do. What the operator gets instead is the actionable thing — the bridge
contract, the operator to subpoena, and the chains that bridge serves.

Honest about the destination: a single bridge serves many chains and the destination of any one
transfer lives in that transaction's call data, which we do not decode (Etherscan V2 marks `input`
deprecated). So `destinations` is the bridge's served set, never a per-transfer claim. Saying "went
to Solana" without decoding would be a guess wearing a fact's clothes.
"""
import os
from functools import cache
from pathlib import Path

import yaml

from app.labels.registry import BRIDGE, SOURCE_TIER, Label

REPO = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
BRIDGES = REPO / "labels" / "bridges.yaml"


@cache
def _entries() -> tuple[dict, ...]:
    return tuple(yaml.safe_load(BRIDGES.read_text()) or ())


def labels() -> list[Label]:
    """Every curated bridge address as a BRIDGE-role Label, for the label set (§6)."""
    out = []
    for b in _entries():
        tier = b.get("tier", "curated")
        for a in b.get("addresses") or []:
            prov = f"{b['name']} <- {b['source']}"
            if a.get("note"):
                prov += f" ({' '.join(a['note'].split())})"
            out.append(Label(a["address"], a["chain"], BRIDGE, b["entity"], tier, SOURCE_TIER[tier],
                             "labeled", prov))
    return out


@cache
def entities() -> dict[str, str]:
    return {b["entity"]: b["name"] for b in _entries()}


@cache
def destinations() -> dict[str, list[str]]:
    return {b["entity"]: list(b.get("destinations") or []) for b in _entries()}


def flag(entity: str, name: str, address: str, hop: int, tx_hash: str) -> str:
    """The §9.3 report line: bridge contract + the chains it serves (never a decoded destination)."""
    dests = ", ".join(destinations().get(entity, [])) or "unknown"
    return (f"bridge:{name}@{address}(hop {hop}, tx {tx_hash}) -> serves [{dests}]; "
            f"per-transfer destination not decoded — subpoena the bridge operator")


def _selfcheck():
    ls = labels()
    assert ls, "no bridge labels loaded — labels/bridges.yaml missing or empty"
    seen = {}
    for l in ls:
        assert l.role == BRIDGE and l.chain == "eth"
        assert l.address.startswith("0x") and len(l.address) == 42, l.address
        assert l.address.lower() not in seen, f"duplicate bridge address {l.address}"
        seen[l.address.lower()] = l
        assert "http" in l.provenance, f"{l.address} has no source URL"
    ents = {l.entity for l in ls}
    assert {"wormhole", "stargate", "synapse", "debridge", "multichain", "polygon_pos_bridge"} <= ents, ents
    assert all(destinations()[e] for e in ents)
    f = flag("wormhole", "Wormhole", "0xabc", 2, "0xdef")
    assert f.startswith("bridge:") and "solana" in f and "not decoded" in f
    assert len(ls) >= 18, len(ls)


if __name__ == "__main__":
    _selfcheck()
    print(f"bridge selfcheck PASS — {len(labels())} addresses, {len(destinations())} bridges")
