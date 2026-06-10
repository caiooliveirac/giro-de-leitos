-- 005_pairing_code_paired_at_nullable.sql
-- Codigo de pareamento cria uma linha "pendente" em trusted_devices com
-- paired_at = NULL (aparelho ainda nao pareado); o pareamento real preenche
-- paired_at depois. A 001 criou a coluna como NOT NULL, o que fazia o
-- create_pairing_code falhar (NotNullViolation) e retornar 500. Idempotente.

ALTER TABLE trusted_devices
    ALTER COLUMN paired_at DROP NOT NULL;
