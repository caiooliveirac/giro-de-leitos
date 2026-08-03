-- 009_parsed_events_sender.sql
-- Autoria do giro: QUEM enviou a mensagem que virou este evento.
--
-- Até aqui o adapter do WhatsApp descartava o remetente e mandava só
-- {text, source}. O endpoint /api/ingest/whatsapp-bridge já aceitava
-- `sender_phone`, mas nada preenchia — e sem autoria a cobrança só sabia
-- nomear o coordenador *cadastrado*, que muitas vezes não é quem posta o giro
-- no grupo. Quem posta É o contato da unidade; estas colunas são a base para
-- descobrir isso (ver migrations/010_unit_contacts.sql).
--
-- Ambas são opcionais de propósito: mensagem antiga, ingestão manual e
-- adapter desatualizado continuam gravando NULL, sem erro.
--
-- Idempotente (roda em todo startup).

ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS sender_phone TEXT;
ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS sender_name TEXT;

CREATE INDEX IF NOT EXISTS idx_parsed_events_sender_phone_received_at
    ON parsed_events (sender_phone, received_at DESC)
    WHERE sender_phone IS NOT NULL;
