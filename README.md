# Giro de Leitos

Microsserviço responsável por receber os giros das UPAs e PAs, interpretar o texto, consolidar o último estado por unidade e alimentar a visão operacional exibida na tabela de regulação.

Hoje o serviço já cobre:

- entrada manual de giros
- webhook do Telegram
- persistência em PostgreSQL
- histórico de eventos
- resumo consolidado por unidade
- geração de alertas de mudança relevante
- integração com a aba `UPAs` do painel principal

---

## Objetivo

O serviço foi criado para transformar mensagens livres de giro de leitos em informação operacional utilizável no plantão.

Na prática, ele permite:

- identificar rapidamente quais unidades têm vaga na vermelha
- separar vaga de amarela por masculino e feminino
- destacar disponibilidade de isolamento adulto
- indicar presença de ortopedista e psiquiatria
- manter visível a última atualização por unidade
- preservar unidades cadastradas mesmo quando ainda não enviaram giro no dia

---

## Como o fluxo funciona

1. O giro chega por entrada manual ou Telegram.
2. O texto bruto é parseado.
3. O evento é salvo em `parsed_events`.
4. O último estado consolidado da unidade é atualizado em `current_unit_status`.
5. Mudanças relevantes podem gerar registros em `alert_events`.
6. A rota `/api/summary` entrega o resumo já pronto para a interface da tabela.

---

## O que o parser identifica

### Unidade

- nome da unidade informada no texto
- nome consolidado da unidade no cadastro interno
- aliases conhecidos

### Leitos

- sala vermelha
- sala amarela total
- sala amarela masculina
- sala amarela feminina
- isolamento unissex
- isolamento feminino
- isolamento masculino
- outros leitos assistenciais relevantes

### Especialidades

- ortopedia
- cirurgia
- psiquiatria

### Metadados

- horário oficial do giro, quando vier no texto
- horário de ingestão
- avisos do parser
- criticidade da vermelha

---

## Regras operacionais aplicadas

### Amarela por sexo

Quando a mensagem traz a amarela total e também a separação por sexo, o serviço salva os dois níveis.

Exemplos:

- `SALA AMARELA (12/12)` + `(06/06) FEMININO` + `(06/06) MASCULINO`
- `SALA AMARELA: (04/04)` + `(03) FEMININO` + `(01) MASCULINO`

No segundo caso, quando o texto não informa a capacidade separada por sexo, mas a amarela total está completamente ocupada, o serviço infere:

- feminino `03/03`
- masculino `01/01`

Isso evita que a interface exiba `n/i` onde o dado já está suficientemente claro para a operação.

### Orlando Imbassahy

`UPA BAIRRO DA PAZ - ORLANDO IMBASSAHY` é tratada como unidade sem sala amarela.

### Isolamento

Na visão operacional principal, o foco é isolamento adulto:

- feminino
- masculino
- unissex

Isolamento pediátrico continua salvo no payload e pode ser visto no detalhe da unidade, mas não entra no cálculo resumido da tela principal.

### Outros leitos

Leitos pediátricos não entram nos totais visíveis da visão operacional.

Além disso, blocos com capacidade zero não são exibidos como informação operacional útil. Exemplos que ficam ocultos na tela resumida:

- `00/00`
- `01/00`
- `04/00`
- `16/00`

---

## Integração com a tabela

O frontend da tabela consome principalmente a rota:

- `GET /api/summary`

Essa rota retorna:

- `units`: estado consolidado por unidade
- `priority_buckets`: agrupamentos usados na visão operacional

Os buckets atuais são:

- `red_priority`
- `yellow_male_priority`
- `yellow_female_priority`
- `isolation_priority`
- `other_beds`
- `with_orthopedist`

Na aba `UPAs`, a interface usa isso para mostrar:

- indicadores rápidos
- grupos de prioridade
- cards por unidade
- modal com texto bruto e payload parseado
- edição do horário oficial do último giro

---

## Rotas principais

### Saúde

- `GET /health`
- `GET /api/health`

### Eventos e resumo

- `GET /api/last-event`
- `GET /api/history`
- `GET /api/summary`
- `GET /api/alerts`
- `GET /api/units`

### Entrada de dados

- `POST /api/ingest/manual`
- `POST /api/webhook/telegram`
- `POST /api/webhook/whatsapp`

### Utilidades

- `GET /api/playground`
- `GET /api/telegram/status`
- `PATCH /api/units/{unit_key}/reported-at`
- `WS /ws/dashboard`

---

## Exemplo de entrada manual

```json
{
  "text": "UPA BROTAS\nSALA VERMELHA 03/04\nSALA AMARELA 05/08\nISOLAMENTO MASC 01/02\nISOLAMENTO FEM 00/02\nORTOPEDIA: SIM",
  "source": "manual",
  "official_at": "2026-03-07T10:30:00Z"
}
```

