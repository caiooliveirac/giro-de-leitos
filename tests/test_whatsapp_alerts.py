"""Alerta de UPA sem giro pelo WhatsApp do gestor.

O canal é frágil por natureza: se ele mandar demais, o gestor silencia o
número e o alerta deixa de existir. Por isso os testes daqui cobrem, antes de
qualquer formatação, as travas que impedem o envio — limiar, cooldown, lista
vazia e o dry-run (que NUNCA pode tocar na rede).
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

# `main` faz fail-fast em auth/crypto no import (mesma receita de test_reports).
os.environ.setdefault("JWT_SECRET", "test-secret-whatsapp")
os.environ.setdefault("CPF_ENCRYPTION_KEY", "OmaP3i0nC2P9MwJv5wDhlb0aBpfNn5Y73I9c8wL2cIc=")
os.environ.setdefault("CPF_HASH_PEPPER", "test-pepper")

from reports import MAX_WHATSAPP_ALERT_UNITS, build_whatsapp_stale_alert_text  # noqa: E402
from services import whatsapp_alerts  # noqa: E402


NOW = datetime(2026, 8, 3, 18, 32, tzinfo=timezone.utc)  # 15:32 em America/Sao_Paulo


def _status_row(unit_key: str, unit_code: str, name: str, hours_ago: float | None) -> dict:
    return {
        "unit_key": unit_key,
        "unit_code": unit_code,
        "displayed_name": name,
        "canonical_name": name,
        "updated_at": None if hours_ago is None else NOW - timedelta(hours=hours_ago),
    }


class ThresholdTests(unittest.TestCase):
    def test_only_units_above_threshold_are_selected(self) -> None:
        rows = [
            _status_row("a", "upa_barris", "UPA BARRIS", 14.0),
            _status_row("b", "upa_brotas", "UPA BROTAS", 11.9),
            _status_row("c", "upa_paripe", "UPA PARIPE", 12.0),
        ]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0)

        self.assertEqual([item["unit_key"] for item in offenders], ["a", "c"])

    def test_threshold_is_independent_from_telegram_watcher(self) -> None:
        """10h dispara o Telegram, mas não este canal (default 12h)."""
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 10.5)]

        self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0), [])
        self.assertEqual(len(whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=10.0)), 1)

    def test_offenders_are_ordered_by_silence_desc(self) -> None:
        rows = [
            _status_row("a", "upa_barris", "UPA BARRIS", 13.0),
            _status_row("b", "upa_brotas", "UPA BROTAS", 20.0),
            _status_row("c", "upa_paripe", "UPA PARIPE", 15.0),
        ]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0)

        self.assertEqual([item["unit_key"] for item in offenders], ["b", "c", "a"])

    def test_row_without_timestamp_is_ignored(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", None)]

        self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0), [])


class CooldownTests(unittest.TestCase):
    def test_unit_alerted_recently_is_skipped(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 30.0)]

        offenders = whatsapp_alerts.select_stale_offenders(
            rows,
            NOW,
            threshold_hours=12.0,
            cooldown_hours=6.0,
            last_sent_by_unit={"a": NOW - timedelta(hours=5, minutes=59)},
        )

        self.assertEqual(offenders, [])

    def test_unit_is_alerted_again_after_cooldown(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 30.0)]

        offenders = whatsapp_alerts.select_stale_offenders(
            rows,
            NOW,
            threshold_hours=12.0,
            cooldown_hours=6.0,
            last_sent_by_unit={"a": NOW - timedelta(hours=6, minutes=1)},
        )

        self.assertEqual(len(offenders), 1)

    def test_cooldown_is_per_unit(self) -> None:
        rows = [
            _status_row("a", "upa_barris", "UPA BARRIS", 30.0),
            _status_row("b", "upa_brotas", "UPA BROTAS", 13.0),
        ]

        offenders = whatsapp_alerts.select_stale_offenders(
            rows,
            NOW,
            threshold_hours=12.0,
            cooldown_hours=6.0,
            last_sent_by_unit={"a": NOW - timedelta(hours=1)},
        )

        self.assertEqual([item["unit_key"] for item in offenders], ["b"])

    def test_state_read_from_iso_string_is_accepted(self) -> None:
        """O estado vem do Postgres, mas um ISO string não pode virar envio."""
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 30.0)]

        offenders = whatsapp_alerts.select_stale_offenders(
            rows,
            NOW,
            threshold_hours=12.0,
            cooldown_hours=6.0,
            last_sent_by_unit={"a": (NOW - timedelta(hours=2)).isoformat()},
        )

        self.assertEqual(offenders, [])


class DispatchTests(unittest.TestCase):
    """Nenhum destes testes pode encostar na rede."""

    def test_dry_run_does_not_call_the_gateway(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", False), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_empty_destination_is_dry_run_even_when_enabled(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", ""), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_empty_message_is_never_sent(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("   ")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_enabled_sends_once_to_the_configured_number(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertTrue(sent)
        send.assert_called_once_with("5571999999999", "🔴 alerta de teste")

    def test_gateway_failure_never_raises_and_reports_not_sent(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(
                    whatsapp_alerts, "send_gateway_message", side_effect=OSError("connection refused")
                ):
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        # False => o watcher não grava cooldown e tenta de novo na próxima
        # varredura, em vez de perder o aviso.
        self.assertFalse(sent)

    def test_gateway_payload_matches_the_documented_contract(self) -> None:
        captured: dict = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"code":"SUCCESS"}'

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["body"] = req.data.decode("utf-8")
            return _Response()

        with mock.patch.object(whatsapp_alerts.request, "urlopen", _fake_urlopen):
            whatsapp_alerts.send_gateway_message(
                "5571999999999",
                "oi",
                url="http://127.0.0.1:3080",
                auth="admin:segredo",
            )

        import base64
        import json

        self.assertEqual(captured["url"], "http://127.0.0.1:3080/send/message")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(json.loads(captured["body"]), {"phone": "5571999999999", "message": "oi"})
        expected = base64.b64encode(b"admin:segredo").decode("ascii")
        self.assertEqual(captured["headers"]["authorization"], f"Basic {expected}")
        self.assertEqual(captured["headers"]["content-type"], "application/json")


class MessageFormattingTests(unittest.TestCase):
    def test_line_per_unit_with_hours_and_coordinator(self) -> None:
        offenders = whatsapp_alerts.select_stale_offenders(
            [
                _status_row("a", "upa_barris", "UPA BARRIS", 14.5),
                _status_row("b", "pa_sao_marcos", "PA SÃO MARCOS", 13.0),
            ],
            NOW,
            threshold_hours=12.0,
        )

        text = build_whatsapp_stale_alert_text(
            offenders,
            {"upa_barris": ["JOILSON SANTOS"]},
            NOW,
            12.0,
        )

        self.assertIn("🔴 *UPA sem giro há mais de 12h*", text)
        self.assertIn("• UPA BARRIS — 14h30 · 👤 JOILSON SANTOS", text)
        self.assertIn("• PA SÃO MARCOS — 13h00 · 👤 sem coordenador", text)
        self.assertIn("Giro de Leitos · aviso automático", text)

    def test_message_stays_short(self) -> None:
        """Alerta, não relatório: cabe na tela sem rolar."""
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row(f"u{i}", f"code{i}", f"UPA {i}", 13.0 + i) for i in range(3)],
            NOW,
            threshold_hours=12.0,
        )

        text = build_whatsapp_stale_alert_text(offenders, {}, NOW, 12.0)

        self.assertLessEqual(len(text.splitlines()), 8)
        self.assertNotIn("Vermelha", text)
        self.assertNotIn("<b>", text)

    def test_extra_units_are_announced_not_silently_dropped(self) -> None:
        count = MAX_WHATSAPP_ALERT_UNITS + 3
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row(f"u{i}", f"code{i}", f"UPA {i}", 13.0 + i) for i in range(count)],
            NOW,
            threshold_hours=12.0,
        )

        text = build_whatsapp_stale_alert_text(offenders, {}, NOW, 12.0)

        named = [line for line in text.splitlines() if line.startswith("• UPA ")]
        self.assertEqual(len(named), MAX_WHATSAPP_ALERT_UNITS)
        self.assertIn("• +3 unidade(s) também sem giro", text)

    def test_many_coordinators_are_trimmed(self) -> None:
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row("a", "upa_barris", "UPA BARRIS", 14.0)], NOW, threshold_hours=12.0
        )

        text = build_whatsapp_stale_alert_text(
            offenders, {"upa_barris": ["ANA", "BRUNO", "CARLA", "DIEGO"]}, NOW, 12.0
        )

        self.assertIn("👤 ANA · BRUNO +2", text)


class NoOffendersTests(unittest.TestCase):
    def test_nothing_is_sent_when_no_unit_violates(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 3.0)]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0)

        self.assertEqual(offenders, [])
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_TO", "5571999999999"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            # Espelha o caminho de main._notify_stale_whatsapp: sem infrator,
            # nem chega a montar mensagem.
            if offenders:  # pragma: no cover - guarda do próprio teste
                whatsapp_alerts.dispatch_stale_alert("não deveria existir")

        send.assert_not_called()


class WatcherIntegrationTests(unittest.TestCase):
    def test_watcher_hook_persists_cooldown_only_after_a_real_send(self) -> None:
        import main

        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 20.0)]

        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", return_value={}), \
                mock.patch.object(main, "get_unit_responsibles", return_value={}), \
                mock.patch.object(main, "dispatch_stale_alert", return_value=True) as dispatch, \
                mock.patch.object(main, "record_whatsapp_alert_sent") as record:
            main._notify_stale_whatsapp(rows, NOW)

        dispatch.assert_called_once()
        record.assert_called_once_with(["a"], NOW)

    def test_watcher_hook_does_not_persist_when_nothing_was_sent(self) -> None:
        import main

        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 20.0)]

        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", return_value={}), \
                mock.patch.object(main, "get_unit_responsibles", return_value={}), \
                mock.patch.object(main, "dispatch_stale_alert", return_value=False), \
                mock.patch.object(main, "record_whatsapp_alert_sent") as record:
            main._notify_stale_whatsapp(rows, NOW)

        record.assert_not_called()

    def test_watcher_hook_swallows_failures(self) -> None:
        """Gateway/banco quebrado não pode derrubar o watcher."""
        import main

        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 20.0)]

        with mock.patch.object(main, "is_database_configured", return_value=True), \
                mock.patch.object(main, "get_whatsapp_alert_state", side_effect=RuntimeError("db down")):
            main._notify_stale_whatsapp(rows, NOW)  # não levanta


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
