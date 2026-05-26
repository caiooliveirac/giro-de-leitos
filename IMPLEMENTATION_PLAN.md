# Plano de implementação — Giro de Leitos web app

Branch: `frontend-design-preview` (não vai pra produção até validação visual).

## Status do design de referência

WebFetch e curl direto em `https://api.anthropic.com/v1/design/h/5jlEl7JDjGkvftTvWhnUIw` retornam **HTTP 404**. O artefato não está acessível externamente. Decisão (autorizada pelo usuário): seguir construindo a estrutura completa usando a estética descrita no brief (iOS Health / iOS 17: cards de superfície clara, tipografia SF/Inter, raios 18–24px, micro-interações com spring, dark mode nativo). Quando o HTML aparecer em `design/Giro de Leitos.html` a camada visual é refinada — todo o backend e a arquitetura React seguem válidos.

## Endpoints existentes (preservar)

Saúde:
- `GET /health`, `GET /api/health`

Eventos / resumo (parser legado):
- `GET /api/last-event`
- `GET /api/history`
- `GET /api/summary`
- `GET /api/alerts`
- `GET /api/units`
- `PATCH /api/units/{unit_key}/reported-at`

Ingestão:
- `POST /api/ingest/manual`
- `POST /api/webhook/telegram`
- `POST /api/webhook/whatsapp`
- `POST /api/ingest/whatsapp-bridge`

Outros:
- `GET /api/playground`
- `GET /api/telegram/status`
- `WS /ws/dashboard`
- Admin legado: login/usuários por env `ADMIN_USER`/`ADMIN_PASSWORD`, atualização e remoção de eventos.

Tudo isso continua intocado. As novas rotas vivem em prefixos disjuntos (`/api/auth/*`, `/api/invites/*`, `/api/users/*`, `/api/unit/{id}/*`).

## Endpoints novos por fase

### Fase 2 — Auth & convites
- `POST /api/auth/admin/login`
- `POST /api/auth/device/generate-code`
- `POST /api/auth/device/pair`
- `GET  /api/auth/me/unit/staff`
- `POST /api/auth/shift/start`
- `POST /api/auth/shift/end`
- `POST /api/auth/pin/verify`
- `POST /api/invites`
- `GET  /api/invites`
- `GET  /api/invites/{token}/preview` (público)
- `POST /api/invites/{token}/accept` (público)
- `POST /api/invites/{id}/revoke`
- `GET  /api/users/pending`
- `POST /api/users/{id}/approve`
- `POST /api/users/{id}/reject`
- `POST /api/users/{id}/suspend`

### Fase 3 — Operação dos leitos
- `GET  /api/unit/{unit_id}/state`
- `GET  /api/unit/{unit_id}/sectors/config`
- `PUT  /api/unit/{unit_id}/sectors/config`
- `PUT  /api/unit/{unit_id}/beds/{bed_number}`
- `POST /api/unit/{unit_id}/beds/{bed_number}/discharge`
- `POST /api/unit/{unit_id}/beds/{bed_number}/death` (exige `X-PIN-Confirm`)
- `POST /api/unit/{unit_id}/beds/{bed_number}/transfer`
- `POST /api/unit/{unit_id}/beds/{bed_number}/clear`
- `PUT  /api/unit/{unit_id}/counters/{sector_key}`
- `PUT  /api/unit/{unit_id}/specialists/{sector_key}`
- `PUT  /api/unit/{unit_id}/exams/{sector_key}`
- `WS   /ws/unit/{unit_id}` — `bed_updated`, `counter_updated`, `specialist_updated`, `exam_updated`

Locking otimista: `If-Match: <version>` por recurso; 409 + estado atual no body.

## Migrações de banco

Arquivo único idempotente: `migrations/001_auth_and_beds.sql`. Pode ser aplicada por psql ou pelo `init_db` (estendido).

