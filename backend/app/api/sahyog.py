"""SAHYOG mock (§14, §17) — a documented, callable contract. NEVER a live integration.

The real SAHYOG schema is non-public ("45+ VASPs onboarded", legal basis BNSS §94 + IT Act
§79(3)(b)), so the payload below is ILLUSTRATIVE and labelled as such in its own response. The
deliverable is the contract, not a connection.

THE HONEST PART. Of the entities our label set can attribute to, only four are confirmed
SAHYOG-onboarded (wazirx, kucoin, bybit, bitget). Every endpoint in our golden discovery cases
resolves to binance / bitfinex / huobi — `sahyog: unknown`. So this mock does NOT pretend a
disclosure can be routed through SAHYOG for them: `routable` is false and `route` says to use
MLAT / direct legal process instead. A demo that quietly routed an un-onboarded VASP through the
portal would be the one claim in this project that could not survive scrutiny (§18).
"""
from pydantic import BaseModel, Field

from app.labels.registry import PgRegistry

SCHEMA_NOTE = ("ILLUSTRATIVE schema — the real SAHYOG payload is non-public and conformable under "
               "an MoU. This mock is a documented contract, never a live integration.")
LEGAL_BASIS = "BNSS S.94 + IT Act S.79(3)(b)"


class DisclosureRequest(BaseModel):
    target_vasp: str = Field(description="resolved entity id, e.g. 'binance'")
    suspect_addresses: list[str] = Field(min_length=1)
    transaction_hashes: list[str] = []
    deposit_events: list[dict] = []
    provenance: list[dict] = []
    case_reference: str | None = None


def onboarding(entity_id: str, reg: PgRegistry | None = None) -> dict:
    """Is this entity actually on the SAHYOG portal? The answer is usually no — say so plainly."""
    r = reg or PgRegistry()
    ent = r.entities.get(entity_id)
    confirmed = bool(ent and ent.sahyog == "confirmed")
    return {
        "target_vasp": entity_id,
        "target_vasp_name": ent.name if ent else entity_id,
        "target_vasp_sahyog": ent.sahyog if ent else "unknown",
        "jurisdiction": list(ent.jurisdiction) if ent else [],
        "routable_via_sahyog": confirmed,
        "route": (f"SAHYOG portal ({LEGAL_BASIS})" if confirmed else
                  "NOT on the SAHYOG portal in our label set — route by MLAT / direct legal "
                  f"process to the VASP's jurisdiction, or confirm onboarding out of band"),
    }


def disclosure_payload(result: dict, case_reference: str | None = None,
                       reg: PgRegistry | None = None) -> dict:
    """§17 payload, built from a finished attribution result. Emits the crowned target only —
    if the engine abstained (ambiguous / unattributed) there is no target to name, and the
    payload says that instead of inventing one."""
    rec = result.get("recommended")
    if not rec:
        return {"schema_note": SCHEMA_NOTE, "legal_basis": LEGAL_BASIS, "target_vasp": None,
                "routable_via_sahyog": False,
                "route": "no disclosure request: the engine did not crown a target "
                         f"({result.get('state', 'UNATTRIBUTED')}) — {result.get('rationale', '')}",
                "suspect_addresses": [result["wallet"]] if result.get("wallet") else [],
                "transaction_hashes": [], "deposit_events": [], "provenance": []}

    top = next((c for c in result.get("vasp_candidates", []) if c["entity"] == rec), None) or {}
    near = top.get("nearest") or result.get("nearest") or {}
    path = near.get("path") or result.get("path") or []
    dep = near.get("deposit_event") or result.get("deposit_event")
    pins = result.get("pins", {})
    return {
        "schema_note": SCHEMA_NOTE,
        "legal_basis": LEGAL_BASIS,
        **onboarding(rec, reg),
        "case_reference": case_reference,
        "suspect_addresses": [result["wallet"]],
        "endpoint_address": near.get("endpoint"),
        "transaction_hashes": [p["tx"] for p in path if p.get("tx")],
        "deposit_events": [dep] if dep else [],
        "attribution": {"confidence_index": top.get("score"), "of": 100,
                        "separation": result.get("separation"),
                        "hops": near.get("hops"), "role_basis": near.get("role_basis"),
                        "rationale": result.get("rationale")},
        "provenance": provenance(result),
        "reproduce": {k: pins.get(k) for k in
                      ("snapshot_block", "label_set_version", "weight_hash", "adapter_version")},
        "disclaimer": ("Investigative lead, not identity and not evidence. Identifying the account "
                       "holder behind the endpoint is the VASP's KYC under a lawful request."),
    }


def provenance(result: dict) -> list[dict]:
    """The §13 ProvenanceCard rows: where every claim came from, per candidate endpoint."""
    out = []
    for c in result.get("vasp_candidates", []):
        n = c.get("nearest") or {}
        out.append({"entity": c["entity"], "entity_name": c.get("entity_name"),
                    "sahyog": c.get("sahyog"), "endpoint": n.get("endpoint"),
                    "role_basis": n.get("role_basis"), "hops": n.get("hops"),
                    "label_source": n.get("label_source"), "tier": n.get("tier"),
                    "confidence_index": c.get("score"), "n_paths": c.get("n_paths"),
                    "deposit_event": n.get("deposit_event")})
    return out
