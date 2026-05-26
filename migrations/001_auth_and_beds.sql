-- 001_auth_and_beds.sql
-- Idempotent migration: auth, units promotion, beds, sectors, invites, audit.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- units (promoted from python UNIT_REGISTRY; data seeded via scripts/seed_admin.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS units (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT UNIQUE NOT NULL,
    canonical_name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS unit_aliases (
    id BIGSERIAL PRIMARY KEY,
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    UNIQUE (unit_id, alias)
);

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    cpf_encrypted TEXT NOT NULL,
    cpf_hash TEXT UNIQUE NOT NULL,
    phone TEXT,
    photo_url TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin','coordinator','professional')),
    cargo TEXT,
    coren_crm TEXT,
    unit_id UUID REFERENCES units(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','suspended')),
    email TEXT UNIQUE,
    password_hash TEXT,
    pin_hash TEXT,
    lgpd_accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    approved_by UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_users_unit_id ON users (unit_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- ---------------------------------------------------------------------------
-- unit_sectors_config
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unit_sectors_config (
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    sector_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    capacity INTEGER,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_id, sector_key)
);

-- ---------------------------------------------------------------------------
-- beds
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS beds (
    id BIGSERIAL PRIMARY KEY,
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    bed_number INTEGER NOT NULL,
    patient_sigla TEXT,
    clinical_summary TEXT,
    occupied_since TIMESTAMPTZ,
    last_updated_by UUID REFERENCES users(id),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (unit_id, bed_number)
);

-- ---------------------------------------------------------------------------
-- counters / specialists / exams (per sector)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS counters (
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    sector_key TEXT NOT NULL,
    occupancy INTEGER NOT NULL DEFAULT 0,
    capacity INTEGER NOT NULL DEFAULT 0,
    last_updated_by UUID REFERENCES users(id),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (unit_id, sector_key)
);

CREATE TABLE IF NOT EXISTS specialists (
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    sector_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unavailable' CHECK (status IN ('available','unavailable','on_call')),
    last_updated_by UUID REFERENCES users(id),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (unit_id, sector_key)
);

CREATE TABLE IF NOT EXISTS exams (
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    sector_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'working' CHECK (status IN ('working','unavailable')),
    unavailable_reason TEXT,
    last_updated_by UUID REFERENCES users(id),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (unit_id, sector_key)
);

-- ---------------------------------------------------------------------------
-- invites
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('coordinator','professional')),
    target_unit_id UUID REFERENCES units(id) ON DELETE CASCADE,
    created_by UUID NOT NULL REFERENCES users(id),
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','used','expired','revoked')),
    used_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_invites_token ON invites (token);
CREATE INDEX IF NOT EXISTS idx_invites_status ON invites (status);

-- ---------------------------------------------------------------------------
-- auth_sessions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    end_reason TEXT
);

-- ---------------------------------------------------------------------------
-- trusted_devices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trusted_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    device_fingerprint TEXT NOT NULL,
    label TEXT,
    paired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    pairing_code TEXT,
    pairing_code_expires_at TIMESTAMPTZ,
    UNIQUE (unit_id, device_fingerprint)
);

-- ---------------------------------------------------------------------------
-- audit_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id UUID REFERENCES users(id),
    session_id UUID REFERENCES auth_sessions(id),
    device_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    previous_value JSONB,
    new_value JSONB,
    client_ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC);

-- ---------------------------------------------------------------------------
-- notification_queue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_queue (
    id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    template TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
