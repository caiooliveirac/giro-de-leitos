-- Multi-UPA: um coordenador pode coordenar mais de uma unidade.
-- `users.unit_id` continua sendo a unidade "primária" (default de sessão /
-- self-pair); a tabela abaixo guarda o conjunto completo de UPAs do coordenador.
-- Idempotente (roda em todo startup).

CREATE TABLE IF NOT EXISTS coordinator_units (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unit_id    uuid NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_coordinator_units_unit ON coordinator_units(unit_id);

-- Backfill: cada coordenador com unidade entra no conjunto.
INSERT INTO coordinator_units (user_id, unit_id)
SELECT id, unit_id
  FROM users
 WHERE role = 'coordinator' AND unit_id IS NOT NULL
ON CONFLICT (user_id, unit_id) DO NOTHING;
