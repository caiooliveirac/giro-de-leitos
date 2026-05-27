# Deploy — Giro de Leitos

Deploy automatizado via GitHub Actions no padrão da box `samu` (EC2 `56.125.255.58`).
Mesmo padrão dos outros apps que vivem nessa máquina (taximetro-digital, plantoes etc):
**rsync source -> build image -> canary container -> healthcheck -> atomic swap -> rollback se falhar**.

A `whatsapp-bridge` (Baileys) **nunca** é tocada pelo deploy — ela mantém a sessão WhatsApp
viva e qualquer restart força novo QR code. O swap só substitui `giro-de-leitos-api` e
`giro-de-leitos-frontend`; postgres e bridge ficam intactas.

---

## 1. GitHub Secrets (one-time)

No repositório (`Settings -> Secrets and variables -> Actions`):

| Secret                     | Valor                                                                 |
|----------------------------|-----------------------------------------------------------------------|
| `PROD_SSH_USER`            | `ubuntu`                                                              |
| `PROD_SSH_HOST`            | `56.125.255.58`                                                       |
| `PROD_SSH_KEY`             | conteúdo de `~/.ssh/caio_ec2_2026` (chave **privada**, incluindo cabeçalho `-----BEGIN ...-----`) |
| `TELEGRAM_BOT_TOKEN`       | (opcional) token do bot — notificação de deploy ok/falha              |
| `TELEGRAM_ADMIN_CHAT_ID`   | (opcional) chat id do admin — geralmente `1438288563`                 |

> A chave privada **não** entra no repositório. Cole o conteúdo direto no campo Secret.

---

## 2. `.deploy-env` no servidor (one-time)

Arquivo de envs lido por `docker run --env-file` durante o canary swap.
Vive em `/home/ubuntu/giro-de-leitos/.deploy-env` no servidor e **não** está versionado.

Crie via SSH:

```bash
ssh samu
sudo -u ubuntu vim /home/ubuntu/giro-de-leitos/.deploy-env
chmod 600 /home/ubuntu/giro-de-leitos/.deploy-env
```

Template:

```env
# --- Banco ---
DATABASE_URL=postgresql://giro:giro_seguro_2026@giro-de-leitos-db-local:5432/giro_de_leitos

# --- Auth / Crypto (gere com `openssl rand -hex 32` / `openssl rand -base64 32`) ---
JWT_SECRET=__GERAR__
CPF_ENCRYPTION_KEY=__GERAR_32_BYTES_BASE64__
CPF_HASH_PEPPER=__GERAR__

# --- Seed admin inicial (idempotente, opcional após o primeiro run) ---
ADMIN_INITIAL_EMAIL=admin@mnrs.com.br
ADMIN_INITIAL_PASSWORD=__SENHA_FORTE__
ADMIN_INITIAL_NAME=Admin
ADMIN_INITIAL_CPF=00000000000
ADMIN_INITIAL_PIN=0000

# --- Telegram ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_ID=1438288563
TELEGRAM_WEBHOOK_SECRET=

# --- Integrações ---
WHATSAPP_BRIDGE_URL=http://giro-whatsapp-bridge:3000
PUBLIC_BASE_URL=https://giro.mnrs.com.br
PUBLIC_WEBHOOK_PATH=/api/webhook/telegram
```

---

## 3. nginx (one-time)

