"""PDF report (§13 ReportButton) — the artifact an investigator files.

Rendered from the finished trace ONLY: every number on the page is one the API already returned,
and the four determinism pins (§12) are printed on the face so a reader can reproduce the run.

Nothing is written to disk. No writable volume is mounted on the api container (labels/ and
vendor/ are both :ro), so the PDF streams from memory and `reports` records its content hash
rather than a `pdf_path` that would point at a container-local file nobody can fetch.

The routing section prints BOTH branches on purpose (§17). The crowned VASP's real route, and the
contrast branch, both computed from the same `vasp` registry: an onboarded VASP fires the SAHYOG
portal under BNSS S.94, everyone else gets the honest MLAT boundary. Neither branch is a mock-up
of the other — a demo that quietly routed an un-onboarded VASP through the portal would be the one
claim in this project that could not survive scrutiny (§18).
"""
import hashlib
import html as _html

from app.api import sahyog as sahyog_mock
from app.db import connect
from app.labels.registry import PgRegistry


def _like(s: str) -> str:
    """Escape LIKE wildcards: an address is matched literally, never as a pattern."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def response_hashes(r: dict, limit: int = 12) -> list[dict]:
    """The §13 'response hash' rows: the cached provider bytes this trace was computed from.

    Lives here, next to the renderer, because the report's content hash covers these rows: the
    verifier has to reproduce the SAME html, and calling build_pdf() without them renders a
    different page that can never match what was filed.

    Keyed by the trace's own pinned snapshot, so a trace served from an earlier snapshot's rows
    legitimately returns none — the surface says that rather than widening the query until
    something matches."""
    block = (r.get("pins") or {}).get("snapshot_block")
    addrs = [r.get("wallet")] + [(c.get("nearest") or {}).get("endpoint")
                                 for c in r.get("vasp_candidates", [])]
    addrs = [a for a in addrs if a]
    if block is None or not addrs:
        return []
    with connect() as c:
        rows = c.execute(
            "SELECT request_key, request, content_hash, provider, fetched_at FROM raw_response "
            "WHERE scope = %s AND request ILIKE ANY(%s) ORDER BY fetched_at LIMIT %s",
            (f"snapshot:{block}", [f"%{_like(a)}%" for a in addrs], limit)).fetchall()
    keys = ("request_key", "request", "content_hash", "provider", "fetched_at")
    return [dict(zip(keys, x, strict=True)) for x in rows]

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #111; line-height: 1.45; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 9pt; text-transform: uppercase; letter-spacing: .12em; color: #555;
     margin: 7mm 0 2mm; border-bottom: .4pt solid #bbb; padding-bottom: 1mm; }
.mono { font-family: "DejaVu Sans Mono", Menlo, Consolas, monospace; font-size: 8pt;
        word-break: break-all; }
.sub { color: #555; font-size: 8.5pt; margin: 0 0 4mm; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
th { text-align: left; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .08em;
     color: #555; border-bottom: .4pt solid #bbb; padding: 1.5mm 2mm; }
td { padding: 1.5mm 2mm; border-bottom: .3pt solid #ddd; vertical-align: top; }
.crown { background: #fbf5e0; border: .6pt solid #d8b84a; padding: 3mm; }
.big { font-size: 13pt; font-weight: bold; }
.note { font-size: 7.5pt; color: #555; margin-top: 2mm; line-height: 1.4; }
.route { border-left: 2.5pt solid #888; padding: 2mm 0 2mm 3mm; margin: 2mm 0; }
.route.on { border-left-color: #2a7f4f; }
.route.off { border-left-color: #9a5b1e; }
.pins td { border: none; padding: .8mm 2mm .8mm 0; }
"""


def _routes(recommended: str | None, reg: PgRegistry) -> list[dict]:
    """This case's real route, plus the contrast branch — both from the registry, neither invented.

    If the engine abstained there is no target, so only the contrast branch can be shown: that is
    the honest rendering of "no disclosure request exists yet", not a missing section."""
    out = []
    if recommended:
        out.append({**sahyog_mock.onboarding(recommended, reg), "which": "this case"})

    # India-first (§17): several VASPs are onboarded, but the contrast worth printing is one an
    # Indian investigator can actually serve under BNSS S.94, so an IN jurisdiction sorts first.
    # Alphabetical order would pick Bitget (SC) over WazirX (IN) and quietly weaken the point.
    def india_first(e: str):
        ent = reg.entities.get(e)
        return (0 if "IN" in (getattr(ent, "jurisdiction", None) or ()) else 1, e)

    confirmed = sorted((e for e, v in reg.entities.items()
                        if getattr(v, "sahyog", None) == "confirmed"), key=india_first)
    contrast = next((e for e in confirmed if e != recommended), None)
    if contrast:
        out.append({**sahyog_mock.onboarding(contrast, reg), "which": "contrast branch"})
    return out


