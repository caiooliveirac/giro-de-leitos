-- 012_parsed_events_wa_message_id.sql
-- Identidade da mensagem do WhatsApp que virou este evento.
--
-- Por quê: em 18-19/11/2026 o gateway ficou ~32h sem conseguir entregar o
-- webhook e os giros só existiam no histórico do próprio gateway. Para
-- reprocessar com segurança (o reconciliador roda a cada 10 min e reenvia o
-- que faltar) a ingestão precisa ser IDEMPOTENTE: a mesma mensagem chegando
-- pelo webhook ao vivo e pelo reconciliador não pode virar dois eventos.
--
-- O índice único é PARCIAL: ingestão manual, Telegram e o histórico anterior
-- não têm id e continuam gravando NULL, sem conflito.
--
-- Idempotente (roda em todo startup).

ALTER TABLE parsed_events ADD COLUMN IF NOT EXISTS wa_message_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_parsed_events_wa_message_id
    ON parsed_events (wa_message_id)
    WHERE wa_message_id IS NOT NULL;