```bash
ssh samu

# 3.1 Adicionar upstream do frontend (so se nao existir ainda)
sudo vim /etc/nginx/conf.d/mnrs-host-upstreams.conf
# acrescente:
#   upstream canary_giro_frontend {
#       server 127.0.0.1:3050;
#       keepalive 16;
#   }
# (o upstream `canary_giro` para 127.0.0.1:8000 ja existe.)

# 3.2 Server block do subdominio
sudo cp /home/ubuntu/giro-de-leitos/nginx/giro.mnrs.com.br.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

DNS: `giro.mnrs.com.br` deve apontar para o EC2. Em geral o wildcard da Cloudflare já cobre.

---

## 4. Primeiro deploy

1. Cadastre os Secrets (passo 1).
2. Crie o `.deploy-env` (passo 2).
3. Configure nginx (passo 3).
4. **Push em `main`** ou rode manualmente:

   ```
   GitHub -> Actions -> "Deploy" -> Run workflow -> component=both
   ```

5. Pós-primeiro deploy, seed dos coordenadores (CSV não vai pelo rsync):

   ```bash
   scp coordenadores.csv samu:/home/ubuntu/giro-de-leitos/
   ssh samu 'docker exec giro-de-leitos-api python scripts/seed_admin.py'
   ssh samu 'docker exec giro-de-leitos-api python scripts/seed_coordinators_csv.py /app/coordenadores.csv'
   ```

   (o `seed_admin.py` é idempotente; pode rodar sempre que mexer no `.deploy-env`.)

---

## 5. Deploy de rotina

- **Push em `main`** dispara `test -> deploy` automaticamente.
- **Manual** (sem alterar código, ex: rebuild com `.deploy-env` novo):
  `Actions -> Deploy -> Run workflow -> component=api|frontend|both`.

O workflow:

1. Roda `pytest` + `tsc --noEmit`.
2. rsync do source.
3. Backup `pg_dump` do postgres (mantém últimos 8).
4. Build `giro-api:<sha>` + `giro-frontend:<sha>`.
5. Sobe canary em portas altas (`8001` / `3051`), healthcheck `/api/health` e `/`.
6. **Atomic swap** nas portas reais (`8000` / `3050`) — downtime ~1-3s.
7. Tagueia versão anterior como `giro-api:previous` / `giro-frontend:previous`.
8. Notifica Telegram (se secrets presentes).

---

## 6. Rollback

`Actions -> Rollback -> Run workflow -> component=both -> confirm=ROLLBACK`.

Reverte para a imagem `:previous` (a que rodava antes do último deploy bem-sucedido).
Healthcheck pós-rollback obrigatório.

> Só existe `:previous` se já houve pelo menos **dois** deploys bem-sucedidos.
> O primeiro deploy não cria `:previous` — não há para onde reverter.

Rollback manual (mais raro):

```bash
ssh samu
docker rm -f giro-de-leitos-api
docker run -d --name giro-de-leitos-api --restart unless-stopped \
  --network giro-de-leitos_default \
  --env-file /home/ubuntu/giro-de-leitos/.deploy-env \
  -p 127.0.0.1:8000:8000 \
  giro-api:previous
```

---

## 7. Sinais de fogo e ações

| Sintoma                                                | O que checar                                                                 | Ação                                  |
|--------------------------------------------------------|------------------------------------------------------------------------------|----------------------------------------|
| Workflow falha em "healthcheck canary"                 | `docker logs giro-api-canary` no servidor                                    | Corrigir bug; produção segue intacta   |
| API 502 após swap                                      | `docker logs giro-de-leitos-api --tail 100`                                  | `Actions -> Rollback`                  |
| Bridge logando "ECONNREFUSED parser-api:8000"          | Bridge perdeu a rede docker. Rede `giro-de-leitos_default` deletada por engano | `docker network create giro-de-leitos_default` + `docker network connect` em ambos |
| Frontend 502                                           | `docker logs giro-de-leitos-frontend`                                        | `Actions -> Rollback -> frontend`      |
| Postgres não responde                                  | `docker ps` — container `giro-de-leitos-db-local` parado?                    | Restaurar do último dump em `/home/ubuntu/giro-backups/` |
| QR code do WhatsApp pedindo de novo                    | Alguém parou a bridge                                                        | `docker compose up -d whatsapp-bridge` + ler logs para escanear QR |

---

## Apêndice — Padrão idêntico ao taximetro

Esse pipeline é cópia adaptada de `/home/ubuntu/taximetro-digital/.github/workflows/deploy.yml`.
Diferenças:

- Dois componentes (`api` + `frontend`) em vez de um.
- A `whatsapp-bridge` é zona proibida — sobrevive aos swaps porque a rede
  `giro-de-leitos_default` persiste (criada uma vez pelo `docker compose up`).
- Backup `pg_dump` extra (taximetro não tem banco próprio).
