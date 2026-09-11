-- System of record (§7.6). Neo4j is a derived index rebuilt from these tables (scripts/rebuild_graph.py).
-- Idempotent: every statement is IF NOT EXISTS, so init_schema() runs on every start.

-- ---------- labels (§6), versioned (§12: Tornado Cash was sanctioned 2022, delisted 2025) ----------
-- Deviation from §7.6: the label version is part of every key, so an old label set stays replayable.
CREATE TABLE IF NOT EXISTS label_set (
    version     text PRIMARY KEY,          -- ls-<sha256(canonical rows)[:12]>: same content = same version
    created_at  timestamptz NOT NULL DEFAULT now(),
    n_labels    int NOT NULL,
    n_entities  int NOT NULL,
    sources     jsonb NOT NULL             -- pinned inputs (TagPacks commit, file hashes)
);
CREATE TABLE IF NOT EXISTS vasp (
    label_set_version text NOT NULL REFERENCES label_set(version),
    id           text NOT NULL,
    name         text NOT NULL,
    type         text NOT NULL,
    jurisdiction jsonb NOT NULL DEFAULT '[]',
    aliases      jsonb NOT NULL DEFAULT '[]',
    sahyog       text NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (label_set_version, id)
);
CREATE TABLE IF NOT EXISTS address_label (
    id                bigserial PRIMARY KEY,
    label_set_version text NOT NULL REFERENCES label_set(version),
    chain       text NOT NULL,
    address     text NOT NULL,
    addr_key    text NOT NULL,             -- registry.addr_key: lowercase EVM/bech32, base58 verbatim
    role        text NOT NULL,             -- deposit | hot | sanctioned | mixer | dex
    entity_id   text NOT NULL,
    source      text NOT NULL,             -- SOURCE_TIER key
    confidence  double precision NOT NULL,
    basis       text NOT NULL,
    provenance  text NOT NULL,
    UNIQUE (label_set_version, chain, addr_key, entity_id, role, source, basis)
);
CREATE INDEX IF NOT EXISTS address_label_lookup ON address_label (label_set_version, chain, addr_key);

-- ---------- raw upstream responses, content-addressed (§12) ----------
CREATE TABLE IF NOT EXISTS raw_blob (
    content_hash text PRIMARY KEY,          -- sha256 of the canonical JSON body
    body         jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_response (
    request_key  text PRIMARY KEY,          -- provider-independent request + snapshot scope (volatile reads)
    request      text NOT NULL,
    scope        text NOT NULL,             -- 'immutable' | 'snapshot:<block>'
    content_hash text NOT NULL REFERENCES raw_blob(content_hash),
    provider     text NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_response_request ON raw_response (request);

-- ---------- normalized graph edges (both data models, §7.2/§7.3) ----------
CREATE TABLE IF NOT EXISTS utxo_tx (
    chain text NOT NULL, hash text NOT NULL, block bigint, ts bigint,
    total_in numeric NOT NULL, total_out numeric NOT NULL,
    PRIMARY KEY (chain, hash)
);
CREATE TABLE IF NOT EXISTS edge (
    id       bigserial PRIMARY KEY,
    chain    text NOT NULL,
    src      text NOT NULL,                 -- address, or tx hash for a CREDITS edge
    dst      text NOT NULL,                 -- address, or tx hash for a FUNDS edge
    kind     text NOT NULL,                 -- native | internal | erc20 | funds | credits
    tx_hash  text NOT NULL,
    idx      text NOT NULL,                 -- log_index | trace id | vin | vout; '' = none (native)
    value    numeric NOT NULL,              -- base units
    asset    text NOT NULL,
    decimals int  NOT NULL,
    block    bigint NOT NULL,
    ts       bigint NOT NULL,
    meta     jsonb NOT NULL DEFAULT '{}',
    UNIQUE (chain, src, dst, tx_hash, kind, idx)   -- §7.2 edge identity: one tx emits many movements
);
CREATE TABLE IF NOT EXISTS trace_edge (       -- which trace saw which edge (per-trace subgraph, rebuild)
    trace_id uuid NOT NULL, edge_id bigint NOT NULL REFERENCES edge(id), hop int NOT NULL,
    PRIMARY KEY (trace_id, edge_id)
);

-- ---------- cases, jobs, audit (§7.6, §12) ----------
CREATE TABLE IF NOT EXISTS cases (
    id uuid PRIMARY KEY, wallet text NOT NULL, chain text NOT NULL, source text NOT NULL DEFAULT 'manual',
    snapshot_block bigint NOT NULL, snapshot_ts timestamptz,
    label_set_version text NOT NULL REFERENCES label_set(version),
    params jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS trace_jobs (
    id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES cases(id),
    state text NOT NULL,                    -- QUEUED | FETCHING | SCORING | DONE | FAILED (§12)
    current_hop int NOT NULL DEFAULT 0, progress real NOT NULL DEFAULT 0,
    result jsonb, error text, started_at timestamptz, finished_at timestamptz,
    pins jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS audit_log (        -- append-only
    id bigserial PRIMARY KEY, trace_id uuid, ts timestamptz NOT NULL DEFAULT now(),
    input_params jsonb NOT NULL, upstream_hash text, actor text NOT NULL DEFAULT 'system'
);
CREATE TABLE IF NOT EXISTS reports (
    id uuid PRIMARY KEY, case_id uuid REFERENCES cases(id), pdf_path text, content_hash text,
    adapter_version text, weight_hash text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS evidence (
    id bigserial PRIMARY KEY, trace_id uuid NOT NULL, candidate_entity text NOT NULL,
    factor text NOT NULL, value real, provenance jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS golden_set (
    id text PRIMARY KEY, suspect_addr text NOT NULL, chain text NOT NULL,
    expected_entity text, min_hops int, source_doc text
);
