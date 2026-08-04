# Restrição de UPA no grupo do WhatsApp

Como a decisão da chefia de redirecionar a demanda de uma unidade chega ao
grupo onde as UPAs postam o giro — e por que ela chega diferente do jeito que
chega aos reguladores.

## O problema

A restrição nasce no painel `/tabela` (repo `tabela`): a chefia clica na célula
da UPA, escolhe o prazo, digita o PIN. Dali ela já ia para dois lugares — a
célula vermelha no painel e o grupo dos **reguladores** no Telegram (imediato +
lembrete de 2h).

Faltava o terceiro: a **própria unidade**. A UPA descobria que estava
redirecionada pelo telefone, depois de um paciente já ter sido recusado, ou não
descobria — e cobrava explicação de quem regula. O grupo do WhatsApp onde o giro
é postado já reúne exatamente as pessoas que precisam saber.

## O que muda para quem lê o grupo

Três avisos, todos em texto puro (negrito com `*asterisco*` — o WhatsApp não
entende HTML):

| quando | o que sai |
|---|---|
| a chefia restringe / muda o prazo | comunicado imediato daquela unidade |
| a cada **4h** (07, 11, 15, 19, 23, 03) | lembrete com as unidades vigentes |
| liberação ou prazo vencido | a unidade voltou a receber |

```
🚫 *UPA Barris — demanda redirecionada*

A pedido da Coordenação de Unidades Fixas, esta unidade não receberá
encaminhamentos até *hoje 19:00*.
As demandas serão redirecionadas para as demais unidades nesse período.

Giro de Leitos · aviso automático
```

Três decisões de tom e cadência, todas deliberadas:

- **4h, não 2h.** O grupo dos reguladores é ferramenta de trabalho de quem
  regula e aguenta a repetição. O grupo das UPAs é onde o giro é postado: um
  lembrete de 2 em 2 horas ali vira ruído, e grupo silenciado é a única falha
  irreversível deste canal. As janelas continuam ancoradas em 07:00/19:00 para
  cair na virada de turno.
- **Comunicado, não ordem.** O regulador lê "não encaminhe". A unidade precisa
  ler que a demanda dela está sendo redirecionada **a pedido da Coordenação de
  Unidades Fixas** — a decisão não é do SAMU nem do plantão, e o texto diz de
  quem é.
- **Silêncio quando não há nada.** Sem restrição vigente, nenhum lembrete. Um
  "nenhuma unidade restrita" seis vezes por dia ensinaria o grupo a ignorar o
  aviso justamente para o dia em que ele importa.

## Como funciona

```
chefia no /tabela (aba UPAs)  ──▶  upa_restrictions (banco do `tabela`)
                                          │
                        GET /tabela/api/upas/restrictions  (público)
                                          │
   ┌──────────────────────────────────────┼───────────────────────────┐
   │                                      │                           │
painel                        bot Plantões SAMU          giro-de-leitos (aqui)
(célula vermelha)             (chegada do regulador)     services/upa_restrictions_wa.py
                                                                      │
                                                          gateway whatsmeow
                                                          POST /send/message
                                                                      │
                                                          grupo das UPAs
```

A detecção de mudança é por **comparação de snapshot**, não por webhook: o
`tabela` não conhece este serviço e não deve passar a conhecer — ele é o dono do
dado, e aqui só se lê. A cada `WHATSAPP_RESTRICTION_POLL_MINUTES` (2 por padrão)
a lista vigente é comparada com a última que o grupo viu, e a diferença vira
aviso. O atraso máximo de uma mudança é um poll.

O envio reaproveita o gateway whatsmeow já em produção
(`services/whatsapp_alerts.send_gateway_message`): o campo `phone` do
`POST /send/message` aceita o JID do grupo (`120363XXXXXXXXX@g.us`) do mesmo
jeito que aceita um número.

## As quatro travas

1. **Nasce desligada.** Sem `WHATSAPP_RESTRICTION_ENABLED=true` e sem
   `WHATSAPP_GROUP_TO`, o módulo apenas loga o que enviaria. O canal liga quando
   o dono decidir, nunca por um deploy.
2. **API fora do ar nunca vira "liberou geral".** `fetch_restrictions` devolve
   `None` (não `[]`) em qualquer falha de leitura, e o ciclo inteiro é pulado.
   Se um timeout virasse lista vazia, uma instabilidade de rede anunciaria ao
   grupo que todas as unidades voltaram a receber — a mensagem errada, no canal
   errado, sem como desfazer.
