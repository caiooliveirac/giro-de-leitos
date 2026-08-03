-- 010_unit_contacts.sql
-- Quem posta o giro de cada unidade, derivado da própria ingestão.
--
-- Não é cadastro: ninguém preenche esta tabela na mão. Toda vez que um giro
-- COM unidade identificada chega com autoria (migrations/009), a linha
-- (unit_code, sender_phone) é criada ou incrementada. Depois de alguns dias a
-- tabela responde a pergunta que o coordenador cadastrado não responde:
-- "para quem eu falo quando a UPA X para de postar?".
--
-- Uso: o alerta de silêncio no WhatsApp e o /cobranca mencionam estes números
-- (menção fantasma via campo `mentions` do POST /send/message do gateway).
-- Sem contato conhecido, os dois caem no coordenador cadastrado, como antes —
-- a tabela nasce vazia e isso não pode quebrar nada.
--
-- `giro_count` conta giros publicados, não mensagens: o upsert só acontece
-- quando o evento tem unit_code, então conversa solta do grupo não entra.
--
-- Idempotente (roda em todo startup).

CREATE TABLE IF NOT EXISTS unit_contacts (
    unit_code     TEXT NOT NULL,
    sender_phone  TEXT NOT NULL,
    sender_name   TEXT,
    giro_count    BIGINT NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_code, sender_phone)
);

-- Ordem de leitura do alerta/cobrança: quem mais posta primeiro, desempate
-- pelo envio mais recente.
CREATE INDEX IF NOT EXISTS idx_unit_contacts_unit_rank
    ON unit_contacts (unit_code, giro_count DESC, last_seen_at DESC);
