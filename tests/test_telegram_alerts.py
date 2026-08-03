from datetime import datetime, timedelta, timezone

from db import _emit_transition_alerts
from main import build_alerts_text, build_system_status_text, build_system_summary_text


def _status_row(name: str) -> dict:
    return {
        "unit_key": name.lower(),
        "displayed_name": name,
        "has_orthopedist": True,
        "has_surgeon": False,
        "has_psychiatrist": False,
        "payload": {
            "data": {
                "upa_name": name,
                "rooms": {
                    "red_room": {"occupied": 1, "capacity": 2, "ratio": "01/02", "has_capacity": True},
                    "yellow_male": {"occupied": 2, "capacity": 3, "ratio": "02/03", "has_capacity": True},
                    "yellow_female": {"occupied": 3, "capacity": 3, "ratio": "03/03", "has_capacity": False},
                    "isolation_mode": "total",
                },
            }
        },
    }


def test_resumo_is_short_and_status_keeps_unit_details() -> None:
    row = _status_row("UPA TESTE")

    summary = build_system_summary_text([row])
    status = build_system_status_text([row])

    assert "📍 RESUMO AGORA" in summary
    assert "🔴 Vermelha: UPA TESTE" in summary
    assert "- Vermelha: 01/02" not in summary
    assert "🏥 UPA TESTE" in status
    assert "- Vermelha: 01/02" in status


def test_alertas_show_elapsed_time_recency_and_subject() -> None:
    now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
    text = build_alerts_text(
        [
            {
                "unit_name": "UPA TESTE",
                "alert_type": "red_vacancy_opened",
                "message": "UPA TESTE passou a ficar COM vaga.",
                "created_at": now - timedelta(minutes=8),
            }
        ],
        now=now,
    )

    assert "🕐 8min 🟢 🔴 UPA TESTE" in text
    assert "passou a ficar COM vaga" in text


class _Cursor:
    def __init__(self) -> None:
        self.count = 0

    def execute(self, _query: str, _params: tuple) -> None:
        self.count += 1

    def fetchone(self) -> dict:
        return {"id": self.count, "created_at": datetime.now(timezone.utc)}


def test_transition_alerts_cover_vacancy_closing_and_specialists() -> None:
    previous = {
        "has_orthopedist": True,
        "has_surgeon": False,
        "has_psychiatrist": False,
        "payload": {
            "data": {
                "rooms": {
                    "red_room": {"occupied": 1, "capacity": 2, "ratio": "01/02"},
                    "yellow_room": {"occupied": 3, "capacity": 3, "ratio": "03/03"},
                }
            }
        },
    }
    current_rooms = {
        "red_room": {"occupied": 2, "capacity": 2, "ratio": "02/02"},
        "yellow_room": {"occupied": 3, "capacity": 3, "ratio": "03/03"},
    }

    alerts = _emit_transition_alerts(
        _Cursor(),
        previous_status=previous,
        unit_key="upa_teste",
        unit_code="upa_teste",
        unit_name="UPA TESTE",
        event_id=2,
        red_occupied=2,
        red_capacity=2,
        yellow_occupied=3,
        yellow_capacity=3,
        isolation_total_occupied=None,
        isolation_total_capacity=None,
        isolation_female_occupied=None,
        isolation_female_capacity=None,
        isolation_male_occupied=None,
        isolation_male_capacity=None,
        has_orthopedist=False,
        has_surgeon=True,
        has_psychiatrist=False,
        current_rooms=current_rooms,
    )

    alert_types = {alert["alert_type"] for alert in alerts}
    assert "red_vacancy_closed" in alert_types
    assert "orthopedist_changed" in alert_types
    assert "surgeon_changed" in alert_types

