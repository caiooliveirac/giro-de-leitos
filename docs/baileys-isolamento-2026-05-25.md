# Investigação Baileys / WhatsApp — Giro de Leitos × Transportes SAMU

**Data:** 2026-05-25
**Autor:** levantamento read-only (Claude Code)
**Status:** diagnóstico fechado, plano proposto, **nada foi executado em prod**.

> **TL;DR** — A queda do Giro foi causada por **dois bugs combinados**:
> (1) o sidecar `whatsapp-bridge` do Giro e o `transportes-ingest` estão pareados no **mesmo número de WhatsApp** ("Chefe De Plantão", `557197150415`), em slots de Linked Device distintos. Quando o ingest foi re-pareado ontem ~17h BRT durante deploy, o WhatsApp revogou o slot do Giro (`reason=401`, `loggedOut`).
> (2) o bridge do Giro tenta `fs.rmSync('./auth_info')` ao receber `loggedOut`, mas esse caminho é um **bind de Docker volume** → `EBUSY` → crash síncrono → container reinicia → 401 de novo → **loop infinito** desde ontem 20:00 UTC (17:00 BRT). O `startBridgeWithPairingCode()` nunca é alcançado.
>
> Auth state em disco **NÃO** é compartilhado (paths/inodes distintos). O acoplamento é só a nível de identidade WhatsApp — mesmo número, mesmo perfil, mesmo `browser`. Postgres, nginx, PM2 ecosystem, GH Actions runners, cron e env files estão isolados.

---

## Seção A — Achados

### Tabela comparativa das duas apps com Baileys

