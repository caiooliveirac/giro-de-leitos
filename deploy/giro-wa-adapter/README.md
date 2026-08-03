# giro-wa-adapter

Ponte entre o gateway whatsmeow (`aldinokemal2104/go-whatsapp-web-multidevice`)
e o ingest do parser. Roda **no host**, como serviço systemd, fora do
docker-compose da aplicação:

```
whatsmeow-gw --webhook--> :3081 (este adapter) --POST--> :8000/api/ingest/whatsapp-bridge
```

Este diretório é a **fonte de verdade** do adapter. A cópia que roda em
produção fica em `~/giro-wa-adapter/adapter.mjs` e é atualizada a partir daqui
(passo a passo abaixo) — nunca o contrário.

## O que a v2 mudou

Além do texto, o adapter passa a extrair **quem enviou** a mensagem e mandar
`sender_phone` (só dígitos, antes do `@`) e `sender_name` ao ingest. É isso que
alimenta `unit_contacts` e permite ao alerta de silêncio **mencionar quem posta
o giro** da UPA, em vez de nomear um coordenador cadastrado que muitas vezes
não é quem manda a mensagem no grupo.

O formato do webhook **não está confirmado** e varia entre versões do gateway,
então a extração é defensiva: tenta `sender_jid`, `sender`, `participant`,
`from`, `author` (nessa ordem, no payload e nos objetos aninhados), descarta
JID de grupo (`@g.us`), e o nome sai de `sender_display_name`, `push_name`,
`pushname`, `notify` ou `sender.name`. Nada disso é obrigatório: **sem autoria
o giro é enviado do mesmo jeito**, e o log registra qual campo funcionou:

```
[hook] chat=1203...@g.us group=true len=214 sender=5571988887777 via=participant nome=Maria(push_name)
```

Confirmado o campo real em produção, dá para encurtar a lista — mas só depois
de ver o log, não antes.

## Variáveis

| Variável | Default | Observação |
|---|---|---|
| `ADAPTER_PORT` | `3081` | Porta do webhook |
| `GIRO_INGEST_URL` | `http://127.0.0.1:8000/api/ingest/whatsapp-bridge` | Destino do giro |
| `GIRO_GROUP_JID` | vazio | Vazio = aceita todos os grupos (modo descoberta) |
| `DEBUG_RAW` | vazio | `1` loga o payload cru (900 chars) das mensagens de grupo |

## Testes

Sem dependências e sem rede:

```bash
docker run --rm --network none \
  -v "$PWD/deploy/giro-wa-adapter":/app -w /app node:20-alpine node --test
```

## Instalar em produção (host, fora do compose)

```bash
# 1. backup da versão em uso
cp ~/giro-wa-adapter/adapter.mjs ~/giro-wa-adapter/adapter.mjs.bak-$(date +%Y%m%d-%H%M)

# 2. copiar a versão do repo
cp ~/giro-de-leitos/deploy/giro-wa-adapter/adapter.mjs ~/giro-wa-adapter/adapter.mjs

# 3. reiniciar e conferir o log (a linha [hook] agora traz sender=/via=)
sudo systemctl restart giro-wa-adapter
journalctl -u giro-wa-adapter -f
```

Rollback: `cp ~/giro-wa-adapter/adapter.mjs.bak-<data> ~/giro-wa-adapter/adapter.mjs`
e reiniciar. A API aceita o adapter antigo (sem autoria) sem nenhuma mudança —
os campos novos são opcionais nos dois lados.
