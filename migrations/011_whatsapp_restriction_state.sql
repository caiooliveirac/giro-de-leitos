-- 011_whatsapp_restriction_state.sql
-- Estado do aviso de restrição de UPA no grupo do WhatsApp.
--
-- O aviso tem duas metades e as duas precisam de memória que sobreviva a
-- restart/deploy:
--
--   snapshot        — a última lista de restrições que o grupo JÁ viu. É contra
--                     ela que cada varredura compara para descobrir o que
--                     mudou. Sem persistir, todo restart re-anunciaria como
--                     "nova" cada restrição ainda vigente.
--   digest:<slot>   — uma linha por janela de 4h já enviada. Sem isto, a API
--                     subindo duas vezes dentro da mesma janela mandaria o
--                     lembrete duas vezes.
--
-- Chave/valor de propósito: as duas metades têm ciclos de vida diferentes
-- (o snapshot é sobrescrito, os slots só acumulam) mas o mesmo dono, e uma
-- tabela só evita duas migrations para guardar dois JSONs.
--
-- Idempotente (roda em todo startup).

CREATE TABLE IF NOT EXISTS whatsapp_restriction_state (
    state_key  TEXT PRIMARY KEY,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