| Item | giro-de-leitos / whatsapp-bridge | transportes-samu / ingest |
|---|---|---|
| Tipo | Sidecar Node em Docker (compose service `whatsapp-bridge`) | Worker Node em PM2 (`transportes-ingest`) |
| Caminho | [whatsapp-bridge/index.mjs](../whatsapp-bridge/index.mjs) | /home/ubuntu/transportes-samu/apps/ingest/ |
| Baileys | `@whiskeysockets/baileys ^6.7.16` ([package.json](../whatsapp-bridge/package.json)) | `@whiskeysockets/baileys ^6.7.16` |
| Versão Baileys runtime | `2.3000.1035194821` (log) | `2.3000.1035194821` (log) |
| Auth state | `useMultiFileAuthState` em `./auth_info` (cwd = `/app`) | `useMultiFileAuthState` em `WA_SESSION_DIR` (default `apps/ingest/auth`) |
| **Path absoluto da auth** | `/var/lib/docker/volumes/giro-de-leitos_wa_auth_info/_data/` (Docker named volume) | `/home/ubuntu/transportes-samu/apps/ingest/auth/` (host filesystem) |
| Mesmo inode/path? | **Não.** Mountpoint diferente, devices diferentes. | Idem. |
| `me.id` no creds.json | `557197150415:3@s.whatsapp.net` | `557197150415:7@s.whatsapp.net` |
| `me.name` | `Chefe De Plantão` | `Chefe De Plantão` |
| Device-id (slot WA) | `:3` | `:7` |
| `browser` no makeWASocket | `Browsers.macOS("Safari")` (linha 706) | `Browsers.macOS("Safari")` ([client.ts:43](/home/ubuntu/transportes-samu/apps/ingest/src/whatsapp/client.ts#L43)) |
| Processo runtime | container `giro-whatsapp-bridge` (Docker) | PM2 process id 7 (`transportes-ingest`) |
| cwd | `/app` (no container) | `/home/ubuntu/transportes-samu` |
| Banco PG | n/a (sidecar não usa PG; só HTTP → parser-api) | `transportes_samu` no PG nativo, role `transportes_samu` |
| Porta exposta | nenhuma (só sai pra `parser-api:8000`) | nenhuma (worker; web é processo separado em 3008) |
| Status atual | **`Restarting (1)` — crash loop ativo** | **`online`, mas 4 restarts em 17h** por `DATABASE_URL is required` |

### Apps SEM Baileys (varridas)

| App | Tecnologia | Sinal de WhatsApp? |
|---|---|---|
| `plantoes` (Next.js, PM2) | Next 16 + Drizzle + next-auth | Telegram webhook only — sem WA |
| `plantoes-telegram-worker` (PM2) | tsx worker | Telegram only |
| `nep-samu` (Next.js standalone, PM2) | Next.js | sem WA |
| `samu-bot` em `/var/www/samu-ai` (PM2) | Express + `node-telegram-bot-api` (polling) | Telegram only; consulta `https://mnrs.com.br/tabela/api/cases` — **não monitora o Giro nem o bridge** |

Conclusão: **apenas Giro e Transportes-ingest usam Baileys** no servidor. O grep canônico:

```
$ grep -rln "@whiskeysockets/baileys" /home/ubuntu --include=package.json
/home/ubuntu/giro-de-leitos/whatsapp-bridge/package.json
/home/ubuntu/transportes-samu/apps/ingest/package.json
```

---

## Seção B — Causa raiz confirmada

### (a) As duas apps compartilham o mesmo `auth_info` (mesmo diretório/key)?

**NÃO.** Paths e devices/inodes distintos:

- Giro: `/var/lib/docker/volumes/giro-de-leitos_wa_auth_info/_data/creds.json` (root, dentro do storage do Docker)
- Transportes: `/home/ubuntu/transportes-samu/apps/ingest/auth/creds.json` (ubuntu, filesystem normal)

Nenhum symlink entre eles, nenhuma bind mount cruzada no `docker-compose.yml`. Tamanhos e timestamps são diferentes.

### (b) As duas apps usam o **mesmo número de WhatsApp**?

**SIM. CONFIRMADO COM EVIDÊNCIA DIRETA DOS DOIS creds.json:**

- Giro `creds.json` → `me.id = 557197150415:3@s.whatsapp.net`, `me.name = "Chefe De Plantão"`
- Transportes-ingest `creds.json` → `me.id = 557197150415:7@s.whatsapp.net`, `me.name = "Chefe De Plantão"`

O `WHATSAPP_PHONE_NUMBER=5571997150415` no env do bridge confirma o número (formato BR com o "9" inicial). A diferença `:3` vs `:7` é o **device-id que o WhatsApp Multi-Device atribui a cada Linked Device**.

WhatsApp permite múltiplos devices conectados ao mesmo número, mas **não é estável para uso "headless" simultâneo via Baileys**: cada operação de "Conectar novo dispositivo" pode invalidar slots antigos, sobretudo quando o cliente "primário" (celular) inicia uma sessão de pareamento. Foi isso que aconteceu.

### (c) `browser` distinto?

**NÃO.** Ambos usam `Browsers.macOS("Safari")` — string literal idêntica. O `WAUserAgent` enviado ao servidor WhatsApp é, portanto, indistinguível entre os dois. Mesmo que o WhatsApp use device-id para isolar slots, manter `browser` igual atrapalha a leitura humana no painel de "Aparelhos conectados" do celular (aparecem como duas linhas idênticas, fácil de o operador deslogar a errada).

### (d) Logs do horário ~17h BRT ontem com `conflict`/`device_removed`/`401`/`515`/`loggedOut`?

**SIM.** Evidências:

1. **Volume de auth do Giro** — última escrita: `2026-05-24 20:00 UTC` (= **17:00 BRT**). Modificações em `app-state-sync-key-AAAAAHVu.json` e `app-state-sync-version-critical_block.json` na mesma hora — o socket Baileys gravou o "último estado" antes do servidor revogar a sessão.

2. **Container `giro-whatsapp-bridge` em crash loop desde então.** Stack trace constante (a cada ~1 min de uptime, 60s de restart_delay implícito):

   ```
   ⚡ Conexão fechada (reason=401). Deslogado – escaneie QR novamente.
   node:fs:1214
     binding.rmdir(pathModule.toNamespacedPath(path));
             ^
   Error: EBUSY: resource busy or locked, rmdir './auth_info'
       at rmdirSync (node:fs:1214:11)
       at file:///app/index.mjs:826:20
       at EventEmitter.<anonymous> (.../baileys/lib/Utils/event-buffer.js:35:16)
       at .../Socket/socket.js:255:12 {
     errno: -16, code: 'EBUSY', syscall: 'rmdir', path: './auth_info'
   }
   ```

3. **Commits do Transportes coincidem exatamente** com a janela ([transportes-samu git log](/home/ubuntu/transportes-samu)):

   ```
   ddd669e 2026-05-24 17:43:29 -0300 Merge PR#19 fix/ingest-load-env-production
   51c0dc4 2026-05-24 17:43:24 -0300 fix(ingest): also load .env.production from root
   6c3eb25 2026-05-24 17:42:13 -0300 Merge PR#18 fix/load-env-production-from-root
   7bb37c6 2026-05-24 17:42:08 -0300 fix(web): load .env.production from root
   ea5d496 2026-05-24 17:40:34 -0300 Merge PR#17 fix/port-3008
   eab3267 2026-05-24 17:40:00 -0300 fix(deploy): move web to port 3008
   79a7bab 2026-05-24 17:21:03 -0300 Merge PR#16 feat/phase-6-deploy-ec2
   9df7183 2026-05-24 17:20:35 -0300 feat(phase 6): deploy plumbing
   ```

   Entre 17:20 e 18:18 BRT houve deploy do Phase 6 + correções consecutivas. O re-pareamento WhatsApp do ingest provavelmente foi feito nesse intervalo (necessário porque `apps/ingest/auth/` veio vazio no checkout inicial do EC2).

4. **Log do ingest mostra o device-id 7** após o pareamento:

   ```
   {"ns":"baileys","node":{"username":"557197150415", "device":7, "connectReason":"USER_ACTIVATED", ...}, "msg":"logging in..."}
   ```

   `connectReason:"USER_ACTIVATED"` = pareamento iniciado por ação humana no celular. O Giro estava em `:3` e foi promovido a estado revogado quando o `:7` entrou.

### Mecanismo confirmado da cadeia de falha

1. ~17:20 BRT 24/05: deploy do Transportes na EC2; alguém escaneia QR/insere pairing code no celular do Chefe → ingest entra como device `:7`.
2. WhatsApp Multi-Device invalida o slot `:3` (Giro) — o servidor retorna `Disconnect` com `statusCode=401` (DisconnectReason.loggedOut) à conexão Baileys do bridge.
3. Bridge entra no ramo `else` do handler `connection.update` ([index.mjs:814-829](../whatsapp-bridge/index.mjs#L814)):
   - dispara `notifyTelegram(...)` para `TELEGRAM_ADMIN_CHAT_ID=1438288563` (mensagem "WhatsApp Bridge DESLOGADO")
   - executa `fs.rmSync(AUTH_DIR, { recursive: true, force: true })` na linha 826 — **AUTH_DIR é `/app/auth_info`, que é o mountpoint do volume nomeado `giro-de-leitos_wa_auth_info`**
   - `rmdir` num mountpoint resulta em `EBUSY` no kernel; `rmSync` joga exceção síncrona; o processo morre com exit code 1 **antes** de chegar no `setTimeout(() => startBridgeWithPairingCode(), 60_000)` da linha 828.
4. Docker compose tem `restart: unless-stopped` → container reinicia ~60s depois (delay observado).
5. Na próxima execução, o auth_info **AINDA ESTÁ INTACTO** (o rmSync falhou antes de remover qualquer arquivo do volume — `EBUSY` aborta o rmdir _do diretório raiz_, mas como `rmSync` é `rimrafSync` ele primeiro tenta varrer recursivamente; alguns arquivos podem ter sido removidos, outros não — checar o estado real do volume vai mostrar). Bridge reconecta com creds antigas, servidor retorna 401 de novo, ciclo se repete.
6. **`startBridgeWithPairingCode()` nunca roda** → o pareamento automático embutido no código nunca acontece → ninguém recebe um pairing code novo no Telegram → fica precisando de intervenção humana.

> Observação: o handler `notifyTelegram` da linha 822 _provavelmente_ chega a sair (é fetch HTTP assíncrono e o crash síncrono vem 4 linhas abaixo), mas como o processo morre logo em seguida pode ou não fechar. Em todo caso, isso é o porquê do "vigilante" reportar — o próprio bridge avisou pelo Telegram quando conseguiu, antes de morrer.

---

## Seção C — Plano de isolamento (sem executar)

Passos em ordem **crescente de risco** — os primeiros são reversíveis e só tocam código local; os últimos exigem janela de manutenção e re-pareamento controlado.

### C.1 — Corrigir o bug do `rmSync` no bridge do Giro **(risco MUITO BAIXO, faz mais sentido primeiro)**

Em [whatsapp-bridge/index.mjs:826](../whatsapp-bridge/index.mjs#L826), trocar:

```js
fs.rmSync(AUTH_DIR, { recursive: true, force: true });
```

por **limpeza do conteúdo do diretório, não do diretório em si**:

```js
// AUTH_DIR é um mountpoint de Docker volume; não podemos remover o
// diretório (EBUSY). Removemos apenas o conteúdo.
for (const f of fs.readdirSync(AUTH_DIR)) {
  fs.rmSync(path.join(AUTH_DIR, f), { recursive: true, force: true });
}
```

Isso destrava o crash loop atual sem mexer em nada do WhatsApp. **Mesmo antes de re-parear**, o container deixa de reiniciar a cada minuto.

> ⚠️ Cuidado: já que esse caminho roda em `loggedOut`, validar antes que ele realmente **deve** apagar creds — se a hipótese atual estiver errada (improvável, mas teoricamente o 401 pode vir por outra causa), perder creds vai forçar um re-pareamento desnecessário. Adicionar log antes do rm e talvez um *delay* opcional de 5min via env (`AUTH_WIPE_DELAY_S`) dá margem pra abortar.

### C.2 — `browser` distinto por instância **(risco BAIXO, só código)**

[whatsapp-bridge/index.mjs:706](../whatsapp-bridge/index.mjs#L706):

```js
browser: ["Giro-Leitos-SAMU", "Chrome", "120"],
```

[transportes-samu/apps/ingest/src/whatsapp/client.ts:43](/home/ubuntu/transportes-samu/apps/ingest/src/whatsapp/client.ts#L43):

```ts
browser: ["Transportes-SAMU", "Chrome", "120"],
```

Não previne o conflito de slot (o servidor WhatsApp casa por device-id, não por user-agent), mas:
- separa visualmente os dois no painel "Aparelhos conectados" do celular do Chefe (operador vê `Giro-Leitos-SAMU` e `Transportes-SAMU` em vez de duas linhas idênticas "Safari (macOS)") — reduz risco de operador deslogar a errada
- ajuda Baileys a ter cache key distinto em alguns paths internos
- aparece nos logs do servidor WhatsApp se eles cooperarem com pedido de revisão de banimento

### C.3 — Reconexão resiliente + circuit breaker **(risco BAIXO)**

Hoje, o bridge faz reconnect imediato em `else` de loggedOut. Refatorar `connection.update` para:

```js
import { DisconnectReason } from "@whiskeysockets/baileys";
// circuit breaker simples: depois de N reconexões em M minutos, parar e só notificar
let recentDisconnects = [];
function shouldOpenBreaker() {
  const now = Date.now();
  recentDisconnects = recentDisconnects.filter(t => now - t < 10 * 60_000); // 10 min
  return recentDisconnects.length > 5;
}
// ...
if (connection === "close") {
  recentDisconnects.push(Date.now());
  const sc = lastDisconnect?.error?.output?.statusCode;
  if (sc === DisconnectReason.loggedOut) {
    // não fazer rmSync agressivo — esperar intervenção
    notifyTelegram(`🚨 DESLOGADO (401). Não re-tentando automaticamente — risco de ban.`);
    return; // o container vai morrer naturalmente; restart_policy do compose decide o resto
  }
  if (shouldOpenBreaker()) {
    notifyTelegram(`🛑 Circuit breaker: ${recentDisconnects.length} desconexões em 10min. Parando reconexões.`);
    process.exit(0);
  }
  const delayMs = Math.min(60_000, 1000 * 2 ** Math.min(recentDisconnects.length, 6));
  setTimeout(() => startBridge(), delayMs);
}
```

Backoff exponencial até 60s + circuit breaker em 6 quedas/10min protege contra ban por reconnect loop. **Vale aplicar a mesma lógica do lado do Transportes** ([reconnect.ts](/home/ubuntu/transportes-samu/apps/ingest/src/whatsapp/reconnect.ts) já existe — vale revisitar).

### C.4 — Auth state isolado por path + nomes inequívocos **(risco BAIXO)**

Mesmo já estando isolados em disco hoje, padronizar:

- Giro: `WHATSAPP_AUTH_DIR=/var/lib/giro-wa-auth` (host path bind, não volume nomeado — torna o `rmSync` viável caso necessário no futuro), gitignored, backup diário.
- Transportes: manter `WA_SESSION_DIR=/home/ubuntu/transportes-samu/apps/ingest/auth`, **mover para `/var/lib/transportes-wa-auth`** se quiser desacoplar do checkout do repo (hoje, um `rm -rf` acidental do checkout apaga a sessão).

Migração: parar bridge → `cp -a` do conteúdo do volume nomeado para o novo path → ajustar compose mount → subir. Não muda o pareamento porque os arquivos permanecem.

### C.5 — Estratégia de números WhatsApp **(decisão estratégica + risco MÉDIO)**

**Opção 1: dois números distintos (recomendada para SAMU)**

| Prós | Contras |
|---|---|
| Isolamento total — qualquer manutenção em uma app não derruba a outra | Custo de uma linha extra (chip + plano) |
| Fim do risco de "device 4 atingido" → cabe gente também usando WA Web | Operador precisa autorizar dois números nos grupos |
| Cada número pode ter seu próprio runbook de re-pareamento sem janela coordenada | Mudança de comunicação na regional — vai ter atrito |

**Opção 2: um número, um único processo Baileys ("WA gateway")**

Subir um serviço `wa-gateway` (Node, single process) que detém a conexão Baileys e expõe API HTTP/Redis interna. Giro e Transportes consomem essa API em vez de cada um manter sua sessão.

| Prós | Contras |
|---|---|
| Resolve conflito de device por construção — só uma sessão Baileys existe | Single point of failure: se o gateway cai, ambas apps perdem WA |
| Reduz risco de ban (1 fingerprint em vez de 2) | Trabalho de engenharia maior: precisa redesenhar fluxo de mensagens das duas apps |
| Receber/enviar centralizado simplifica observabilidade | Acopla evolução das apps via API do gateway |

**Recomendação para o contexto SAMU**: começar com **Opção 1** (números distintos) — é o caminho mais simples, isola operacionalmente, e a opção de gateway pode ser adicionada depois se aparecer um terceiro consumidor WA na infra. Custo de uma linha pré-paga não chega perto do custo de uma queda em horário de regulação.

> ⚠️ Se ficar Opção 1: o re-pareamento do número novo no Giro deve ser feito **fora do horário de pico** do SAMU (06h-08h ou 23h-01h BRT), e **antes** os 3-5min de QR/pairing devem ter testes prontos (`./tests/test_parser_regressions.py`) pra detectar regressão.

### C.6 — PM2 ecosystem por app + deploy com `--only` **(risco BAIXO)**

Hoje cada app já tem seu `ecosystem.config.cjs`. **NÃO há `pm2 reload all` nos deploys do Transportes** ([deploy.yml + deploy-production.sh](/home/ubuntu/transportes-samu/.github/workflows/deploy.yml)) — o script usa o ecosystem específico. Validar formalmente:

- Adicionar nota no [deploy-production.sh](/home/ubuntu/transportes-samu/scripts/) proibindo `pm2 reload all`/`pm2 restart all`
- Idem em [plantoes/ecosystem.config.cjs](/home/ubuntu/plantoes/ecosystem.config.cjs) e demais
- Considerar PM2 namespaces (`--namespace transportes`) para reforçar fronteira: `pm2 reload --namespace transportes` reinicia só transportes-web + transportes-ingest

Risco hoje: **baixo**, já está OK na prática; só falta documentar e proibir explicitamente.

### C.7 — Postgres: já está isolado **(nenhuma ação)**

Verificado via `\l` e `\du`:

| App | Banco | Dono/Role |
|---|---|---|
| Giro | `giro_de_leitos` | `giro` |
| Transportes | `transportes_samu` | `transportes_samu` |
| Plantões | `plantoes` | `plantoes` |
| NEP-SAMU | `nep_samu` | `nep_samu` |
| Tabela | `tabela` | `tabela` |

Sem grants cruzados, sem search_path compartilhado. **Nenhuma ação necessária** — mas **registrar isso** no `OPERATIONAL_RULES.md` para que ninguém crie role admin compartilhado no futuro.

### C.8 — Alerta proativo via heartbeat **(risco BAIXO–MÉDIO)**

Hoje o "vigilante" do bridge é **reativo**: dispara `notifyTelegram` quando recebe 401. Mas:
- não há heartbeat positivo ("estou up há X minutos")
- silêncio prolongado (ex: container morto antes de mandar o notify) só é percebido por humano

Propor:
1. **Heartbeat para Telegram pelo bridge**: a cada 30 min, postar uma `:thumbsup:` invisível (mensagem com texto curto tipo `🟢 hb 17:00`) no chat do admin. Se 2 heartbeats consecutivos faltarem → alerta no celular do admin (regra Telegram nativa via menção, OU usar segundo bot watchdog).
2. **Heartbeat HTTP no parser-api**: `/health/whatsapp` retorna 200 se bridge enviou alguma mensagem nas últimas 2h; 503 se não. Pluga em `smoke_public_stack.sh` (já roda a cada 5min via cron).
3. **Bot watchdog separado** (pode reaproveitar `samu-bot` em `/var/www/samu-ai/`): chama `/health/whatsapp` do Giro e do Transportes a cada 5min; se 2 falhas seguidas, manda alerta com `@chefe`.

Implementar o item (3) primeiro — não invasivo, e fecha o gap do "vigilante não detectou quando".

### C.9 — Runbook de re-pareamento **(risco BAIXO, só documentação)**

Criar `/home/ubuntu/giro-de-leitos/docs/RUNBOOK-REPAIRING.md` com:

```
PRÉ-CONDIÇÕES
- [ ] Janela 06h-08h ou 23h-01h BRT (evitar pico)
- [ ] Confirmar com Chefe de Plantão que ele NÃO vai re-parear o outro app na mesma janela
- [ ] Backup do volume de auth ANTES: docker run --rm -v giro-de-leitos_wa_auth_info:/v alpine tar -czf /tmp/wa-backup-$(date +%F).tgz /v
- [ ] Telegram admin chat acessível (alertas vão aparecer)

PASSOS PARA RE-PAREAR O GIRO
1. docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml stop whatsapp-bridge
2. Limpar auth: docker volume rm giro-de-leitos_wa_auth_info && docker volume create giro-de-leitos_wa_auth_info
3. docker compose up -d whatsapp-bridge
4. docker compose logs -f whatsapp-bridge → aguardar QR ou pairing code
5. No celular do Chefe: WhatsApp → Aparelhos conectados → Conectar → escanear QR
6. Confirmar "✅ WhatsApp connected" no log
7. Mandar mensagem teste no grupo alvo, ver chegada na API

EFEITO NO TRANSPORTES
- Re-parear o Giro vai forçar o WhatsApp a remover o slot do Transportes-ingest também (mesmo número)
- → Transportes vai entrar em loggedOut imediatamente após o Giro entrar
- → re-parear o Transportes em seguida (mesmo ritual, mas em apps/ingest/auth/)
- ORDEM IMPORTA: re-parear Giro primeiro, Transportes depois

PARA EVITAR O DUPLO-PAREAMENTO: usar números distintos (opção 1 da Seção C.5).
```

### C.10 — Resolver o crash secundário do `transportes-ingest` **(escopo separado, anotar)**

Não é causa raiz do incidente do Giro, mas: o ingest está restartando há 17h por `DATABASE_URL is required` apesar do `.env.production` listar `DATABASE_URL`. Isso é o efeito esperado do commit [51c0dc4](https://github.com/caiooliveirac/transportes-samu/commit/51c0dc4) ("fix(ingest): also load .env.production from root") — mas o reload do PM2 pode ter sido feito sem `--update-env`, ou o `env_file` do ecosystem não está sendo aplicado em fork mode. Investigar separadamente (ver Seção E).

---

## Seção D — Riscos

### D.1 Ban do WhatsApp em re-pareamento frequente

- WhatsApp banuje contas (não devices, **contas inteiras**) que entram em padrões anormais: muitos pareamentos em curto intervalo, conexões persistentemente headless, mensagens em padrão automatizado pra muitos grupos. A conta do Chefe está em risco moderado hoje porque:
  - tem 2 devices simultâneos via Baileys (não-humano)
  - 17h em loop de reconexão a 401 (provavelmente o servidor já vê isso)
  - re-pareamentos coordenados entre 2 apps no mesmo número
- **Mitigação**: NÃO re-parear de imediato; primeiro aplicar C.1 + C.3 (parar o loop), aguardar 2-4h, e só então re-parear em janela controlada. Considerar mudar o `browser` e o `keepAliveIntervalMs` (já é randomizado nos dois) para parecer menos automatizado.

### D.2 Compatibilidade Baileys ↔ WhatsApp Web

- Versão usada: Baileys `^6.7.16`, protocolo `2.3000.1035194821`. Não há WA notable change neste range no momento do relatório (2026-05-25), mas o `^` no semver permite minor bumps que podem trazer regressões em produção. Recomendação: **pin exato** em ambos os `package.json` (`6.7.16`) até validar a próxima minor manualmente.
- O Baileys 7.x está em alpha; **não** mudar para 7.x sem teste em staging.

### D.3 Janela de manutenção

- Horários de pico SAMU/regulação de leitos: ~08h-12h e 14h-20h BRT.
- Janelas seguras para re-pareamento: **06h-08h** e **23h-01h** BRT (preferir o segundo, menos urgências).
- Domingos têm fluxo menor em geral; quinta/sexta são piores.

### D.4 Risco específico do Opção 2 (gateway único)

- Single point of failure: precisaria PM2 com `instances:1` (Baileys não escala horizontal), monitoramento agressivo, e fallback documentado se ele cair (provavelmente "voltar para 2 sessões temporárias").

### D.5 Falha no fix do `rmSync` (C.1)

- O loop `for (const f of fs.readdirSync(AUTH_DIR))` pode também falhar se um dos arquivos estiver sendo escrito por Baileys naquele instante (race condition). Workaround: try/catch por arquivo, com log de quais não foram apagados.

---

## Seção E — Próximas ações (priorizadas, depois da sua revisão)

### Imediato (próximos 30 min)

1. **[risco BAIXO]** Aplicar fix em [whatsapp-bridge/index.mjs:826](../whatsapp-bridge/index.mjs#L826) (Seção C.1) — substituir `fs.rmSync(AUTH_DIR)` por loop de readdir. Build + `docker compose up -d --build whatsapp-bridge`. **Vai parar o crash loop sem re-parear**, mas o bridge ficará em estado "Deslogado, aguardando pareamento" — sem perda de mensagens novas (já estamos perdendo há 18h), e sem reconexão hostil ao servidor WA. Sem risco de ban.

2. **[risco MUITO BAIXO]** Documentar no Telegram que o Giro está esperando re-pareamento — silenciar (`/mute`) o chat de alertas pelas próximas X horas pra não acumular notificações enquanto se prepara o re-pareamento.

3. **[risco BAIXO]** Investigar e corrigir o `DATABASE_URL is required` do `transportes-ingest` (Seção C.10). Verificar `pm2 env 7` para confirmar se DATABASE_URL está populada; se não, `pm2 reload ecosystem.config.cjs --update-env` na cwd do transportes-samu.

### Curto prazo (próxima janela 23h-01h BRT)

4. **[risco MÉDIO]** Decidir entre **Opção 1** (número novo para Giro) e **Opção 2** (gateway único). Recomendação: Opção 1.

5. **[risco MÉDIO]** Re-parear o Giro seguindo o runbook (Seção C.9). Se ficar com mesmo número: re-parear Transportes em seguida (ordem importa).

6. **[risco BAIXO]** Aplicar Seções C.2 (browser distinto), C.3 (circuit breaker), C.4 (auth path host).

### Médio prazo (próxima semana)

7. **[risco BAIXO]** Implementar heartbeat watchdog (Seção C.8) — começar pelo item 3 (samu-bot consultando `/health/whatsapp`).

8. **[risco BAIXO]** Documentar tudo em `OPERATIONAL_RULES.md` do Giro e Transportes — incluindo runbook de re-pareamento e regras de PM2.

9. **[risco BAIXO]** Pin de versões `@whiskeysockets/baileys` (Seção D.2).

### Não fazer agora

- ❌ NÃO reiniciar o container do bridge sem o fix (continuará crashando)
- ❌ NÃO re-parear o WhatsApp antes de C.1 (vai disparar 401 no Transportes-ingest e duplicar o problema)
- ❌ NÃO mexer no creds.json existente — pode ainda dar pra recuperar se o WhatsApp não tiver expirado as keys
- ❌ NÃO fazer `pm2 reload all` no Transportes ou em qualquer app desse host

---

---

## Seção F — Decisão arquitetural pendente

> **Status do trabalho**: Fase 1 do plano de execução (fix do bridge) foi
> aplicada e commitada (`deeda13`) — o crash loop está **interrompido em
> código**, mas o container **ainda não foi reiniciado**, por escolha
> deliberada. Decisão A vs B deve ser tomada **antes** do restart, porque
> ela define se vamos usar o mesmo número (e arriscar o ping-pong de novo)
> ou se vamos migrar para arquitetura definitiva.

### F.1 Comparação Opção A vs. Opção B

Escala assumida: **3 = alto, 2 = médio, 1 = baixo** (preencher por critério).

| Critério | A: Números distintos | B: Gateway Baileys único |
|---|---|---|
| Esforço de implementação (h-engenheiro) | **3-5h** — env var nova, re-pareamento com número novo, atualização do envio de mensagens (Giro responde no grupo) | **20-30h** — novo serviço Node, API HTTP/Redis, refactor de Giro e Transportes para consumir |
| Custo recorrente (R$/mês) | **R$ 15-40** (chip pré-pago ou linha M2M) | **R$ 0** (mesma infra) |
| Tempo de bootstrap em produção | ~30 min (re-pareamento + smoke test) | 2-4h (subir gateway + migrar 2 apps em janela de manutenção coordenada) |
| Risco de regressão imediata | **BAIXO** (cada app vira mais isolada) | **MÉDIO** (refactor cruzado, edge cases de delivery) |
| Escalabilidade para novas apps SAMU | **Cada app nova → mais 1 número** | **App nova só consome a API do gateway, número único** |
| Impacto operacional (chefes de plantão precisam re-cadastrar contato em grupos?) | **SIM, num grupo** — o grupo de regulação de leitos precisaria adicionar o novo número e remover/manter o antigo dependendo do uso. Chefe de Plantão fica fora desse grupo se quiser. | **NÃO** — usuário final continua vendo "Chefe de Plantão" mandando mensagens |
| Complexidade de manutenção | **BAIXO** (status quo, só 1 chip a mais) | **MÉDIO-ALTO** (gateway é serviço novo a operar, monitorar, deployar, ter runbook) |
| Risco de ban WhatsApp | **MÉDIO** — dois números headless, mas cada um tem fingerprint próprio e tráfego mais baixo individualmente | **BAIXO** — único número, tráfego centralizado e monitorável, pode usar mensagens menos automatizadas |
| Single point of failure | **NÃO** — falha de uma app não afeta a outra | **SIM** — gateway down → ambas apps perdem WA. Mitigável com HA, mas isso é mais trabalho. |
| Visibilidade pro Chefe de Plantão | **CONFUSO** — ele vê dois números "Giro-Leitos" e "Transportes" pareados no celular dele e pode deslogar o errado | **TRANSPARENTE** — vê só uma sessão (do gateway) e a aplica para tudo |
| Quantidade de re-pareamentos no futuro | **Provavelmente menos** — re-parear uma app não derruba a outra | **Único ponto de re-pareamento** — quando precisar, é só lá |
| Pré-requisito para mensagens **enviadas** (não só recebidas) | nenhum extra | exige API stateful pra escolher "qual app está respondendo" e qual contexto de conversa |

#### Esforço estimado em horas (mais detalhe)

**Opção A:**
- Adquirir chip/linha + ativar WhatsApp: 30 min – 2h (depende de fornecedor)
- Re-parear Giro com número novo (runbook abaixo): 30 min em janela noturna
- Atualizar `WHATSAPP_PHONE_NUMBER` no `.env` do Giro: 5 min
- Smoke test (mensagem teste → ingestão → DB → dashboard): 30 min
- Documentação: 1h
- **Total: ~3-5h em uma janela 23h-01h BRT.**

**Opção B:**
- Design do gateway (decidir HTTP vs Redis pub/sub, schema de mensagens, autenticação interna): 4-6h
- Implementação do gateway Baileys + endpoints `/messages`, `/health`, `/send`: 8-12h
- Adaptar Giro (`whatsapp-bridge` vira consumer Redis em vez de socket Baileys): 4-6h
- Adaptar Transportes-ingest (idem): 4-6h
- Testes E2E + deploy coordenado em janela: 4-6h
- Documentação + runbook: 2h
- **Total: ~25-40h em ~1-2 semanas, com 1 janela de deploy.**

#### Minha recomendação técnica

**Para a situação atual (1 incidente, 2 apps, prazo de horas), Opção A.** Faz
o problema sumir em uma janela noturna, custo trivial, e desbloqueia o Giro
sem dependência de novo serviço.

**Para o roadmap de médio prazo (mais apps SAMU vindo: Taxímetro talvez
precisa, painel de vagas pode usar), considerar implementar B em paralelo
nas próximas 2-4 semanas** — mas como projeto separado, não como bloqueador
do incidente atual.

O caminho mais conservador: A agora (resolve hoje), B no próximo trimestre
(arquitetura definitiva).

---

### F.2 Esboço técnico da Opção B (gateway único)

Se a decisão for B (agora ou depois), arquitetura mínima:

```
                           ┌───────────────────────────────┐
                           │  wa-gateway (PM2 process)     │
                           │                                │
   WhatsApp Multi-Device ←─┤  - Baileys single socket       │
   (1 device-id, 1 número) │  - Persistência: PG nativo     │
                           │    (db: wa_gateway, table:     │
                           │     msg_inbound / msg_outbound)│
                           │  - API HTTP: localhost:3050    │
                           │  - Redis pub/sub interno       │
                           └──────────┬────────────┬────────┘
                                      │            │
                            ┌─────────┘            └─────────┐
                            │                                │
                  wa:msg:in    POST /send             wa:msg:in    POST /send
                            │                                │
                 ┌──────────▼──────────┐         ┌──────────▼──────────┐
                 │  Giro de Leitos     │         │ Transportes-ingest  │
                 │  (parser-api +      │         │ (worker PM2)        │
                 │   sem bridge Node)  │         │                     │
                 └─────────────────────┘         └─────────────────────┘
```

**Endpoints**:
- `GET /health` → `{status:"open"|"closed"|"connecting", device:7, uptime:1234}`
- `POST /send` `{to:"557181082189-1462561641@g.us", text:"...", app:"giro"}` → 202
- `GET /pair` (admin-only via token) → retorna pairing code ou QR base64
- `POST /restart` (admin-only) → reinicia sessão Baileys

**Eventos** (Redis pub/sub):
- Canal `wa:msg:in` → publica `{messageId, from, jid, text, ts, raw}` para todos
  os subscribers (Giro filtra grupo dele, Transportes filtra grupo dele)
- Canal `wa:msg:status` → `{messageId, status:"sent"|"delivered"|"read"}` quando
  uma `/send` é confirmada

**Tabelas PG (db `wa_gateway`)**:
```sql
CREATE TABLE msg_inbound (
  id BIGSERIAL PRIMARY KEY,
  wa_message_id TEXT UNIQUE NOT NULL,
  remote_jid TEXT NOT NULL,
  sender_jid TEXT,
  text TEXT,
  raw JSONB,
  received_at TIMESTAMPTZ DEFAULT NOW(),
  forwarded_to TEXT[] DEFAULT '{}'  -- ["giro", "transportes"]
);

CREATE TABLE msg_outbound (
  id BIGSERIAL PRIMARY KEY,
  app TEXT NOT NULL,        -- "giro" | "transportes"
  to_jid TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL,     -- "queued" | "sent" | "failed"
  wa_message_id TEXT,
  requested_at TIMESTAMPTZ DEFAULT NOW(),
  sent_at TIMESTAMPTZ
);

CREATE INDEX idx_msg_inbound_received_at ON msg_inbound (received_at DESC);
```

**Localização**:
- Diretório: `/home/ubuntu/wa-gateway/`
- PM2: `wa-gateway` (porta 3050 só em 127.0.0.1)
- Banco: `wa_gateway` no PG nativo, role `wa_gateway`
- Volume auth: `/var/lib/wa-gateway-auth/` (host filesystem, não Docker — evita o problema do EBUSY)
- Nginx: **NÃO expor publicamente**. Acesso só de 127.0.0.1.

**Migração das apps existentes**:
- Giro: remover o serviço `whatsapp-bridge` do `docker-compose.yml`; o
  `parser-api` (FastAPI) vira subscriber Redis no canal `wa:msg:in` e usa
  `POST /send` quando precisa responder.
- Transportes-ingest: substituir `apps/ingest/src/whatsapp/*` por adapter
  que consome do mesmo Redis.

**Riscos específicos**:
- Single sock Baileys = 1 ponto único de queda. Mitigar com supervisão PM2
  agressiva + circuit breaker + alertas heartbeat.
- Se gateway cair, **as duas apps ficam offline** ao mesmo tempo. Mais grave
  que ter cada uma com sua sessão. Compensação: gateway é simples (~500
  linhas), poucos pontos de falha.

---

### F.3 Runbook de re-pareamento controlado

Pré-requisitos antes de executar:

- [ ] **Decisão A ou B já tomada** e seu plano correspondente implementado.
- [ ] **Janela 23h-01h BRT** (preferencial — menos urgências SAMU).
- [ ] **Avisar Chefe de Plantão noturno** que vai re-parear — pedir para
      ele não tocar no app de WhatsApp durante os ~5 minutos do
      procedimento.
- [ ] **Backup do volume de auth do Giro**:
      ```bash
      docker run --rm -v giro-de-leitos_wa_auth_info:/v -v /tmp:/backup \
        alpine sh -c "cd /v && tar czf /backup/wa-backup-$(date +%F-%H%M).tgz ."
      ls -lh /tmp/wa-backup-*.tgz
      ```
- [ ] **Backup do auth do Transportes**:
      ```bash
      tar czf /tmp/transportes-wa-backup-$(date +%F-%H%M).tgz \
        -C /home/ubuntu/transportes-samu/apps/ingest auth/
      ```
- [ ] Telegram admin chat acessível (alertas vão aparecer).
- [ ] Acesso SSH/`code .` ao servidor confirmado.

#### Procedimento — Opção A (números distintos)

Assumindo que o chip novo já foi adquirido e ativado (número `XXXXXXXXXX`):

1. **Atualizar env do Giro** (sem reiniciar):
   ```bash
   # Editar /home/ubuntu/giro-de-leitos/.env
   # Trocar:  WHATSAPP_PHONE_NUMBER=5571997150415
   # Por:     WHATSAPP_PHONE_NUMBER=<NUMERO_NOVO>
   ```

2. **Limpar auth antigo do Giro** (com o fix da Fase 1.1, o próprio handler
   loggedOut faria isso, mas vamos fazer explicitamente):
   ```bash
   docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml stop whatsapp-bridge
   # Não use 'docker volume rm' — perde o estado do circuit breaker.
   # Em vez disso, entre no volume e apague só o conteúdo:
   docker run --rm -v giro-de-leitos_wa_auth_info:/v alpine \
     sh -c "find /v -mindepth 1 -not -path '/v/.bridge-state*' -delete"
   ```

3. **Subir o bridge** — vai iniciar pairing code automaticamente (porque
   `state.creds.registered` será `false`):
   ```bash
   docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml up -d --build whatsapp-bridge
   docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml logs -f whatsapp-bridge
   ```
   Aguardar mensagem no Telegram:
   `🔑 Código de pareamento WhatsApp: XXXX-XXXX`

4. **No celular do novo número**: WhatsApp → Configurações → Aparelhos
   conectados → Conectar dispositivo → "Conectar com número de telefone"
   → digitar o código.

5. **Confirmar conexão** no log:
   `✅ Reconectado via pairing code!`

6. **Verificação operacional**:
   ```bash
   # Mandar mensagem teste no grupo alvo (a partir do celular do Chefe de
   # Plantão, no número antigo — o NOVO só observa).
   # Conferir nos logs:
   docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml \
     logs whatsapp-bridge | grep "messages.upsert" | tail
   # Confirmar via API:
   curl -s http://localhost:8000/api/summary | jq '.last_event_at'
   ```

7. **Smoke completo**: simular um giro em algum UPA conhecido e ver se ele
   aparece no dashboard.

#### Critério de rollback

Se em até 15 min após restart:
- Não chegar pairing code no Telegram, OU
- Pairing code falhar (erro `link too many times` etc), OU
- Após pareamento, mensagens do grupo não estão aparecendo no log

→ **Rollback**:
```bash
docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml stop whatsapp-bridge
docker run --rm -v giro-de-leitos_wa_auth_info:/v -v /tmp:/backup \
  alpine sh -c "cd /v && find . -mindepth 1 -not -path './.bridge-state*' -delete && tar xzf /backup/wa-backup-YYYY-MM-DD-HHMM.tgz"
# Reverter WHATSAPP_PHONE_NUMBER no .env
docker compose -f /home/ubuntu/giro-de-leitos/docker-compose.yml up -d whatsapp-bridge
```

E abrir incidente no Telegram admin chat — investigar com calma no dia
seguinte, fora de plantão.

#### Para Opção B (gateway único)

O runbook é diferente — re-pareia só o gateway, não cada app. Será
escrito quando/se a Opção B for escolhida.

---

### F.4 Riscos de ban WhatsApp

**Estado atual do número 557197150415 (do levantamento)**:

- Foi pareado pelo menos **2 vezes** nas últimas 30 dias (devices `:3` e
  `:7` ativos; provavelmente outros antes — não temos histórico no Baileys
  client local).
- Re-pareamento de ontem foi feito por **deploy automatizado** (Phase 6
  deployer), o que para o servidor WhatsApp é indistinguível de uma ação
  humana **mas** em horário de pico, sem aviso prévio à conta.
- Desde 17h ontem (BRT): ~1080 reconexões falhando (~3/min em algumas
  janelas) — o servidor WhatsApp **registra** esse padrão.

**Práticas que aumentam risco**:
- Reconexão em loop com a mesma sessão sem cool-down (foi o que aconteceu)
- Múltiplos devices headless no mesmo número
- Mensagens automatizadas em padrão fixo (ex: alerta de UPA inativo em
  horário cravado, sempre com mesmo template)
- Broadcast lists com >5 destinatários
- Pareamento por pairing code (vs QR) é considerado por alguns
  observadores como sinal de "automação" — embora não confirmado pela Meta

**Práticas que reduzem risco** (já adotadas no código atual):
- `keepAliveIntervalMs: randInt(25_000, 55_000)` — bom
- `markOnlineOnConnect: false` — bom
- `simulateTyping()` antes de enviar mensagem — bom
- Alertas em horários BRT semi-fixos com pequena variação — bom

**Recomendação de uso conservador até estabilização**:

1. **Não re-parear nada por 24-48h** após o fix C.1 ser aplicado e o
   container reiniciado, mesmo que o circuit breaker permita. Deixar o
   servidor WhatsApp "esquecer" o padrão de loop.
2. Quando for re-parear (Fase 3), fazer em **horário noturno** e
   **um número de cada vez** com pelo menos 30 min de intervalo.
3. **Limitar o volume de mensagens enviadas** das duas apps para o grupo
   nas próximas 2 semanas: não mais que 30 mensagens/dia de cada app.
4. **Backup periódico** do auth_info (cron diário): tendo o backup,
   re-pareamento se torna raríssimo.

**Se o número for banido**:
- WhatsApp não dá recurso fácil. Caminho realista: trocar o número.
- Backup do conteúdo (mensagens) é local nos creds + DB — não é perdido.
- Grupos precisam re-adicionar o novo número.
- Por isso a Opção A já te dá um "número de reserva" embutido — se o atual
  cair, a outra app continua. Argumento adicional a favor de A.

---

### F.5 TODOs separados (não bloqueiam decisão)

Itens descobertos durante o levantamento que não são causa raiz, mas
deveriam ser endereçados em algum momento:

- **Heartbeat ativo** (Seção C.8): contrato com o vigilante não está claro.
  `samu-bot` em `/var/www/samu-ai/` polleia HTTP de outra app
  (`mnrs.com.br/tabela/api/cases`), não recebe heartbeats. Opções:
  - Adicionar endpoint `/health/whatsapp` no parser-api do Giro e no `transportes-web` (estes já sondados via nginx); cron `smoke_public_stack.sh` consome e alerta.
  - Subir um watchdog separado (Node ou Python simples) com webhook Telegram.
  - **Bloqueador**: precisa decidir contrato (heartbeat push vs pull).
- **Transportes-ingest** acabou se estabilizando sozinho — os 4 restarts
  foram durante o deploy de ontem. Os erros `DATABASE_URL is required` no
  `error.log` são históricos. Não restartar agora (risco de `identity changed`
  de novo no servidor WA).
- **Versão do Baileys** está em `^6.7.16` em ambos apps; pinar exato
  (`6.7.16`) após validar a próxima atualização manualmente.
- **Arquivos órfãos no repo do Giro**: existem `ed_events` e `ql -U giro
  -h localhost -d giro_de_leitos -t -c \d parsed_events` no working tree
  do Giro — parecem ser output acidental de comandos psql. Revisar e
  apagar separadamente (não tocados neste trabalho).
- **Trabalho em andamento não commitado no Giro**: `db.py`, `docker-compose.yml`,
  `main.py`, `parser_service.py`, `tests/test_parser_regressions.py`,
  `units.py` estão modificados localmente desde antes deste trabalho —
  não foram tocados aqui, mas merecem atenção (provavelmente do esforço
  anterior de "detecção de anomalias de horário", commit `c631117`).

---

## Apêndice — Comandos de verificação read-only usados

```bash
# Estado dos containers Giro
docker ps --filter name=giro
docker logs giro-whatsapp-bridge --tail 300 --timestamps
docker inspect giro-whatsapp-bridge --format '{{json .State}}'

# Volume de auth Giro
sudo ls -la /var/lib/docker/volumes/giro-de-leitos_wa_auth_info/_data/
sudo cat /var/lib/docker/volumes/giro-de-leitos_wa_auth_info/_data/creds.json

# Auth Transportes
cat /home/ubuntu/transportes-samu/apps/ingest/auth/creds.json
ls -la /home/ubuntu/transportes-samu/apps/ingest/auth/

# Estado PM2
pm2 list
pm2 show transportes-ingest
tail -300 /home/ubuntu/.pm2/logs/transportes-ingest-error.log

# Baileys refs
grep -rln "@whiskeysockets/baileys" /home/ubuntu --include=package.json

# Postgres
sudo -u postgres psql -c "\l"
sudo -u postgres psql -c "\du"

# Portas
ss -tlnp

# Nginx
sudo ls -la /etc/nginx/sites-enabled/

# Git histórico do incidente
cd /home/ubuntu/transportes-samu && git log --since="2026-05-24" --pretty=format:'%h %ai %s'
```
