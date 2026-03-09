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

- `/resumo`
- `/status`
- `/giro`
- `/alertas`

### Variáveis de ambiente

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `PUBLIC_BASE_URL`
- `PUBLIC_WEBHOOK_PATH`

### URLs úteis em produção

- `https://mnrs.com.br/giro/api/telegram/status`
- `https://mnrs.com.br/giro/api/webhook/telegram`

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
