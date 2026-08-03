-- 007_unparsed_messages.sql
-- Mensagens que o sistema reconheceu como giro mas NÃO conseguiu publicar
-- (unidade e/ou horário ausentes). Antes desta tabela, o texto bruto da falha
-- só existia no alerta do Telegram do admin — não dava para medir quantas são,
-- de qual grupo vêm nem qual o padrão do erro (/naoparseados).
--
-- Alimentada no mesmo ponto em que o alerta de "giro não reconhecido" é
-- emitido (main._notify_unparsed_giro), portanto já filtrada por
-- _looks_like_giro: conversa comum do grupo não entra aqui.
--
-- Retenção: sem TTL automático; a tabela cresce ~2 linhas/dia hoje.
-- Idempotente (roda em todo startup).

CREATE TABLE IF NOT EXISTS unparsed_messages (
    id         BIGSERIAL PRIMARY KEY,
    source     TEXT NOT NULL,
    raw_text   TEXT NOT NULL,
    first_line TEXT,
    reasons    JSONB NOT NULL DEFAULT '[]'::jsonb,
    unit_hint  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unparsed_messages_created_at
    ON unparsed_messages (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_unparsed_messages_source_created_at
    ON unparsed_messages (source, created_at DESC);