def _separation_line(result: dict) -> str:
    """Separation is a gap between TWO candidates. With one it does not exist, and printing the
    sole score as "N pts between top two" claimed a comparison that was never made."""
    pts, n = result.get("separation_pts"), len(result.get("vasp_candidates") or [])
    if n < 2 or pts is None:
        return ("Only one candidate endpoint was reached, so there is no separation to report "
                "(separation applies to the gap between the top two).") if n else ""
    return f"Separation {_html.escape(str(result.get('separation')))} ({pts} pts between top two)."


def _rows(headers, rows) -> str:
    head = "".join(f"<th>{_html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_html(result: dict, hashes: list[dict] | None = None, reg: PgRegistry | None = None) -> str:
    # The PINNED label set, not the newest one (§12). Rendering against "latest" meant a report for
    # an old case silently picked up entity names, jurisdictions and SAHYOG status from a label set
    # the case was never scored under — the exact non-determinism the pins exist to prevent.
    reg = reg or PgRegistry((result.get("pins") or {}).get("label_set_version"))
    e = _html.escape
    pins = result.get("pins", {})
    # Tron has no block height in TronGrid's TRC-20 rows; its snapshot is unix seconds (providers/tron.py)
    snap_label = "snapshot (unix time, UTC)" if result.get("chain") == "tron" else "snapshot block"
    rec = result.get("recommended")
    cands = result.get("vasp_candidates", [])
    top = next((c for c in cands if c["entity"] == rec), None)
    near = (top or {}).get("nearest") or result.get("nearest") or {}
    dep = near.get("deposit_event") or result.get("deposit_event")
    disclosure = sahyog_mock.disclosure_payload(result, None, reg)

    if rec and top:
        verdict = (f'<div class="crown"><div class="big">{e(top.get("entity_name") or rec)} · '
                   f'{top["score"]} / 100 confidence index</div>'
                   f'<div class="note">{e(result.get("rationale") or "")}</div></div>')
    else:
        verdict = (f'<div class="crown" style="background:#f6f6f6;border-color:#999">'
                   f'<div class="big">No target crowned: {e(result.get("state", "UNATTRIBUTED"))}</div>'
                   f'<div class="note">{e(result.get("rationale") or result.get("reason") or "")}'
                   f' Abstention is a result: the engine refuses to name a VASP on absence of '
                   f'evidence.</div></div>')

    lead = _rows(["#", "entity", "index /100", "paths", "endpoint", "sahyog"],
                 [(i + 1, e(c.get("entity_name") or c["entity"]), c["score"], c.get("n_paths", ""),
                   f'<span class="mono">{e((c.get("nearest") or {}).get("endpoint") or "")}</span>',
                   e(c.get("sahyog") or "unknown"))
                  for i, c in enumerate(cands)]) if cands else \
        '<div class="note">No VASP candidate reached. Nothing is ranked on absence of evidence.</div>'

    br = (top or {}).get("breakdown") or {}
    factors = _rows(["factor", "value", "weight", "contribution"],
                    [(k, br["factors"].get(k), br["weights"].get(k), br["contributions"].get(k))
                     for k in br.get("weights", {})]) if br else ""
    pen = (f'<div class="note">penalties applied: '
           f'{e(", ".join(br.get("penalties_applied") or []) or "none")} '
           f'(total −{br.get("penalty", 0)})</div>') if br else ""

    prov = _rows(["entity", "endpoint", "role basis", "hops", "index", "deposit tx"],
                 [(e(p.get("entity_name") or p["entity"]),
                   f'<span class="mono">{e(p.get("endpoint") or "")}</span>',
                   e(str(p.get("role_basis") or "not recorded")), p.get("hops"),
                   p.get("confidence_index"),
                   f'<span class="mono">{e((p.get("deposit_event") or {}).get("tx") or "-")}</span>')
                  for p in sahyog_mock.provenance(result)]) or ""

    sweeps = "".join(
        f'<div class="note" style="margin-bottom:2mm">'
        f'<b>{e(s.get("entity", ""))}</b> deposit <span class="mono">{e(s.get("address", ""))}</span> '
        f'- sweep {e(str(s.get("sweep")))}, {s.get("distinct_senders")} distinct senders, '
        f'{round(float(s.get("share", 0)) * 100, 2)}% of sweep tx '
        f'<span class="mono">{e(str(s.get("sweep_tx", ""))[:24])}…</span> → hot wallet '
        f'<span class="mono">{e(str(s.get("hot_wallet", "")))}</span><br>'
        f'hot-wallet label provenance: {e(str(s.get("hot_label", "")))}</div>'
        for s in result.get("sweep_evidence", []))

    hrows = _rows(["provider", "request", "response hash", "fetched"],
                  [(e(h["provider"]), f'<span class="mono">{e(h["request"][:64])}</span>',
                    f'<span class="mono">{e(h["content_hash"][:24])}…</span>', e(str(h["fetched_at"])[:19]))
                   for h in (hashes or [])]) if hashes else \
        '<div class="note">No cached provider response rows matched this trace\'s pinned snapshot.</div>'

    route_html = ""
    for r in _routes(rec, reg):
        on = r["routable_via_sahyog"]
        route_html += (
            f'<div class="route {"on" if on else "off"}">'
            f'<b>{e(r["target_vasp_name"])}</b> <span class="note">({e(r["which"])}, '
            f'sahyog: {e(r["target_vasp_sahyog"])}, jurisdiction: '
            f'{e(", ".join(r["jurisdiction"]) or "unrecorded")})</span><br>'
            f'{"ROUTABLE: " if on else "NOT ROUTABLE: "}{e(r["route"])}</div>')

    flags = "".join(f'<div class="note mono">{e(f)}</div>' for f in result.get("flags", [])[:6])

    return f"""<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>VASP attribution report</h1>
<div class="sub">Suspect wallet <span class="mono">{e(result.get("wallet", ""))}</span> ·
chain {e(result.get("chain", ""))} · trace <span class="mono">{e(result.get("trace_id", ""))}</span><br>
Investigative lead, not identity and not evidence. Identifying the account holder behind the
endpoint is the VASP's KYC under a lawful request.</div>

{verdict}

<h2>Determinism pins (§12)</h2>
<table class="pins"><tbody>
<tr><td>{snap_label}</td><td class="mono">{e(str(pins.get("snapshot_block")))}</td></tr>
<tr><td>label set version</td><td class="mono">{e(str(pins.get("label_set_version")))}</td></tr>
<tr><td>weight hash</td><td class="mono">{e(str(pins.get("weight_hash")))} (frozen {e(str(pins.get("weights_frozen_at")))})</td></tr>
<tr><td>adapter version</td><td class="mono">{e(str(pins.get("adapter_version")))}</td></tr>
</tbody></table>
<div class="note">Re-running with these four pins reproduces this report. Reproducibility is not
legal chain of custody; production custody is named as future work.</div>

<h2>Ranked candidates</h2>
{lead}
<div class="note">{_separation_line(result)} Confidence is an INDEX out of 100, not a probability,
not a percentage, and not a claim of ownership.</div>

<h2>Nearest endpoint: proximity, a separate claim (§3)</h2>
<div class="note">hops {near.get("hops")} · role basis {e(str(near.get("role_basis") or "not recorded"))}
· endpoint <span class="mono">{e(str(near.get("endpoint") or "-"))}</span>
{f' · deposit tx <span class="mono">{e(dep["tx"])}</span> ({dep.get("amount_btc") or dep.get("amount")})' if dep else ""}
<br>Fewest hops is not highest confidence. Value at the endpoint is the amount that landed there,
not the suspect's own share; a deposit transaction can aggregate many senders.</div>

<h2>Score breakdown</h2>
{factors}{pen}

<h2>Provenance</h2>
{prov}
{f"<h2>Sweep evidence</h2>{sweeps}" if sweeps else ""}

<h2>Provider response hashes</h2>
{hrows}

<h2>Routing (§17): both branches</h2>
{route_html}
<div class="note">{e(sahyog_mock.SCHEMA_NOTE)} Legal basis {e(sahyog_mock.LEGAL_BASIS)}.
The documented contract is the deliverable; this tool has never transmitted anything to SAHYOG or
to any VASP.</div>

{f"<h2>Flags</h2>{flags}" if flags else ""}
<div class="note" style="margin-top:6mm">{e(disclosure.get("disclaimer", ""))}</div>
</body></html>"""


def build_pdf(result: dict, hashes: list[dict] | None = None) -> tuple[bytes, str]:
    """-> (pdf bytes, content hash of the rendered HTML). Imported lazily: WeasyPrint pulls in
    cairo/pango and there is no reason to pay that at API startup for an endpoint most requests
    never touch.

    The hash is taken over the HTML, not the PDF bytes: WeasyPrint stamps a creation timestamp into
    every PDF, so two renders of one trace produced two different digests and §12's
    reproduce-and-verify could never succeed. The HTML is a pure function of (stored result, pinned
    label set), which is exactly the thing a verifier wants to compare."""
    from weasyprint import HTML
    html_doc = build_html(result, hashes)
    return HTML(string=html_doc).write_pdf(), hashlib.sha256(html_doc.encode()).hexdigest()