### Campos aceitos

- `text`: texto bruto do giro
- `source`: origem lógica da entrada
- `unit_hint`: dica opcional de unidade
- `official_at`: horário oficial do giro, quando for informado manualmente

---

## Telegram

O webhook do Telegram já está preparado para:

- receber `message`
- receber `edited_message`
- parsear automaticamente o texto
- responder no próprio chat com resumo do parse
- atender comandos operacionais

### Comandos prontos

Operacionais (abertos, mesmo nível de sempre):

- `/resumo`, `/status`, `/giro` — situação por unidade
- `/alertas` — últimos alertas

Relatórios de gestão (**restritos** aos chats de admin — ver
`TELEGRAM_REPORT_CHAT_IDS`):

| Comando | Argumento | O que responde |
|---|---|---|
| `/vagas` | `vermelha`, `amarela`, `iso`, `outros` | Onde tem vaga **agora**, com destaque de quem está acima da capacidade e carimbo de dado velho |
| `/cobranca` | dias (padrão `7`) | Tempo sem postar giro por unidade — gap atual e piores gaps do período — nomeando o coordenador cadastrado |
| `/lotacao` | dias (padrão `7`) | Tempo acumulado **acima da capacidade** por unidade no período |
| `/naoparseados` | dias (padrão `7`) | Mensagens que pareciam giro e não puderam ser publicadas: quantas, de qual origem e exemplos |

Notas de implementação:

- Todo comando aceita o sufixo de grupo (`/vagas@girodeleitos_bot`) e argumento.
- Toda resposta passa por `send_telegram_message`, que corta a mensagem em
  partes de até 3.500 caracteres — o limite do `sendMessage` é 4.096 e o
  `/resumo` já o excedia, devolvendo 400 e deixando o usuário sem resposta.
- Horários saem em `REPORT_TIMEZONE` (default `America/Sao_Paulo`).
- Nenhum dado de paciente e nenhum telefone aparece nas respostas — só
  contagens e nomes de coordenador.
- `/cobranca` usa `parsed_events.created_at` (chegada real), não o horário
  digitado no texto, que tem drift documentado.
- `/lotacao` integra `is_over_capacity` entre giros consecutivos, com teto de
  6h por giro: passado esse tempo o dado está velho e para de contar.

### Variáveis de ambiente

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_ADMIN_CHAT_ID` — também é a allowlist base dos relatórios
- `TELEGRAM_REPORT_CHAT_IDS` — chats extras autorizados (lista por vírgula)
- `REPORT_TIMEZONE` — default `America/Sao_Paulo`
- `PUBLIC_BASE_URL`
- `PUBLIC_WEBHOOK_PATH`

### URLs úteis em produção

- `https://mnrs.com.br/giro/api/telegram/status`
- `https://mnrs.com.br/giro/api/webhook/telegram`

---

## Alerta de silêncio no WhatsApp do gestor

Canal **adicional** ao watcher de Telegram do admin (`STALE_ALERT_HOURS`, 10h),
implementado em `services/whatsapp_alerts.py` e disparado pela mesma varredura
de `main._stale_units_watcher` (a cada `STALE_CHECK_INTERVAL_MINUTES`).

Não é relatório: **uma** mensagem curta, uma linha por UPA com horas sem giro e
coordenador. Sem unidade acima do limiar, nada é enviado.

Três travas, nesta ordem:

1. **Limiar próprio e mais alto** (`WHATSAPP_ALERT_HOURS`, default 12h) — o
   Telegram é o acompanhamento do dia a dia; aqui só entra o que já virou
   cobrança. Compartilhar o limiar de 10h transformaria o canal em spam.
2. **Cooldown por unidade** (`WHATSAPP_ALERT_COOLDOWN_HOURS`, default 6h), com
   estado em `whatsapp_alert_state` — persistido porque deploy/restart não
   pode zerar o anti-spam.
3. **Nasce desligado** — sem `WHATSAPP_ALERT_ENABLED=true` **e** destino
   preenchido, o código só loga o que enviaria (dry-run) e não chama o gateway.

O envio usa o gateway whatsmeow já em produção
(`aldinokemal2104/go-whatsapp-web-multidevice`): `POST /send/message` com
`{"phone": ..., "message": ...}` e HTTP basic auth. Falha do gateway nunca
propaga — `dispatch_stale_alert` devolve `False`, o cooldown **não** é gravado
e a próxima varredura tenta de novo.

### Variáveis de ambiente

