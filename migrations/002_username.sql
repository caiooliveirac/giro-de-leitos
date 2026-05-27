-- 002_username.sql
-- Add opaque username for login (alternativa ao CPF). Idempotente.

ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique
    ON users(username)
    WHERE username IS NOT NULL;