Tabelas novas:
- `users`, `units` (promoção do registry), `unit_aliases`, `unit_sectors_config`,
- `beds`, `counters`, `specialists`, `exams`,
- `invites`, `auth_sessions`, `trusted_devices`, `audit_log`, `notification_queue`.

Crypto:
- CPF cifrado (Fernet, `CPF_ENCRYPTION_KEY`) + `cpf_hash` (sha256) pra busca/duplicata.
- Senha: bcrypt cost 12. PIN: bcrypt cost 10. Token convite: `secrets.token_urlsafe(32)`.

Seed:
- `scripts/seed_admin.py` lê `ADMIN_INITIAL_EMAIL`, `ADMIN_INITIAL_PASSWORD`, `ADMIN_INITIAL_NAME`, `ADMIN_INITIAL_PIN` e cria/atualiza o admin root.
- Migra UPAs de `units.py` (UNIT_REGISTRY) pra tabela `units` com aliases preservados em `unit_aliases`.

## Estrutura proposta — backend

```
.
├── main.py                  # rotas legadas + monta novos routers
├── db.py                    # init_db estendido (aplica migrations/*.sql)
├── parser_service.py        # intocado
├── units.py                 # intocado (fonte do seed)
├── migrations/
│   └── 001_auth_and_beds.sql
├── auth/
│   ├── __init__.py
│   ├── router.py
│   ├── service.py
│   ├── deps.py
│   ├── schemas.py
│   ├── crypto.py            # bcrypt, Fernet, jwt, pin
│   └── audit.py             # @audited decorator
├── beds/
│   ├── __init__.py
│   ├── router.py
│   ├── service.py
│   ├── schemas.py
│   └── ws.py                # connection manager por unit_id
├── services/
│   └── notifications.py     # WhatsApp bridge + fila
├── scripts/
│   └── seed_admin.py
└── tests/
    ├── test_auth.py
    └── test_beds.py
```

## Estrutura proposta — frontend

```
frontend/
├── package.json
├── next.config.js           # rewrites /api e /ws -> :8000 em dev
├── tailwind.config.ts       # tokens semânticos iOS-Health
├── tsconfig.json (strict)
├── app/
│   ├── layout.tsx
│   ├── globals.css
│   ├── (public)/
│   │   └── convite/[token]/page.tsx
│   ├── (auth)/
│   │   ├── pair/page.tsx
│   │   └── shift/page.tsx
│   └── (app)/
│       ├── page.tsx                 # tela principal operador
│       ├── configurar/page.tsx
│       ├── equipe/page.tsx
│       └── admin/page.tsx
├── components/
│   ├── beds/
│   │   ├── RedRoomBed.tsx
│   │   ├── CounterSector.tsx
│   │   ├── SpecialistCard.tsx
│   │   └── ExamCard.tsx
│   ├── auth/
│   │   ├── PinPad.tsx
│   │   └── StaffPicker.tsx
│   └── shared/
│       ├── TopBar.tsx
│       ├── ToastViewport.tsx
│       └── OfflineBanner.tsx
├── lib/
│   ├── api.ts
│   ├── auth.ts
│   ├── ws.ts
│   ├── optimistic.ts
│   └── device.ts
├── hooks/
│   ├── useUnitState.ts
│   └── useShiftSession.ts
└── styles/
    └── tokens.css
```

## Estratégia de execução

Branch única, commit por fase. Subagents disparados em paralelo onde fases são independentes (Fase 2 e Fase 3 são paralelas após Fase 1). Frontend mockando tipos do backend em `lib/api.ts` pra não bloquear UI em endpoints.

## Débitos registrados
- Testes de frontend pulados nesta primeira leva.
- HTML do design ausente — visual será calibrado quando o arquivo aparecer.
- Fase 7 sem testes de frontend (ws/optimistic/offline-queue) — cobrir em leva futura com Vitest/Playwright. Faltam também ícones PWA 192/512 em `frontend/public/` e config nginx para servir o frontend atrás de `/`.
- nginx.conf de produção fica pra Fase 7.
