"""Ingestão idempotente e à prova de giro atrasado (Postgres de verdade).

Contexto: em 18-19/11/2026 o gateway WhatsApp ficou ~32h sem entregar o
webhook. Os giros só existiam no histórico do gateway, e o reconciliador agora
reenvia o que faltar. Isso só é seguro com duas garantias:

1. a mesma mensagem (``wa_message_id``) nunca vira dois eventos;
2. um giro ENVIADO antes do que está no painel vai só para o histórico —
   nunca sobrepõe o estado atual nem dispara alerta.

Precisa de um Postgres descartável em ``TEST_DATABASE_URL`` (o banco é
limpo a cada teste). Sem ele os testes são pulados, como no CI.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("JWT_SECRET", "test-secret-idempotency")
os.environ.setdefault("CPF_ENCRYPTION_KEY", "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=")
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

import db  # noqa: E402
import main  # noqa: E402

TEST_DB = os.getenv("TEST_DATABASE_URL", "").strip()

# Giro sintético (nenhum dado real): unidade do cadastro, horário no texto.
GIRO = """UPA BARRIS
DATA: 18/11/2026 ÀS {hora}
SALA VERMELHA: {vermelha}/04
SALA AMARELA: 05/10
"""


def _giro(hora: str, vermelha: str) -> str:
    return GIRO.format(hora=hora, vermelha=vermelha)


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL não definido")
class IngestIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._patch = mock.patch.object(db, "DATABASE_URL", TEST_DB)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        db.init_db()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE parsed_events, current_unit_status, alert_events RESTART IDENTITY CASCADE")
            conn.commit()
        self.notified: list = []
        p = mock.patch.object(main, "_notify_transition_alerts", side_effect=lambda a: self.notified.append(a))
        p.start()
        self.addCleanup(p.stop)
        for name in ("_notify_unparsed_giro", "_notify_group_telegram"):
            q = mock.patch.object(main, name, return_value=None)
            q.start()
            self.addCleanup(q.stop)
        from fastapi.testclient import TestClient

        self.client = TestClient(main.app)

    def _post(self, text: str, sent_at: str, msg_id: str | None, **extra):
        body = {"text": text, "source": "whatsmeow", "received_at": sent_at, **extra}
        if msg_id:
            body["wa_message_id"] = msg_id
        r = self.client.post("/api/ingest/whatsapp-bridge", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _q(self, sql: str):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def test_same_message_twice_is_one_event(self) -> None:
        first = self._post(_giro("10:00", "02"), "2026-11-18T13:00:00Z", "MSG1")
        second = self._post(_giro("10:00", "02"), "2026-11-18T13:00:00Z", "MSG1")
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(self._q("SELECT count(*) AS n FROM parsed_events")[0]["n"], 1)

    def test_without_id_keeps_old_behaviour(self) -> None:
        self._post(_giro("10:00", "02"), "2026-11-18T13:00:00Z", None)
        self._post(_giro("10:00", "02"), "2026-11-18T13:00:00Z", None)
        self.assertEqual(self._q("SELECT count(*) AS n FROM parsed_events")[0]["n"], 2)

    def test_older_message_never_overwrites_current(self) -> None:
        self._post(_giro("12:00", "03"), "2026-11-18T15:00:00Z", "NOVO")
        late = self._post(_giro("09:00", "01"), "2026-11-18T12:00:00Z", "VELHO")
        self.assertEqual(late["status"], "stale")
        cur = self._q("SELECT red_occupied, updated_at FROM current_unit_status")
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0]["red_occupied"], 3)
        # O atrasado está no histórico.
        self.assertEqual(self._q("SELECT count(*) AS n FROM parsed_events")[0]["n"], 2)
        self.assertEqual(self.notified, [])

    def test_backfill_in_order_updates_panel_without_alerts(self) -> None:
        self._post(_giro("09:00", "01"), "2026-11-18T12:00:00Z", "A")
        self._post(_giro("12:00", "04"), "2026-11-18T15:00:00Z", "B", backfill=True)
        cur = self._q("SELECT red_occupied FROM current_unit_status")
        self.assertEqual(cur[0]["red_occupied"], 4)
        self.assertEqual(self.notified, [])

    def test_live_newer_message_still_alerts(self) -> None:
        self._post(_giro("09:00", "01"), "2026-11-18T12:00:00Z", "A")
        self._post(_giro("12:00", "04"), "2026-11-18T15:00:00Z", "B")
        self.assertEqual(self._q("SELECT red_occupied FROM current_unit_status")[0]["red_occupied"], 4)
        self.assertTrue(self.notified, "transição ao vivo deve continuar alertando")


if __name__ == "__main__":
    unittest.main()
