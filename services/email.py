"""Transactional e-mail via SMTP (Gmail / Google Workspace).

Uses only the stdlib (``smtplib`` + ``email.message.EmailMessage``) with
STARTTLS. Configuration comes entirely from environment variables:

- ``SMTP_HOST`` (default ``smtp.gmail.com``)
- ``SMTP_PORT`` (default ``587``)
- ``SMTP_USER`` — sending Google account e-mail (required)
- ``SMTP_PASS`` — Google app password (required)
- ``SMTP_FROM`` (default = ``SMTP_USER``)

``send_email`` raises (and logs) a clear error when the SMTP credentials are
missing or the send fails, so callers can decide whether to surface or swallow.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP_USER/SMTP_PASS are absent."""


def _smtp_config() -> dict[str, str | int]:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    try:
        port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    except ValueError:
        port = 587
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip() or user
    return {"host": host, "port": port, "user": user, "password": password, "sender": sender}


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    """Send a single e-mail via STARTTLS. Raises on misconfiguration/failure.

    A plain-text body is always sent; ``body_html`` is attached as an
    ``text/html`` alternative when provided.
    """
    cfg = _smtp_config()
    if not cfg["user"] or not cfg["password"]:
        logger.error("send_email: SMTP_USER/SMTP_PASS ausentes — não é possível enviar e-mail.")
        raise EmailNotConfigured("SMTP_USER/SMTP_PASS não configurados.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = str(cfg["sender"])
    msg["To"] = to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(str(cfg["host"]), int(cfg["port"]), timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(str(cfg["user"]), str(cfg["password"]))
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        logger.error("send_email: falha ao enviar para %s: %s", to, exc, exc_info=True)
        raise

    logger.info("send_email: enviado para %s (subject=%r)", to, subject)
