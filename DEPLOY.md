# Deploy de Produção — Giro de Leitos

Playbook completo para o **primeiro deploy** e para os **deploys subsequentes**
do `giro-de-leitos` (parser-api + frontend) preservando intocada a
`whatsapp-bridge`.

> **Regra inviolável**: nenhum script aqui mexe na `whatsapp-bridge`. A bridge
> roda em prod por outro caminho (compose separado / PM2 / outra máquina). Se
> precisar reiniciá-la, use a forma que já existe — esses scripts não a
> conhecem.

---

## 0. Decisões de default deste pipeline

| Tema | Decisão | Como mudar |
|---|---|---|
| Domínio do frontend | `giro.mnrs.com.br` (subdomínio). `/` → Next, `/api/*` `/ws/*` → parser-api. | Editar `nginx/giro-app.conf` e DNS. |
| Alternativa | Servir em `/giro-app` no mesmo domínio. **Não recomendado**: dá problema com cookies first-party e namespacing dos paths. | — |
| Tag das imagens | SHA curto do commit: `giro-api:<sha>` / `giro-frontend:<sha>`. Build local no servidor (sem registry). | `deploy/deploy.sh` (procurar `docker build -t`). |
| Swap | `docker compose up -d --no-deps <svc>` — janela de indisponibilidade ~1–3s. | — |
| Backup | `pg_dump` automático em `/opt/giro/backups/` (mantém 7). | `deploy/pre-deploy.sh`. |
| Rollback | Imagem `PREVIOUS` permanece local. `./deploy/rollback.sh` faz swap reverso em <5s. | — |

**Sobre a janela de 1–3s no swap**: o Telegram retransmite webhooks em falha
(seguro). A **whatsapp-bridge** posta em `POST /api/ingest/whatsapp-bridge`
**sem retry** (verificado em `whatsapp-bridge/index.mjs` — só try/catch que
loga e segue). Portanto **uma ou duas mensagens de WhatsApp podem ser perdidas
durante o swap**. Mitigações:
1. Janela é curta (<3s na maioria das vezes).
2. Próxima mensagem da mesma UPA reestabelece o estado.
3. Se for crítico, faça o deploy fora de horário de pico de plantão.

---

## 1. Pré-requisitos (one-time setup no servidor)

### 1.1 Estrutura de diretórios

```bash
sudo mkdir -p /opt/giro/{logs,backups}
sudo chown -R $(id -u):$(id -g) /opt/giro
cd /opt/giro
git clone https://github.com/<owner>/giro-de-leitos.git
cd giro-de-leitos
git checkout main
```

### 1.2 Arquivo de ambiente `/opt/giro/.deploy-env`

```bash
cat > /opt/giro/.deploy-env <<'EOF'
# --- DB ---
POSTGRES_DB=giro_de_leitos
POSTGRES_USER=giro
POSTGRES_PASSWORD=__troque_essa_senha__

# --- Telegram / WhatsApp ---
TELEGRAM_BOT_TOKEN=__token__
TELEGRAM_ADMIN_CHAT_ID=1438288563
TELEGRAM_WEBHOOK_SECRET=__random_64_hex__
PUBLIC_BASE_URL=https://mnrs.com.br
PUBLIC_WEBHOOK_PATH=/giro/api/webhook/telegram

# A bridge continua falando com o parser pelo nome do container,
# pela rede `perguntas_default`. NÃO altere a menos que saiba o que faz.
WHATSAPP_BRIDGE_URL=http://whatsapp-bridge:3000

# --- Auth / Crypto (novos) ---
# Gere com:
#   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=__cole_aqui__
# Gere com:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CPF_ENCRYPTION_KEY=__cole_aqui__
CPF_HASH_PEPPER=__opcional_string_aleatoria__

# --- Admin inicial (seed_admin.py, idempotente) ---
ADMIN_INITIAL_EMAIL=admin@exemplo.com
ADMIN_INITIAL_PASSWORD=__troque__
ADMIN_INITIAL_NAME=Admin Regulador
ADMIN_INITIAL_CPF=00000000000
ADMIN_INITIAL_PIN=0000

# --- SHAs (preenchidos automaticamente pelos scripts) ---
# API_SHA=
# FRONTEND_SHA=
# PREVIOUS_API_SHA=
# PREVIOUS_FRONTEND_SHA=
EOF
chmod 600 /opt/giro/.deploy-env
```

