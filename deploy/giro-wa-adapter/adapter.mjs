// Adaptador whatsmeow-gateway -> parser do giro-de-leitos.
// Recebe o webhook do go-whatsapp-web-multidevice (127.0.0.1:3081/hook),
// filtra mensagens de GRUPO e repassa o texto ao ingest do parser.
// GIRO_GROUP_JID vazio = aceita todos os grupos (modo descoberta: loga os JIDs
// para travarmos o filtro no grupo certo depois).
//
// v2 — AUTORIA. Além do texto, o adapter agora extrai QUEM enviou a mensagem
// e repassa {sender_phone, sender_name} ao ingest. As UPAs postam o próprio
// giro pelo WhatsApp, então quem posta é o contato da unidade: é esse número
// que o alerta de silêncio menciona (campo `mentions` do gateway), no lugar de
// nomear um coordenador cadastrado que muitas vezes não é quem manda a
// mensagem.
//
// O formato exato do webhook NÃO está confirmado (o payload varia entre
// versões do gateway e entre mensagem de grupo/privada). Por isso a extração é
// DEFENSIVA: tenta os campos plausíveis em ordem, loga o que encontrou (e o
// que não encontrou) e, na falta de todos, segue enviando o giro sem autoria —
// perder o giro por causa do remetente seria trocar o essencial pelo acessório.
import http from "node:http";

const PORT = Number(process.env.ADAPTER_PORT || 3081);
const INGEST = process.env.GIRO_INGEST_URL || "http://127.0.0.1:8000/api/ingest/whatsapp-bridge";
const GROUP_JID = (process.env.GIRO_GROUP_JID || "").trim();

// Ordem de tentativa do JID/telefone do autor. `participant` e `sender_jid`
// são os campos que o whatsmeow usa para "quem falou dentro do grupo";
// `from`/`author` aparecem em versões e forks diferentes. `chat_id` NÃO entra
// nesta lista: em grupo ele é o JID do grupo (@g.us), não da pessoa.
const SENDER_JID_FIELDS = ["sender_jid", "sender", "participant", "from", "author"];
// Nome exibido (push name). Só enfeite: identifica o humano no log e no texto
// do alerta, mas quem faz a menção funcionar é o número.
// `from_name` e o campo REAL deste gateway (go-whatsapp-web-multidevice v9),
// confirmado no webhook de producao em 03/08/2026; os demais sao fallback.
const SENDER_NAME_FIELDS = ["from_name", "sender_display_name", "push_name", "pushname", "notify", "sender_name"];

const GROUP_SUFFIX = "@g.us";

// Todos os candidatos (campo, valor) na ordem de tentativa. Devolver a lista
// inteira — em vez do primeiro achado — deixa quem chama descartar um valor
// implausível (ex.: sender_jid preenchido com o JID do grupo) e seguir para o
// próximo campo, em vez de ficar sem autoria por causa do primeiro palpite.
function stringCandidates(sources, fields) {
  const found = [];
  for (const field of fields) {
    for (const source of sources) {
      const value = source?.[field];
      if (typeof value === "string" && value.trim()) {
        found.push({ field, value: value.trim() });
        continue;
      }
      // Alguns gateways aninham: {sender: {id, name}}.
      if (value && typeof value === "object") {
        const nested = value.id ?? value.jid ?? value.number ?? value.name;
        if (typeof nested === "string" && nested.trim()) {
          found.push({ field, value: nested.trim() });
        }
      }
    }
  }
  return found;
}

function firstString(sources, fields) {
  return stringCandidates(sources, fields)[0] || null;
}

// Fontes plausíveis do payload, da mais específica para a mais genérica.
export function payloadSources(p) {
  return [p?.payload, p, p?.message, p?.payload?.sender, p?.sender, p?.key, p?.payload?.key].filter(
    (source) => source && typeof source === "object",
  );
}

// Objetos que representam a PESSOA ({id, name}). Só aqui um campo chamado
// "name" pode ser o nome do remetente — solto no payload, "name" costuma ser
// nome de arquivo ou do grupo, e viraria um autor inventado.
function senderObjects(p) {
  return [p?.payload?.sender, p?.sender, p?.payload?.participant, p?.participant].filter(
    (source) => source && typeof source === "object",
  );
}

export function extractText(p) {
  const pl = p?.payload || p;
  return pl?.body || pl?.text || pl?.caption || p?.message?.text || null;
}

export function extractChat(p) {
  const pl = p?.payload || p;
  return pl?.chat_id || pl?.chat || pl?.sender_jid || pl?.from || p?.from || "";
}

