-- Kai MCP de empresa — declarative schema (source of truth for Atlas).
-- Every table is tenant-scoped. Applied on first `docker compose up` via initdb,
-- and managed going forward with `atlas schema apply --env local`.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Users registry per tenant (reference + ACL, not strong PII).
CREATE TABLE IF NOT EXISTS users (
    tenant_id  uuid        NOT NULL,
    user_id    text        NOT NULL,
    role       text,
    acl_tags   text[]      NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

-- Knowledge base: free-form chunks + embeddings + ACL tags.
CREATE TABLE IF NOT EXISTS knowledge (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL,
    collection text        NOT NULL,
    title      text        NOT NULL,
    content    text        NOT NULL,
    acl_tags   text[]      NOT NULL DEFAULT '{}',
    embedding  vector(1536),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_tenant_idx ON knowledge (tenant_id);
CREATE INDEX IF NOT EXISTS knowledge_collection_idx ON knowledge (tenant_id, collection);
CREATE INDEX IF NOT EXISTS knowledge_embedding_idx
    ON knowledge USING hnsw (embedding vector_cosine_ops);

-- Rules active per scope.
CREATE TABLE IF NOT EXISTS rules (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL,
    scope      text        NOT NULL,
    body       text        NOT NULL,
    active     boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rules_tenant_idx ON rules (tenant_id, scope);

-- Audit log of tool calls (NEVER stores tokens or prompt content).
CREATE TABLE IF NOT EXISTS interactions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    user_id     text        NOT NULL,
    tool        text        NOT NULL,
    outcome     text        NOT NULL DEFAULT 'ok',
    cost_tokens integer     NOT NULL DEFAULT 0,
    latency_ms  integer     NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interactions_tenant_idx ON interactions (tenant_id, created_at DESC);

-- Outbound actions audit (table present day 1; tools are Phase 2).
CREATE TABLE IF NOT EXISTS outbound_actions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    user_id     text        NOT NULL,
    action_type text        NOT NULL,
    payload     jsonb       NOT NULL,
    gate_tier   text        NOT NULL DEFAULT 'CRITICAL',
    executed    boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbound_tenant_idx ON outbound_actions (tenant_id, created_at DESC);

-- Pending approvals for outbound actions (Phase 2 confirmation gate).
CREATE TABLE IF NOT EXISTS pending_approvals (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL,
    action_id  uuid        NOT NULL REFERENCES outbound_actions (id) ON DELETE CASCADE,
    status     text        NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pending_tenant_idx ON pending_approvals (tenant_id, status);

-- Who-am-I snapshots (Yamel pattern), versioned per user.
CREATE TABLE IF NOT EXISTS who_am_i_snapshots (
    tenant_id  uuid        NOT NULL,
    user_id    text        NOT NULL,
    version    integer     NOT NULL DEFAULT 1,
    profile    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, version)
);
