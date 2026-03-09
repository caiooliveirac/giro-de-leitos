from __future__ import annotations

import json
import os
import sys
from urllib import request

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://mnrs.com.br").rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
PUBLIC_WEBHOOK_PATH = os.getenv("PUBLIC_WEBHOOK_PATH", "/giro/api/webhook/telegram").strip() or "/giro/api/webhook/telegram"

if not BOT_TOKEN:
    print("Erro: defina TELEGRAM_BOT_TOKEN antes de executar.", file=sys.stderr)
    sys.exit(1)

payload: dict[str, object] = {
    "url": f"{PUBLIC_BASE_URL}{PUBLIC_WEBHOOK_PATH}",
    "allowed_updates": ["message", "edited_message"],
    "drop_pending_updates": False,
}

if WEBHOOK_SECRET:
    payload["secret_token"] = WEBHOOK_SECRET

req = request.Request(
    url=f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with request.urlopen(req, timeout=20) as response:
    body = response.read().decode("utf-8")
    print(body)