Comandos para gerar as chaves (rodar e colar a saída):

```bash
# JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# CPF_ENCRYPTION_KEY (Fernet 32-byte url-safe)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 1.3 DNS

Aponte `giro.mnrs.com.br` (A/AAAA) para o servidor do mnrs.

### 1.4 Bloco nginx

O snippet pronto está em `nginx/giro-app.conf`. **Linka** no nginx host:

```bash
sudo cp /opt/giro/giro-de-leitos/nginx/giro-app.conf /etc/nginx/conf.d/giro-app.conf
sudo nginx -t        # valida sintaxe
sudo systemctl reload nginx
```

> Se o seu nginx vive **fora do docker** (no host), troque os upstreams em
> `giro-app.conf` para `127.0.0.1:3000` e `127.0.0.1:8000`, e adicione
> `ports:` aos serviços no `docker-compose.prod.yml`. Se o nginx vive
> **dentro** do compose `perguntas_default`, a config como está funciona.

### 1.5 Certificado TLS

```bash
sudo certbot --nginx -d giro.mnrs.com.br --redirect --agree-tos -m admin@mnrs.com.br
```

(Ou use webroot/DNS — depende do que o restante do stack `mnrs.com.br` usa.)

### 1.6 Primeiro `up` + seed do admin

Primeiro deploy precisa criar o admin inicial **uma única vez**:

```bash
cd /opt/giro/giro-de-leitos
./deploy/deploy.sh                # builda e sobe tudo
# Após containers estarem saudáveis:
docker exec -it giro-de-leitos-api python scripts/seed_admin.py
```

`seed_admin.py` lê `ADMIN_INITIAL_*` do ambiente. **É idempotente** — pode
rodar de novo sem efeitos colaterais.

---

## 2. Deploy normal (rotina)

```bash
cd /opt/giro/giro-de-leitos
./deploy/deploy.sh
```

O que acontece (resumido — detalhes nos comentários de `deploy/deploy.sh`):

1. Carrega `/opt/giro/.deploy-env`.
2. `git fetch && git pull --ff-only origin main`.
3. Captura SHA dos containers em execução → `PREVIOUS_API_SHA` / `PREVIOUS_FRONTEND_SHA`.
4. Calcula novo SHA = `git rev-parse --short HEAD`.
5. `docker build -t giro-api:$SHA .` e `docker build -t giro-frontend:$SHA frontend/`.
6. `deploy/pre-deploy.sh`: `pg_dump` para `/opt/giro/backups/db-<TS>.sql.gz`, mantém 7 últimos.
7. Persiste novos SHAs em `/opt/giro/.deploy-env`.
8. **Swap atômico**: `docker compose -f docker-compose.prod.yml up -d --no-deps parser-api frontend`.
9. `deploy/healthcheck.sh` (até 30s): `/api/health`, `/api/summary`, atividade recente em DB, HTTP HTML no frontend.
10. Se healthcheck falha → `deploy/rollback.sh` automático + exit 1.

Variantes:

```bash
./deploy/deploy.sh --component api        # só backend
./deploy/deploy.sh --component frontend   # só frontend
./deploy/deploy.sh --skip-backup          # PERIGOSO (não use exceto debug)
./deploy/deploy.sh --skip-healthcheck     # PERIGOSO
```

---

## 3. Rollback

Tempo esperado: **<5s** (apenas re-tag + `compose up`, imagem anterior já no host).

```bash
./deploy/rollback.sh api          # só parser-api
./deploy/rollback.sh frontend     # só frontend
./deploy/rollback.sh both         # ambos
```

Se a imagem `PREVIOUS_*` foi removida (purge docker), o script aborta com
mensagem clara — você terá que rebuilar a partir do commit antigo:

```bash
git checkout <previous-sha>
docker build -t giro-api:<previous-sha> .
git checkout main
./deploy/rollback.sh api
```

---

## 4. Status / inspeção rápida

```bash
./deploy/status.sh
```

Mostra SHAs persistidos, SHAs em execução, containers, últimos 3 deploys e
backups recentes.

---

## 5. Verificações pós-deploy

```bash
# Legado (continua válido — não mudamos a rota)
curl -fsS https://mnrs.com.br/giro/api/health
curl -fsS https://mnrs.com.br/giro/api/summary | jq '.units | length'

