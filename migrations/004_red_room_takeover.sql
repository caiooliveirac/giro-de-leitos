-- 004_red_room_takeover.sql
-- Idempotent migration: takeover ("assumir giro") da sala vermelha.
--
-- Modelo: por padrão a sala vermelha de uma unidade é projetada AO VIVO do
-- parser do WhatsApp (re-semeia a cada giro). Quando um plantonista "assume"
-- a unidade, a edição manual passa a vencer e o parser para de sobrescrever.
-- O estado de takeover é uma propriedade da unidade (persiste entre plantões),
-- não da sessão.

CREATE TABLE IF NOT EXISTS unit_sector_takeover (
    unit_id     UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    sector_key  TEXT NOT NULL,
    assumed_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    assumed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    PRIMARY KEY (unit_id, sector_key)
);

-- Segurança contra perda de dado: unidades que já têm leitos manuais na sala
-- vermelha (editadas sob o modelo antigo) devem permanecer "assumidas", para
-- que o próximo giro não apague o que o plantonista digitou.
INSERT INTO unit_sector_takeover (unit_id, sector_key)
SELECT DISTINCT unit_id, 'red_room' FROM beds
ON CONFLICT (unit_id, sector_key) DO NOTHING;
