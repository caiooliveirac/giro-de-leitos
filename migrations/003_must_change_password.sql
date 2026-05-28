-- 003_must_change_password.sql
-- Forca troca de senha no proximo login apos reset administrativo. Idempotente.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