3. **Primeira execução semeia calado.** Sem snapshot gravado, o que já estava
   restrito não é anunciado como novo: a lista é gravada e passa a valer dali.
   Sem isso, todo restart repetiria as restrições vigentes.
4. **O snapshot só avança para o que foi entregue**, item por item. Aviso que o
   gateway recusou continua pendente e é retentado na varredura seguinte, em vez
   de virar silêncio.

A idempotência do lembrete é uma linha por janela em `whatsapp_restriction_state`
(`digest:<data>T<hora>`), reservada com `INSERT ... ON CONFLICT DO NOTHING`.
Envio que falha **devolve** a janela, para tentar de novo dentro da tolerância de
15 minutos.

## Modelo de dados

Tabela `whatsapp_restriction_state` (migration `011`, idempotente):

| coluna | papel |
|---|---|
| `state_key` | `snapshot` ou `digest:<data>T<hora>` |
| `payload` | o snapshot (id → unidade/prazo); vazio nas linhas de janela |
| `updated_at` | quando foi gravado |

A distinção entre snapshot **ausente** e snapshot **vazio** é o que separa
"instalação nova" de "não há nada restrito" — ver trava 3.

## Variáveis de ambiente

| variável | default | efeito |
|---|---|---|
| `WHATSAPP_RESTRICTION_ENABLED` | `false` | `true` liga o canal |
| `WHATSAPP_GROUP_TO` | vazio | JID do grupo das UPAs; vazio = dry-run |
| `TABELA_RESTRICTIONS_URL` | `https://mnrs.com.br/tabela/api/upas/restrictions` | fonte; aponte ao LAB para testar |
| `WHATSAPP_RESTRICTION_DIGEST_HOURS` | `4` | espaçamento do lembrete, ancorado em 07:00/19:00 |
| `WHATSAPP_RESTRICTION_POLL_MINUTES` | `2` | teto do atraso de uma mudança |
| `WHATSAPP_GW_URL` / `WHATSAPP_GW_AUTH` | — | os mesmos do alerta de silêncio |

Para descobrir o JID do grupo, suba o adapter com `GIRO_GROUP_JID` vazio (modo
descoberta) e leia o log — ver `deploy/giro-wa-adapter/README.md`.

## Ligando com segurança

```bash
# 1. Confira o que sairia, sem enviar nada (o canal desligado loga a mensagem)
docker compose logs -f api | grep dry-run

# 2. Aponte ao LAB antes do LIVE, se quiser exercitar o fluxo inteiro
TABELA_RESTRICTIONS_URL=http://127.0.0.1:4000/tabela/api/upas/restrictions

# 3. Ligue
WHATSAPP_RESTRICTION_ENABLED=true
WHATSAPP_GROUP_TO=120363XXXXXXXXX@g.us
```

Na primeira subida com o canal ligado o grupo **não** recebe nada: o snapshot é
semeado em silêncio (trava 3). O primeiro aviso real é a próxima mudança, ou o
lembrete da próxima janela de 4h.

Com o canal **desligado**, o ciclo é uma simulação fiel: o snapshot avança e a
janela do lembrete é reservada como se o envio tivesse acontecido. Cada mudança
aparece no log **uma vez**, não a cada varredura — e ligar o canal meses depois
não despeja no grupo a fila inteira de mudanças velhas. Só falha de gateway com
o canal **ligado** mantém o aviso pendente para retentativa.

## Cobrança de giro parado — canal vizinho, não o mesmo

`services/whatsapp_alerts.py` cobra quem está tempo demais sem postar o giro
(SLA do `/cobranca`: diurno acima de 6h, noturno acima de 12h, cooldown de 6h
por unidade). Historicamente esse aviso ia para **um destino só**, o WhatsApp do
gestor (`WHATSAPP_ALERT_TO`) — o grupo nunca foi cobrado.

`WHATSAPP_ALERT_TO_GROUP=true` passa a mandar a **mesma** cobrança também para
`WHATSAPP_GROUP_TO`. Continua desligado por padrão: a mensagem cita nominalmente
quem posta o giro de cada unidade e o coordenador, e levá-la do gestor para o
grupo muda quem lê isso — decisão do dono do canal, não de um deploy.

Com os dois destinos ligados, uma entrega parcial (gestor sim, grupo não) grava
o cooldown assim mesmo: repetir a varredura para recuperar o grupo mandaria a
mesma cobrança duas vezes ao gestor, e duplicar aviso é pior do que perder um.
