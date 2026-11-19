#!/usr/bin/env bash
# Recria o whatsmeow-gw (gateway WhatsApp) com a config completa.
#
# POR QUE ESTE SCRIPT EXISTE: em 2026-08-22 o container foi recriado sem
# --add-host host.docker.internal:host-gateway. Sem isso os webhooks para o
# giro-wa-adapter (3081) e transportes-ingest (3082) morrem em DNS e o giro de
# leitos para de atualizar, silenciosamente. O gateway host.docker.internal
# TEM de ser o do docker0 (172.17.0.1) — o ufw só libera 3081/3082 nessa
# interface. Por isso a rede "bridge" também é conectada.
#
# A sessão do WhatsApp vive no volume whatsmeow-data: recriar não pede QR.
set -euo pipefail
cd "$(dirname "$0")"

docker rm -f whatsmeow-gw 2>/dev/null || true
docker run -d \
  --name whatsmeow-gw \
  --restart unless-stopped \
  --network wa-net \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:3080:3000 \
  -v whatsmeow-data:/app/storages \
  --env-file ./env.list \
  aldinokemal2104/go-whatsapp-web-multidevice:latest rest

docker network connect bridge whatsmeow-gw
docker network connect escala_default whatsmeow-gw

sleep 5
docker exec whatsmeow-gw getent hosts host.docker.internal
echo "OK — confira: docker logs --tail 20 whatsmeow-gw"