// Só os dígitos do JID, antes do "@". Espelha services/whatsapp_alerts.py
// (normalize_phone) para que os dois lados gravem a MESMA grafia do telefone:
//   "5571988887777@s.whatsapp.net" -> "5571988887777"
//   "5571988887777:42@s.whatsapp.net" -> "5571988887777"
//   "+55 (71) 98888-7777" -> "5571988887777"
// Fora da faixa 8–15 dígitos não é telefone e vira "": abaixo é ruído do
// payload, acima é id de grupo antigo ("557181082189-1462561641", 22 dígitos)
// que perdeu o sufixo @g.us pelo caminho. 15 é o teto do E.164.
export function normalizePhone(value) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text) return "";
  const digits = text.split("@")[0].split(":")[0].replace(/\D+/g, "");
  return digits.length >= 8 && digits.length <= 15 ? digits : "";
}

// O telefone de dentro de um valor que pode ser JID puro, JID com sufixo de
// device, número formatado ou a forma composta que algumas versões usam:
// "5571988887777@s.whatsapp.net in 120363...@g.us". Pega o primeiro pedaço que
// seja uma PESSOA — JID de grupo (@g.us) nunca vira autoria, porque mencionar
// o grupo inteiro não é o mesmo que mencionar quem posta.
export function personalPhone(raw) {
  for (const token of String(raw ?? "").split(/[\s,;]+/)) {
    if (!token || token.includes(GROUP_SUFFIX)) continue;
    const digits = normalizePhone(token);
    if (digits) return digits;
  }
  return "";
}

// Extrai o autor de forma defensiva. Devolve sempre um objeto — com campos
// vazios quando o payload não traz remetente — e conta de onde cada pedaço
// veio, para o log dizer qual campo este gateway realmente usa.
export function extractSender(p) {
  const sources = payloadSources(p);
  const candidates = stringCandidates(sources, SENDER_JID_FIELDS);
  const nameHit =
    firstString(sources, SENDER_NAME_FIELDS) ||
    firstString(senderObjects(p), ["name", "display_name"]);

  let phone = "";
  let jidField = "";
  let rawJid = candidates[0]?.value || "";
  for (const candidate of candidates) {
    const digits = personalPhone(candidate.value);
    if (digits) {
      phone = digits;
      jidField = candidate.field;
      rawJid = candidate.value;
      break;
    }
  }

  return {
    phone,
    name: nameHit?.value || "",
    rawJid,
    jidField,
    nameField: nameHit?.field || "",
    // Só para o log: achamos remetente, mas era o grupo (ou lixo sem dígitos).
    rejectedGroupJid: !phone && candidates.some((c) => c.value.includes(GROUP_SUFFIX)),
  };
}

export function buildIngestBody(p, { source = "whatsmeow" } = {}) {
  const text = extractText(p);
  const sender = extractSender(p);
  const body = { text, source };
  // Campos ausentes NÃO são enviados como null: o ingest trata ausência e
  // vazio igual, e um corpo enxuto é mais fácil de ler no log do servidor.
  if (sender.phone) body.sender_phone = sender.phone;
  if (sender.name) body.sender_name = sender.name;
  return body;
}

export async function handlePayload(p, { fetchImpl = fetch, log = console.log } = {}) {
  const chat = String(extractChat(p));
  const text = extractText(p);
  const isGroup = chat.includes(GROUP_SUFFIX);
  if (process.env.DEBUG_RAW === "1" && isGroup) {
    log("[raw]", JSON.stringify(p).slice(0, 900));
  }
  const sender = extractSender(p);
  log(
    `[hook] chat=${chat} group=${isGroup} len=${text ? text.length : 0}` +
      ` sender=${sender.phone || "(sem autoria)"}` +
      ` via=${sender.jidField || "-"}` +
      ` nome=${sender.name || "-"}(${sender.nameField || "-"})` +
      (sender.rejectedGroupJid ? " [jid do grupo descartado]" : ""),
  );
  if (!isGroup || !text) return null;
  if (GROUP_JID && !chat.includes(GROUP_JID)) return null;

  const body = buildIngestBody(p);
  const r = await fetchImpl(INGEST, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  log(`[ingest] ${r.status} ${d.status || ""} ${d.message || ""}`);
  return { status: r.status, body };
}

export function createAdapterServer() {
  return http.createServer((req, res) => {
    if (req.method !== "POST") {
      res.writeHead(405);
      res.end();
      return;
    }
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      res.writeHead(200);
      res.end("ok");
      try {
        await handlePayload(JSON.parse(body));
      } catch (e) {
        console.error("[hook] erro:", e.message);
      }
    });
  });
}

// Só sobe o servidor quando executado direto — importar este arquivo (nos
// testes) não pode abrir porta nenhuma.
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  createAdapterServer().listen(PORT, "0.0.0.0", () =>
    console.log(`adapter ouvindo em 127.0.0.1:${PORT}`),
  );
}