| Variável | Default | Observação |
|---|---|---|
| `WHATSAPP_ALERT_ENABLED` | `false` | Interruptor geral |
| `WHATSAPP_ALERT_TO` | vazio | Número com DDI (`5571999999999`) ou JID; vazio = desligado |
| `WHATSAPP_GW_URL` | `http://127.0.0.1:3080` | Da API (em container) use `http://host.docker.internal:3080` |
| `WHATSAPP_GW_AUTH` | vazio | `usuario:segredo` — o mesmo `APP_BASIC_AUTH` do container `whatsmeow-gw` |
| `WHATSAPP_ALERT_HOURS` | `12` | Limiar, separado do `STALE_ALERT_HOURS` |
| `WHATSAPP_ALERT_COOLDOWN_HOURS` | `6` | Janela mínima entre avisos da mesma unidade |

---

## Persistência

O banco mantém três camadas principais:

### `parsed_events`

Armazena cada giro recebido como evento histórico.

### `current_unit_status`

Mantém o último estado consolidado de cada unidade.

### `alert_events`

Armazena alertas gerados a partir de transições relevantes, como:

- nova vaga na vermelha
- nova vaga amarela masculina
- nova vaga amarela feminina
- disponibilidade de isolamento adulto
- mudança de ortopedia
- mudança de psiquiatria

### `whatsapp_alert_state`

Uma linha por unidade com o horário do último alerta de silêncio **entregue**
no WhatsApp do gestor — é o cooldown do canal (dry-run e falha de rede não
gravam).

---

## Cadastro de unidades

As unidades conhecidas ficam no cadastro interno e são usadas para:

- normalizar nomes
- unir aliases diferentes na mesma unidade consolidada
- manter unidades visíveis no resumo mesmo sem giro recente

Isso é o que permite, por exemplo:

- consolidar `UPA Adroaldo Albergaria` em `UPA PERIPERI`
- manter `UPA SAN MARTIN` visível mesmo quando ainda não houve giro recente

---

## Horário oficial do giro

O sistema diferencia dois horários:

- horário oficial informado pela unidade
- horário em que a mensagem foi recebida

Quando o horário oficial estiver no texto, ele é priorizado.

Quando não estiver:

- pode ser enviado manualmente na entrada
- pode ser corrigido depois pela rota de edição

Isso evita distorção nos cards de atualização da unidade.

---

## Playground

Existe uma interface simples para teste manual do parser.

### Local

- `/api/playground`

### Produção

- `https://mnrs.com.br/giro/api/playground`

O playground permite:

- colar o texto do giro
- informar o horário oficial manualmente
- enviar para parsing
- inspecionar o JSON retornado

---

## Desenvolvimento local

### Requisitos

- Docker
- Docker Compose

### Subir ambiente local

```bash
docker compose up --build
```

Serviços esperados:

- API FastAPI
- PostgreSQL

---

## Deploy em produção

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Quando a alteração for só no parser/API:

```bash
docker compose -f docker-compose.prod.yml up -d --build parser-api
```

---

## Estrutura principal

```text
giro-de-leitos/
├── main.py                    # rotas FastAPI e montagem do resumo
├── parser_service.py          # parser das mensagens
├── db.py                      # persistência, consolidação e alertas
├── units.py                   # cadastro e resolução de aliases
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
└── scripts/
```

---

## Situação atual de uso

No estado atual, o serviço já pode ser usado em rotina operacional para:

- receber giros por Telegram
- alimentar a aba `UPAs`
- acompanhar atualização por unidade
- localizar vagas de vermelha e amarela por sexo
- enxergar isolamento adulto disponível
- manter histórico e alertas

O ponto mais importante é que a informação operacional já chega tratada para a tela, reduzindo necessidade de interpretação manual do texto bruto.

---

## Frontend web app

Há um frontend Next.js 14 em `frontend/` que substitui o playground HTML antigo para o fluxo operacional de UPAs.

### Rodar em desenvolvimento

1. Backend em `localhost:8000`:
   `uvicorn main:app --reload --port 8000`
2. Frontend:
   `cd frontend && pnpm install && pnpm dev`
3. Acesse `http://localhost:3000`. O Next proxya `/api/*` e `/ws/*` pro backend automaticamente.

### Onboarding inicial

1. Configure `.env` a partir de `.env.example`. Gere `CPF_ENCRYPTION_KEY` Fernet.
2. Rode `python scripts/seed_admin.py` — cria as UPAs e o usuário admin root.
3. Faça login admin em `/admin/login`, gere convite de coordenador, copie o link, envie por WhatsApp.

### Build de produção

`docker compose up --build` sobe Postgres, parser-api, whatsapp-bridge e frontend.

O playground HTML antigo (`/api/playground`) continua acessível como fallback.
