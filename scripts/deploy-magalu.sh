#!/usr/bin/env bash
# Deploy do giro-de-leitos no servidor magalu (docker: api + frontend).
#
# Roda NO SERVIDOR, chamado por `labctl promote giro-de-leitos <sha>` (o
# auto-promote.timer segue a branch `live`, que o deploy.yml do GitHub move
# depois dos testes verdes — ver DEPLOY.md). Sem SSH e sem runner no Mac.
#
# É o que o antigo job de deploy fazia por SSH, agora local:
#   checkout limpo do SHA em /home/ubuntu/deploy-src/giro-de-leitos →
#   rsync para /home/ubuntu/giro-de-leitos (mesmos excludes: .deploy-env e o
#   que é do servidor ficam) → backup pg_dump → build giro-api:<sha> e
#   giro-frontend:<sha> → canary (8051 / 3052) → swap (8000 / 3050) → health.
#
# Os dois builds e os dois canaries vêm antes do primeiro swap: falhou ali, a
# produção nem foi tocada. Falhou depois do swap, volta sozinho para a imagem
# que estava no ar (que também fica tagueada como :previous).
#
# Uso: bash deploy-magalu.sh <sha>
# Rollback: revert na main, ou `labctl rollback giro-de-leitos` (redeploy do
# commit anterior; com o cache do docker, rápido).
set -euo pipefail

SHA_ARG="${1:?uso: deploy-magalu.sh <sha>}"
SRC_DIR=/home/ubuntu/deploy-src/giro-de-leitos
DEPLOY_DIR=/home/ubuntu/giro-de-leitos
ENV_FILE="$DEPLOY_DIR/.deploy-env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERRO: $ENV_FILE ausente." >&2
  exit 1
fi

cd "$SRC_DIR"
git fetch -q origin
SHA=$(git rev-parse --verify "${SHA_ARG}^{commit}")
PREV=$(git rev-parse HEAD)
TAG="${SHA:0:7}"
IMG_API="giro-api:$TAG"
IMG_FRONT="giro-frontend:$TAG"
API_PREV=$(docker inspect giro-de-leitos-api --format '{{.Config.Image}}' 2>/dev/null || echo "")
FRONT_PREV=$(docker inspect giro-de-leitos-frontend --format '{{.Config.Image}}' 2>/dev/null || echo "")

# Mesmos excludes do antigo rsync do GitHub: o que é do servidor (.deploy-env,
# credenciais, CSV de coordenadores, dependências, o .git antigo) não é tocado.
# go-w: permissões como as do checkout do runner (umask 022).
sincroniza() {
  rsync -a --delete --chmod=go-w \
    --exclude '.git' \
    --exclude 'node_modules' \
    --exclude '.next' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.env.local' \
    --exclude '.pytest_cache' \
    --exclude 'credentials*.json' \
    --exclude 'coordenadores.csv' \
    --exclude '.deploy-env' \
    --exclude '*.zip' \
    "$SRC_DIR/" "$DEPLOY_DIR/"
}

espera() { # <url> <tentativas>
  local i
  for i in $(seq 1 "$2"); do
    if curl -fsS "$1" > /dev/null 2>&1; then
      echo "OK em ${i}s ($1)"
      return 0
    fi
    sleep 1
  done
  return 1
}

# Postgres do giro: container giro-de-leitos-db-local na rede giro-de-leitos_default.
# --add-host: DATABASE_URL antigo com host.docker.internal => host-gateway.
sobe_api() { # <nome> <imagem> <porta-host> <commit> [opções extras do docker run]
  local nome="$1" img="$2" porta="$3" commit="$4"
  shift 4
  docker rm -f "$nome" 2>/dev/null || true
  docker run -d --name "$nome" "$@" \
    --network giro-de-leitos_default \
    --add-host=host.docker.internal:host-gateway \
    --env-file "$ENV_FILE" \
    -e GIT_COMMIT_SHA="$commit" \
    -p "127.0.0.1:$porta:8000" \
    "$img" > /dev/null
}

sobe_api_producao() { # <imagem> <commit>
  # --network-alias parser-api: quem fala com http://parser-api:8000 pela rede docker.
  sobe_api giro-de-leitos-api "$1" 8000 "$2" --restart unless-stopped --network-alias parser-api
  # wa-net: rede compartilhada com o gateway do WhatsApp (whatsmeow-gw), que
  # publica só em 127.0.0.1 do host e por isso NÃO é alcançável por
  # host.docker.internal. Sem esta rede o alerta de UPA sem giro falha em
  # silêncio: o watcher loga timeout e a varredura seguinte tenta de novo,
  # para sempre, sem ninguém ser avisado. Idempotente de propósito.
  docker network create wa-net 2>/dev/null || true
  docker network connect wa-net giro-de-leitos-api 2>/dev/null || true
  docker network connect wa-net whatsmeow-gw 2>/dev/null || true
}

sobe_front() { # <nome> <imagem> <porta-host> [opções extras do docker run]
  local nome="$1" img="$2" porta="$3"
  shift 3
  docker rm -f "$nome" 2>/dev/null || true
  docker run -d --name "$nome" "$@" -p "127.0.0.1:$porta:3000" "$img" > /dev/null
}