# Novo subdomínio
curl -fsS https://giro.mnrs.com.br/api/health
curl -fsSI https://giro.mnrs.com.br/ | head -1   # esperar 200

# Bridge ainda postando? (rode no compose dela)
docker logs whatsapp-bridge --tail 50
# Esperar ver `POST /api/ingest/whatsapp-bridge` (ou similar) recente.
```

---

## 6. Sinais de fogo (sintoma → ação)

| Sintoma | Diagnóstico rápido | Ação |
|---|---|---|
| Telegram parou de entregar | `curl https://mnrs.com.br/giro/api/health` falha? `sudo nginx -t`; cert expirou? | `./deploy/rollback.sh api` se mudança recente; senão investigar nginx/cert. |
| WhatsApp parou de aparecer no painel | `docker logs whatsapp-bridge --tail 100` — vê erros de conexão pro parser? | Verificar que `WHATSAPP_BRIDGE_URL` aponta para `parser-api:8000` (no compose dela) e que ambos estão na rede `perguntas_default`. Rollback do parser se quebra confirmada. |
| Frontend 502 em `giro.mnrs.com.br` | `docker logs giro-de-leitos-frontend --tail 50`; container caiu? | `./deploy/rollback.sh frontend` — mantém api nova rodando. |
| Health check passa mas UI vazia | Provavelmente CORS / cookies. Conferir se `giro.mnrs.com.br/api/*` retorna 200 do mesmo origin. | Ajustar nginx; sem rollback necessário (não regressão funcional). |
| Postgres não sobe após restart | `docker logs giro-de-leitos-db` | Restore: ver seção 7. **Nunca** dê `docker compose down -v`. |

---

## 7. Restore de banco (último recurso)

```bash
# Identificar backup
ls -lht /opt/giro/backups/

# Restore para o container vivo
gunzip -c /opt/giro/backups/db-<TS>.sql.gz \
  | docker exec -i giro-de-leitos-db psql -U giro -d giro_de_leitos
```

> Restore destrutivo (drop + recreate) só com a equipe alinhada — perde dados
> entre o backup e agora.

---

## 8. Plano de incidente expresso

**Comando 1-line de rollback total:**

```bash
cd /opt/giro/giro-de-leitos && ./deploy/rollback.sh both
```

**Quem avisar:** admin via Telegram chat configurado em `TELEGRAM_ADMIN_CHAT_ID`
(`1438288563`). O parser-api já manda alertas automáticos de anomalia para esse
chat.

---

## 9. O que **nunca** fazer

- `docker compose -f docker-compose.prod.yml down` — derruba tudo, inclusive postgres. Use sempre `up -d --no-deps <svc>`.
- `docker compose ... --remove-orphans` — pode tentar remover a `whatsapp-bridge` se ela aparecer (não deve, mas evite).
- `docker volume rm giro_db_data` — destrói o DB.
- Editar `docker-compose.yml` (esse é o dev). Prod é `docker-compose.prod.yml`.
- Mexer em `parser_service.py`, `units.py`, schema legado de `db.py`, ou rotas legadas (`/api/summary`, `/api/last-event`, `/api/webhook/telegram`, `/api/ingest/*`, `/ws/dashboard`).
