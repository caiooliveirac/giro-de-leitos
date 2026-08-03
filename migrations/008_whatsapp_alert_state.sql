-- 008_whatsapp_alert_state.sql
-- Anti-spam do alerta de silêncio enviado por WhatsApp ao gestor.
--
-- O watcher roda a cada STALE_CHECK_INTERVAL_MINUTES (30min por padrão) e uma
-- unidade parada continua parada na varredura seguinte. Sem esta tabela o
-- mesmo aviso sairia de meia em meia hora até alguém postar o giro — e o
-- dedupe em memória do watcher do Telegram (main._stale_alerted) se perde a
-- cada restart/deploy, que é justamente quando o reenvio incomoda.
--
-- Uma linha por unidade, sobrescrita a cada envio REAL (dry-run não grava:
-- estado aqui significa "o gestor foi avisado", não "o código passou por
-- aqui"). A janela mínima entre avisos é WHATSAPP_ALERT_COOLDOWN_HOURS.
--
-- Idempotente (roda em todo startup).

CREATE TABLE IF NOT EXISTS whatsapp_alert_state (
    unit_key     TEXT PRIMARY KEY,
    last_sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
