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

from reports import GIRO_SLA, MAX_WHATSAPP_ALERT_UNITS, build_group_stale_request_text  # noqa: E402
from services import whatsapp_alerts  # noqa: E402


NOW = datetime(2026, 8, 3, 18, 32, tzinfo=timezone.utc)  # 15:32 em America/Sao_Paulo
_LOCAL_TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo não tem horário de verão


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    """Instante escrito em hora LOCAL — é como o SLA por turno é definido."""
    return datetime(2026, 8, day, hour, minute, tzinfo=_LOCAL_TZ)


def _status_row(unit_key: str, unit_code: str, name: str, hours_ago: float | None) -> dict:
    return {
        "unit_key": unit_key,
        "unit_code": unit_code,
        "displayed_name": name,
        "canonical_name": name,
        "updated_at": None if hours_ago is None else NOW - timedelta(hours=hours_ago),
    }


def _status_row_at(unit_key: str, unit_code: str, name: str, updated_at: datetime) -> dict:
    return {
        "unit_key": unit_key,
        "unit_code": unit_code,
        "displayed_name": name,
        "canonical_name": name,
        "updated_at": updated_at,
    }


class ShiftSlaTests(unittest.TestCase):
    """O limiar padrão é o SLA por turno do /cobranca, não um número fixo.

    8h de silêncio às 10h da manhã é falha; 8h de madrugada é o combinado. Um
    limiar único errava um dos dois casos — e errar para mais cala o canal, que
    é a falha irreversível deste módulo.
    """

    def test_daytime_gap_above_six_hours_is_an_offender(self) -> None:
        # 08:32 → 15:32 local: 7h inteiras dentro do turno diurno.
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 7.0)]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW)

        self.assertEqual([item["unit_key"] for item in offenders], ["a"])
        self.assertEqual(offenders[0]["shift"], "diurno")
        self.assertEqual(offenders[0]["limit_hours"], GIRO_SLA["day_max_gap_hours"])

    def test_daytime_gap_below_six_hours_is_not(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 5.0)]

        self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, NOW), [])

    def test_eight_hours_at_night_is_within_the_combined_limit(self) -> None:
        """O mesmo silêncio que reprova de dia passa de madrugada."""
        now = _local(4, 5, 0)  # 05:00 local, turno noturno
        rows = [_status_row_at("a", "upa_barris", "UPA BARRIS", _local(3, 21, 0))]

        self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, now), [])

    def test_night_gap_above_twelve_hours_is_an_offender(self) -> None:
        now = _local(4, 5, 0)
        # 16:00 → 05:00: 3h de dia e 10h de noite; a maior parte é noturna.
        rows = [_status_row_at("a", "upa_barris", "UPA BARRIS", _local(3, 16, 0))]

        offenders = whatsapp_alerts.select_stale_offenders(rows, now)

        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0]["shift"], "noturno")
        self.assertEqual(offenders[0]["limit_hours"], GIRO_SLA["night_max_gap_hours"])

    def test_tolerance_of_the_cobranca_is_honoured(self) -> None:
        """6h00m30s não é violação — o arredondamento não pode virar cobrança."""
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 6.0 + 30 / 3600)]

        self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, NOW), [])

        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 6.2)]
        self.assertEqual(len(whatsapp_alerts.select_stale_offenders(rows, NOW)), 1)

    def test_env_override_replaces_the_shift_rule_with_a_fixed_number(self) -> None:
        """WHATSAPP_ALERT_HOURS existe como escape — e desliga o turno."""
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 7.0)]

        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_HOURS_OVERRIDE", 12.0):
            self.assertEqual(whatsapp_alerts.select_stale_offenders(rows, NOW), [])

        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_HOURS_OVERRIDE", None):
            self.assertEqual(len(whatsapp_alerts.select_stale_offenders(rows, NOW)), 1)

    def test_cooldown_still_applies_under_the_shift_rule(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 9.0)]

        offenders = whatsapp_alerts.select_stale_offenders(
            rows, NOW, cooldown_hours=6.0, last_sent_by_unit={"a": NOW - timedelta(hours=1)}
        )

        self.assertEqual(offenders, [])

    def test_alert_text_shows_the_shift_limit_that_was_broken(self) -> None:
        rows = [
            _status_row("a", "upa_barris", "UPA BARRIS", 7.0),
            _status_row_at("b", "pa_sao_marcos", "PA SÃO MARCOS", _local(2, 20, 0)),
        ]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW)
        text = build_group_stale_request_text(offenders, NOW)

        self.assertIn("🔄 *Giro de leitos — atualização pendente*", text)
        self.assertIn("UPA BARRIS", text.upper())
        self.assertIn("7h00", text)

    def test_alert_text_stays_short_under_the_shift_rule(self) -> None:
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row(f"u{i}", f"code{i}", f"UPA {i}", 13.0 + i) for i in range(3)], NOW
        )

        text = build_group_stale_request_text(offenders, NOW)

        self.assertLessEqual(len(text.splitlines()), 12)


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
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_empty_destination_is_dry_run_even_when_enabled(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", ""), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_empty_message_is_never_sent(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("   ")

        self.assertFalse(sent)
        send.assert_not_called()

    def test_enabled_sends_once_to_the_group(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
                mock.patch.object(whatsapp_alerts, "send_gateway_message") as send:
            sent = whatsapp_alerts.dispatch_stale_alert("🔴 alerta de teste")

        self.assertTrue(sent)
        send.assert_called_once_with("120363@g.us", "🔴 alerta de teste", mentions=[])

    def test_gateway_failure_never_raises_and_reports_not_sent(self) -> None:
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
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
    def test_line_per_unit_with_the_gap(self) -> None:
        offenders = whatsapp_alerts.select_stale_offenders(
            [
                _status_row("a", "upa_barris", "UPA BARRIS", 14.5),
                _status_row("b", "pa_sao_marcos", "PA SÃO MARCOS", 13.0),
            ],
            NOW,
            threshold_hours=12.0,
        )

        text = build_group_stale_request_text(offenders, NOW, 12.0)

        self.assertIn("🔄 *Giro de leitos — atualização pendente*", text)
        self.assertIn("• UPA BARRIS — 14h30", text)
        self.assertIn("• PA SÃO MARCOS — 13h00", text)
        # No grupo não se nomeia coordenador: quem lê é a própria unidade.
        self.assertNotIn("👤", text)
        self.assertIn("Giro de Leitos · aviso automático", text)

    def test_message_stays_short(self) -> None:
        """Alerta, não relatório: cabe na tela sem rolar."""
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row(f"u{i}", f"code{i}", f"UPA {i}", 13.0 + i) for i in range(3)],
            NOW,
            threshold_hours=12.0,
        )

        text = build_group_stale_request_text(offenders, NOW)

        self.assertLessEqual(len(text.splitlines()), 12)
        self.assertNotIn("Vermelha", text)
        self.assertNotIn("<b>", text)

    def test_extra_units_are_announced_not_silently_dropped(self) -> None:
        count = MAX_WHATSAPP_ALERT_UNITS + 3
        offenders = whatsapp_alerts.select_stale_offenders(
            [_status_row(f"u{i}", f"code{i}", f"UPA {i}", 13.0 + i) for i in range(count)],
            NOW,
            threshold_hours=12.0,
        )

        text = build_group_stale_request_text(offenders, NOW)

        named = [line for line in text.splitlines() if line.startswith("• UPA ")]
        self.assertEqual(len(named), MAX_WHATSAPP_ALERT_UNITS)
        self.assertIn("• +3 unidade(s) também pendente(s)", text)


