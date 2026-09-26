"""§12 reproduce-and-verify — the check the invariant claimed and nobody could run.

    python -m app.eval.verify <trace_id>

What it proves: the filed report for this trace re-renders **byte-identically** from the stored
result and the trace's PINNED label set, and its content hash matches the row recorded in `reports`
when it was filed. What it does not prove: that the providers would return the same bytes today —
the claim is reproducible *from the store* (§12 scopes it that way deliberately).

Why the hash is over the HTML and not the PDF: WeasyPrint stamps a creation timestamp into every
PDF, so two renders of one trace produced two different digests and this check could never pass.
"""
import sys

from app.api.report import build_pdf, response_hashes
from app.db import connect
from app.trace.tasks import job


def verify(trace_id: str) -> int:
    j = job(trace_id)
    if not j or not j.get("result"):
        print(f"{trace_id}: no finished trace to verify")
        return 2
    r = j["result"]
    with connect() as c:
        rows = c.execute("SELECT content_hash, adapter_version, weight_hash, created_at "
                         "FROM reports WHERE case_id = %s ORDER BY created_at", (r["case_id"],)).fetchall()
        raw = c.execute("SELECT count(*) FROM audit_log WHERE trace_id = %s", (trace_id,)).fetchone()[0]

    pins = r.get("pins", {})
    hashes = response_hashes(r)          # exactly what GET /report renders with
    _, digest = build_pdf(r, hashes)
    _, again = build_pdf(r, hashes)

    print(f"trace   {trace_id}")
    print(f"pins    block {pins.get('snapshot_block')} · {pins.get('label_set_version')} · "
          f"{pins.get('weight_hash')} · {pins.get('adapter_version')}")
    print(f"audit   {raw} upstream read(s) recorded for this trace")
    print(f"render  {digest}")
    print(f"re-render {'matches' if again == digest else 'DIFFERS: rendering is not deterministic'}")
    if again != digest:
        return 1
    if not rows:
        print("filed   no report row for this case yet; nothing to compare against "
              "(GET /report/{id} files one)")
        return 3
    match = [h for h, *_ in rows if h == digest]
    for h, adapter, wh, at in rows:
        mark = "OK  " if h == digest else "DIFF"
        print(f"filed   {mark} {h[:32]}… {str(at)[:19]} (adapter {adapter}, weights {wh})")
    if match:
        print("VERIFIED: the filed report reproduces from the stored result and the pinned label set")
        return 0
    print("MISMATCH: the re-render does not match any filed hash for this case. Either the stored "
          "result, the pinned label set or the report template changed since it was filed.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(verify(sys.argv[1]))