API_TROCADA=0
FRONT_TROCADA=0
FONTE_TROCADA=0
reverte() {
  set +e
  echo "=== FALHOU: revertendo para ${PREV:0:7} ===" >&2
  docker logs --tail 80 giro-api-canary 2>/dev/null
  docker rm -f giro-api-canary giro-frontend-canary > /dev/null 2>&1
  if [ "$FRONT_TROCADA" = 1 ] && [ -n "$FRONT_PREV" ]; then
    echo "=== [frontend] voltando para $FRONT_PREV ===" >&2
    sobe_front giro-de-leitos-frontend "$FRONT_PREV" 3050 --restart unless-stopped
    espera http://127.0.0.1:3050/ 30 || echo "ERRO: frontend anterior não voltou — intervenção manual" >&2
  fi
  if [ "$API_TROCADA" = 1 ] && [ -n "$API_PREV" ]; then
    echo "=== [api] voltando para $API_PREV ===" >&2
    sobe_api_producao "$API_PREV" "$PREV"
    espera http://127.0.0.1:8000/api/health 30 || echo "ERRO: api anterior não voltou — intervenção manual" >&2
  fi
  # Fonte (checkout e diretório do deploy) de volta ao que está no ar.
  if [ "$FONTE_TROCADA" = 1 ]; then
    git -C "$SRC_DIR" checkout -q --detach "$PREV" && sincroniza
  fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then reverte; fi; exit "$rc"' EXIT

echo "=== git: ${PREV:0:7} -> $TAG ==="
git checkout -q --detach "$SHA"
FONTE_TROCADA=1
echo "=== rsync $SRC_DIR -> $DEPLOY_DIR ==="
sincroniza

echo "=== backup do banco ==="
mkdir -p /home/ubuntu/giro-backups
ts=$(date +%Y%m%dT%H%M%S)
# Extrai só o DATABASE_URL sem dar `source` no arquivo: .deploy-env é formato
# docker --env-file (KEY=VALUE literal, valores podem ter espaços p.ex.
# SMTP_PASS/SMTP_FROM) e NÃO é um script shell válido.
DUMP_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's|host.docker.internal|127.0.0.1|g' || true)
if [ -z "$DUMP_URL" ]; then
  echo "WARN: DATABASE_URL ausente no .deploy-env, pulando backup"
elif echo "$DUMP_URL" | grep -q "@postgres:"; then
  # magalu: o Postgres roda em container (giro-de-leitos-db-local) com hostname
  # interno "postgres" e porta não publicada no host: o dump roda DENTRO dele.
  IN_URL=$(echo "$DUMP_URL" | sed "s|@postgres:|@127.0.0.1:|")
  docker exec giro-de-leitos-db-local pg_dump "$IN_URL" | gzip > "/home/ubuntu/giro-backups/db-$ts.sql.gz"
else
  pg_dump "$DUMP_URL" | gzip > "/home/ubuntu/giro-backups/db-$ts.sql.gz"
fi
if [ -n "$DUMP_URL" ]; then
  ls -t /home/ubuntu/giro-backups/db-*.sql.gz | tail -n +8 | xargs -r rm
  echo "backup: db-$ts.sql.gz ($(du -h "/home/ubuntu/giro-backups/db-$ts.sql.gz" | cut -f1))"
fi

cd "$DEPLOY_DIR"
echo "=== [api] build $IMG_API ==="
docker build --pull -t "$IMG_API" -t giro-api:candidate .
echo "=== [frontend] build $IMG_FRONT ==="
docker build --pull -t "$IMG_FRONT" -t giro-frontend:candidate -f frontend/Dockerfile frontend/

echo "=== [frontend] canary em 127.0.0.1:3052 ==="
sobe_front giro-frontend-canary "$IMG_FRONT" 3052
espera http://127.0.0.1:3052/ 40 || { echo "ERRO: canary frontend não saudável" >&2; docker logs --tail 80 giro-frontend-canary || true; exit 1; }

echo "=== [api] canary em 127.0.0.1:8051 ==="
# Canary não precisa do alias parser-api. Vive só até o swap: roda com o
# .deploy-env de produção.
sobe_api giro-api-canary "$IMG_API" 8051 "$SHA"
espera http://127.0.0.1:8051/api/health 30 || { echo "ERRO: canary api não passou no healthcheck" >&2; exit 1; }
curl -fsS http://127.0.0.1:8051/api/summary > /dev/null || { echo "ERRO: /api/summary falhou no canary" >&2; exit 1; }

for par in "giro-api:$API_PREV" "giro-frontend:$FRONT_PREV"; do
  repo="${par%%:*}" prev="${par#*:}"
  if [ -n "$prev" ] && [ "$prev" != "$repo:$TAG" ]; then
    docker tag "$prev" "$repo:previous" || true
  fi
done

echo "=== [api] swap em 127.0.0.1:8000 ==="
API_TROCADA=1
sobe_api_producao "$IMG_API" "$SHA"
espera http://127.0.0.1:8000/api/health 30 || { echo "ERRO: api nova não saudável em 8000" >&2; docker logs --tail 80 giro-de-leitos-api || true; exit 1; }
docker rm -f giro-api-canary 2>/dev/null || true

echo "=== [frontend] swap em 127.0.0.1:3050 ==="
FRONT_TROCADA=1
sobe_front giro-de-leitos-frontend "$IMG_FRONT" 3050 --restart unless-stopped
espera http://127.0.0.1:3050/ 30 || { echo "ERRO: frontend novo não saudável em 3050" >&2; docker logs --tail 80 giro-de-leitos-frontend || true; exit 1; }
docker rm -f giro-frontend-canary 2>/dev/null || true

# Disco do magalu é curto: de cada imagem fica só a do ar e a :previous.
for repo in giro-api giro-frontend; do
  manter=$(docker image inspect -f '{{.Id}}' "$repo:$TAG" "$repo:previous" 2>/dev/null || true)
  docker images "$repo" --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}' | while read -r ref id; do
    grep -qxF "$id" <<<"$manter" || docker rmi "$ref" > /dev/null 2>&1 || true
  done
done

echo "=== OK: giro-de-leitos $TAG no ar (anterior: ${PREV:0:7}, imagens :previous) ==="
