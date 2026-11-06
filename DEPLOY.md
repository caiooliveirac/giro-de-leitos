# Deploy — Giro de Leitos

Produção no servidor **magalu** (docker: `giro-de-leitos-api` + `giro-de-leitos-frontend`).
Desde 2026-11-06 o deploy roda **no próprio servidor**, sem SSH e sem runner no Mac
(a porta 22 do magalu fechou para a internet em 2026-10-10):
**testes no GitHub -> branch `live` -> auto-promote no servidor -> rsync -> build image ->
canary container -> healthcheck -> atomic swap -> rollback se falhar**.

A `whatsapp-bridge` (Baileys) **nunca** é tocada pelo deploy — ela mantém a sessão WhatsApp
viva e qualquer restart força novo QR code. O swap só substitui `giro-de-leitos-api` e
`giro-de-leitos-frontend`; postgres e bridge ficam intactas.

---

## 1. GitHub Secrets (one-time)

> O deploy não usa mais nenhum destes secrets (não há SSH nem notificação Telegram no
> `deploy.yml`). Os `PROD_SSH_*` só restam para `rollback.yml` e
> `configure-whatsapp-group.yml`, que dependem de SSH e não alcançam o magalu de uma VM
> do GitHub desde 2026-10-10.

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
# Em prod, postgres roda NATIVO no host (nao em container docker).
# A API roda em container e usa host.docker.internal:5432 para alcancar o host
# (precisa de `--add-host=host.docker.internal:host-gateway` no docker run, ja
# tratado pelo workflow). O pg_dump do step de backup roda no host e troca
# automaticamente host.docker.internal -> 127.0.0.1 antes de chamar pg_dump.
DATABASE_URL=postgresql://giro:SENHA_DO_HOST_POSTGRES@host.docker.internal:5432/giro_de_leitos

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
4. **Verificacoes one-time no host** antes do primeiro deploy:

   ```bash
   # Portas que o workflow usa (canary 8051/3052 + prod 8000/3050) devem estar livres.
   # 8001 esta ocupada (repo-web-1), 8050 esta ocupada (samu-onboarding); por isso
   # usamos 8051 e 3052 para o canary.
   ssh samu 'ss -tlnp | grep -E ":(8051|3050|3052) "'
   # ^ retorno vazio = OK.

   # pg_dump precisa estar instalado no host (postgres roda nativo, nao em docker):
   ssh samu 'which pg_dump'
   ```

6. **Push em `main`** ou rode manualmente:

   ```
   GitHub -> Actions -> "Deploy" -> Run workflow
   ```

7. Pós-primeiro deploy, seed dos coordenadores (CSV não vai pelo rsync):

   ```bash
   scp coordenadores.csv samu:/home/ubuntu/giro-de-leitos/
   ssh samu 'docker exec giro-de-leitos-api python scripts/seed_admin.py'
   ssh samu 'docker exec giro-de-leitos-api python scripts/seed_coordinators_csv.py /app/coordenadores.csv'
   ```

   (o `seed_admin.py` é idempotente; pode rodar sempre que mexer no `.deploy-env`.)

---

## 5. Deploy de rotina

- **Push em `main`** (de qualquer aparelho, inclusive merge pelo celular) basta.
- **Manual** (sem alterar código): `Actions -> Deploy -> Run workflow` revalida e libera a
  ponta da `main`. No servidor: `labctl promote giro-de-leitos [<sha>]` (sem ref =
  `origin/main`, sem o portão dos testes).

O caminho:

1. `deploy.yml` (VM do GitHub): `pytest` + `tsc --noEmit`.
2. Verde: o job `liberar` move a branch **`live`** para o SHA (fast-forward pela API).
3. No servidor, o `auto-promote.timer` (a cada minuto) segue a `live` e roda
   `labctl promote giro-de-leitos <sha>`; o `PROMOTE_CMD` de `~/labctl/apps/giro-de-leitos.conf`
   executa o `scripts/deploy-magalu.sh` **do commit alvo**:
   1. checkout limpo do SHA em `/home/ubuntu/deploy-src/giro-de-leitos` (o `LIVE_DIR`);
   2. rsync para `/home/ubuntu/giro-de-leitos` (preserva `.deploy-env` e o que é do servidor);
   3. backup `pg_dump` do postgres (mantém últimos 8, em `/home/ubuntu/giro-backups`);
   4. build `giro-api:<sha>` + `giro-frontend:<sha>`;
   5. canary em portas altas (`8051` / `3052`), healthcheck `/api/health`, `/api/summary` e `/`;
   6. **atomic swap** nas portas reais (`8000` / `3050`) — downtime ~1-3s; a imagem
      anterior vira `:previous`; se a nova não responder, volta sozinho para a anterior;
   7. apaga imagens antigas (fica a do ar e a `:previous`).
4. O job `aguardar` fica verde quando `https://giro.mnrs.com.br/api/health` responde
   `"commit": "<sha>"`.

Um SHA que falhou no servidor não é tentado de novo: corrige-se com commit novo.
Log: `ssh magalu journalctl -u auto-promote -n 80` e `labctl history giro-de-leitos`.

> O `.git` de `/home/ubuntu/giro-de-leitos` é antigo (o rsync sempre o excluiu) e **não**
> diz o que está no ar. O commit no ar é o do `/api/health` (e o HEAD de
> `/home/ubuntu/deploy-src/giro-de-leitos`).

---

## 6. Rollback

- **Revert na `main`** (padrão): o revert passa pelos testes e publica como qualquer push.
- **No servidor**: `labctl rollback giro-de-leitos` — redeploy do commit anterior registrado
  (`scripts/deploy-magalu.sh <sha-anterior>`; com o cache do docker é rápido).
- **Imediato**, só a API, para a imagem `:previous`:

```bash
ssh magalu
docker rm -f giro-de-leitos-api
docker run -d --name giro-de-leitos-api --restart unless-stopped \
  --network giro-de-leitos_default \
  --network-alias parser-api \
  --add-host=host.docker.internal:host-gateway \
  --env-file /home/ubuntu/giro-de-leitos/.deploy-env \
  -p 127.0.0.1:8000:8000 \
  giro-api:previous
docker network connect wa-net giro-de-leitos-api
```

`Actions -> Rollback` depende de SSH e não alcança o magalu desde 2026-10-10.

---

## 7. Sinais de fogo e ações

| Sintoma                                                | O que checar                                                                 | Ação                                  |
|--------------------------------------------------------|------------------------------------------------------------------------------|----------------------------------------|
| Deploy falha em "canary" (`journalctl -u auto-promote`) | saída do `scripts/deploy-magalu.sh` no journal                               | Corrigir bug; produção segue intacta   |
| API 502 após swap                                      | `docker logs giro-de-leitos-api --tail 100`                                  | Revert na `main` ou `labctl rollback giro-de-leitos` |
| Bridge logando "ECONNREFUSED parser-api:8000"          | Bridge perdeu a rede docker. Rede `giro-de-leitos_default` deletada por engano | `docker network create giro-de-leitos_default` + `docker network connect` em ambos |
| Frontend 502                                           | `docker logs giro-de-leitos-frontend`                                        | Revert na `main` ou `labctl rollback giro-de-leitos` |
| Postgres não responde                                  | postgres roda nativo no host: `sudo systemctl status postgresql`             | `sudo systemctl restart postgresql`; restaurar do último dump em `/home/ubuntu/giro-backups/` se necessário |
| QR code do WhatsApp pedindo de novo                    | Alguém parou a bridge                                                        | `docker compose up -d whatsapp-bridge` + ler logs para escanear QR |

---

## Apêndice — Padrão idêntico ao taximetro

Esse pipeline é cópia adaptada de `/home/ubuntu/taximetro-digital/.github/workflows/deploy.yml`.
Diferenças:

- Dois componentes (`api` + `frontend`) em vez de um.
- A `whatsapp-bridge` é zona proibida — sobrevive aos swaps porque a rede
  `giro-de-leitos_default` persiste (criada uma vez pelo `docker compose up`).
- Backup `pg_dump` extra (taximetro não tem banco próprio).
