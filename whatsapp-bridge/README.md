# WhatsApp Bridge – Giro de Leitos

Serviço sidecar que conecta diretamente ao WhatsApp via **Baileys** (protocolo multi-device) e encaminha automaticamente as mensagens do(s) grupo(s) configurado(s) para a API FastAPI do Giro de Leitos.

**Substitui o fluxo manual:** não é mais necessário copiar/colar do WhatsApp no Telegram.

---

## Como funciona

```
WhatsApp Group  ──▶  whatsapp-bridge (Baileys)  ──POST──▶  FastAPI /api/ingest/manual
                           │                                        │
                     (WebSocket do WA)                      (parser + broadcast WS)
```

1. O bridge se conecta ao WhatsApp como um **dispositivo secundário** (como o WhatsApp Web).
2. Escuta mensagens de texto dos grupos configurados.
3. Encaminha cada mensagem nova via HTTP POST para a API existente.
4. O parser extrai os dados e publica no dashboard em tempo real.

---

## Setup inicial (3 passos)

### 1. Descobrir o ID do grupo

```bash
# Sobe o bridge em modo de listagem de grupos
WA_LIST_GROUPS=true docker compose up whatsapp-bridge

# Escaneie o QR code no terminal
# O bridge vai listar todos os grupos e seus IDs, depois encerrar
```

Anote o ID do grupo desejado (formato: `120363XXXXXXXXX@g.us`).

### 2. Configurar e subir

Crie/edite seu `.env`:

```env
# IDs dos grupos separados por vírgula
WHATSAPP_GROUP_IDS=120363XXXXXXXXX@g.us

# Opcional: modo de teste (imprime sem enviar para a API)
WA_DRY_RUN=false
```

```bash
docker compose up -d
```

### 3. Escanear o QR code (primeira vez)

```bash
docker compose logs -f whatsapp-bridge
```

Aparecerá um QR code no terminal. Escaneie com:  
**WhatsApp → Configurações → Dispositivos conectados → Conectar dispositivo**

A sessão fica salva no volume `wa_auth_info` — só precisa escanear uma vez.

---

## Variáveis de ambiente

| Variável | Default | Descrição |
|---|---|---|
| `WHATSAPP_GROUP_IDS` | _(vazio = todos)_ | IDs dos grupos separados por vírgula |
| `API_BASE_URL` | `http://parser-api:8000` | URL da API FastAPI |
| `API_INGEST_PATH` | `/api/ingest/manual` | Caminho do endpoint de ingestão |
| `WA_LOG_LEVEL` | `warn` | Nível de log (silent, fatal, error, warn, info, debug, trace) |
| `WA_DRY_RUN` | `false` | Se `true`, só imprime as mensagens sem enviar |
| `WA_LIST_GROUPS` | `false` | Se `true`, lista grupos e encerra (para descobrir IDs) |

---

## Executar fora do Docker (desenvolvimento)

```bash
cd whatsapp-bridge
npm install
# Listar grupos para descobrir IDs
LIST_GROUPS=true API_BASE_URL=http://localhost:8000 node index.mjs
# Modo normal
WHATSAPP_GROUP_IDS=120363XXXXXXXXX@g.us API_BASE_URL=http://localhost:8000 node index.mjs
```

---

## Notas importantes

- **Não é a API oficial do WhatsApp** — usa engenharia reversa do protocolo. Funciona de forma estável, mas pode quebrar se o WhatsApp mudar o protocolo. Baileys é ativamente mantido e atualiza rápido.
- **Uma conta WhatsApp por bridge** — o bridge ocupa um slot de "dispositivo conectado" (máximo 4 por conta).
- **Desconexão** — se o dispositivo principal ficar offline por >14 dias, o WhatsApp desconecta os dispositivos secundários e será necessário escanear o QR novamente.
- **Mensagens curtas (< 10 chars) são ignoradas** — para evitar ruído de "ok", "👍", etc.
- **Deduplicação** — mensagens já encaminhadas não são reenviadas em reconexões.