class NoOffendersTests(unittest.TestCase):
    def test_nothing_is_sent_when_no_unit_violates(self) -> None:
        rows = [_status_row("a", "upa_barris", "UPA BARRIS", 3.0)]

        offenders = whatsapp_alerts.select_stale_offenders(rows, NOW, threshold_hours=12.0)

        self.assertEqual(offenders, [])
        with mock.patch.object(whatsapp_alerts, "WHATSAPP_ALERT_ENABLED", True), \
                mock.patch.object(whatsapp_alerts, "WHATSAPP_GROUP_TO", "120363@g.us"), \
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


def test_stale_units_sla_turno_usa_a_mesma_regra_do_cobranca():
    """`?sla=turno` não pode ter um SLA próprio: quem consome de fora (o `tom`)
    precisa concordar com a tela e com a cobrança. Aqui só se garante que o
    endpoint delega para select_stale_offenders — sem cooldown, porque quem
    perguntou quer a lista de agora."""
    from datetime import datetime, timedelta, timezone

    from services.whatsapp_alerts import select_stale_offenders

    now = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)  # 12h local: turno diurno
    linhas = [
        {"unit_key": "upa_a", "unit_code": "UPA_A", "displayed_name": "UPA A",
         "updated_at": now - timedelta(hours=8)},   # estourou o limite diurno (6h)
        {"unit_key": "upa_b", "unit_code": "UPA_B", "displayed_name": "UPA B",
         "updated_at": now - timedelta(hours=2)},   # dentro do combinado
    ]

    ofensores = select_stale_offenders(linhas, now, cooldown_hours=0)
    assert [item["unit_key"] for item in ofensores] == ["upa_a"]
    assert ofensores[0]["limit_hours"] == 6

    # O cooldown é regra de quem avisa sozinho: com ele zerado, um aviso
    # recente não esconde a unidade de quem perguntou.
    com_aviso_recente = select_stale_offenders(
        linhas, now, cooldown_hours=0, last_sent_by_unit={"upa_a": now - timedelta(minutes=5)}
    )
    assert [item["unit_key"] for item in com_aviso_recente] == ["upa_a"]
