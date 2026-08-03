// Testes do adapter (node --test, sem dependência externa e sem rede).
//
// O que está sob teste é a única coisa que o adapter faz de arriscado:
// adivinhar QUAL campo do webhook carrega o remetente. O formato do
// go-whatsapp-web-multidevice não está confirmado e muda entre versões, então
// cada formato plausível vira um caso aqui — inclusive o caso "não veio nada",
// que precisa degradar para "giro sem autoria" e nunca para exceção.
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildIngestBody,
  extractSender,
  extractText,
  handlePayload,
  normalizePhone,
  personalPhone,
} from "./adapter.mjs";

const GROUP = "120363123456789012@g.us";
const PERSON = "5571988887777@s.whatsapp.net";

test("extrai o remetente de payload.sender_jid", () => {
  const sender = extractSender({ payload: { chat_id: GROUP, sender_jid: PERSON, body: "giro" } });
  assert.equal(sender.phone, "5571988887777");
  assert.equal(sender.jidField, "sender_jid");
});

test("extrai de participant quando sender_jid é o JID do grupo", () => {
  const sender = extractSender({
    payload: { chat_id: GROUP, sender_jid: GROUP, participant: PERSON, body: "giro" },
  });
  assert.equal(sender.phone, "5571988887777");
  assert.equal(sender.jidField, "participant");
});

test("extrai de from no formato composto '<pessoa> in <grupo>'", () => {
  const sender = extractSender({ from: `${PERSON} in ${GROUP}`, message: { text: "giro" } });
  assert.equal(sender.phone, "5571988887777");
  assert.equal(sender.jidField, "from");
});

test("extrai de author", () => {
  const sender = extractSender({ payload: { author: "557191234567@s.whatsapp.net" } });
  assert.equal(sender.phone, "557191234567");
  assert.equal(sender.jidField, "author");
});

test("extrai de sender aninhado {id, name}", () => {
  const sender = extractSender({ sender: { id: PERSON, name: "Maria Enf" } });
  assert.equal(sender.phone, "5571988887777");
  assert.equal(sender.name, "Maria Enf");
});

test("extrai o JID com sufixo de device", () => {
  const sender = extractSender({ payload: { sender_jid: "5571988887777:42@s.whatsapp.net" } });
  assert.equal(sender.phone, "5571988887777");
});

test("nome do remetente sai de sender_display_name, push_name, pushname ou notify", () => {
  const campos = ["sender_display_name", "push_name", "pushname", "notify"];
  for (const campo of campos) {
    const sender = extractSender({ payload: { sender_jid: PERSON, [campo]: "Maria Enf" } });
    assert.equal(sender.name, "Maria Enf", `falhou no campo ${campo}`);
    assert.equal(sender.nameField, campo);
  }
});

test("payload sem remetente não quebra e devolve autoria vazia", () => {
  for (const p of [{}, { payload: {} }, { payload: { body: "giro" } }, null, undefined]) {
    const sender = extractSender(p);
    assert.equal(sender.phone, "");
    assert.equal(sender.name, "");
    assert.equal(sender.jidField, "");
  }
});

test("JID de grupo sozinho nunca vira autoria", () => {
  const sender = extractSender({ payload: { chat_id: GROUP, from: GROUP, body: "giro" } });
  assert.equal(sender.phone, "");
  assert.equal(sender.rejectedGroupJid, true);
});

test("valor implausível (curto demais para telefone) é descartado", () => {
  assert.equal(extractSender({ payload: { sender_jid: "123" } }).phone, "");
  assert.equal(extractSender({ payload: { sender_jid: "bot" } }).phone, "");
});

test("id de grupo antigo sem sufixo @g.us não vira telefone", () => {
  // O grupo real de produção é 557181082189-1462561641@g.us: 22 dígitos.
  assert.equal(normalizePhone("557181082189-1462561641@g.us"), "");
  assert.equal(normalizePhone("557181082189-1462561641"), "");
  assert.equal(extractSender({ payload: { sender_jid: "557181082189-1462561641" } }).phone, "");
});

test("normalizePhone aceita as grafias que o WhatsApp entrega", () => {
  assert.equal(normalizePhone("5571988887777@s.whatsapp.net"), "5571988887777");
  assert.equal(normalizePhone("5571988887777:12@s.whatsapp.net"), "5571988887777");
  assert.equal(normalizePhone("+55 (71) 98888-7777"), "5571988887777");
  assert.equal(normalizePhone("5571988887777@lid"), "5571988887777");
  assert.equal(normalizePhone(""), "");
  assert.equal(normalizePhone(null), "");
  assert.equal(normalizePhone(undefined), "");
  assert.equal(normalizePhone("abc"), "");
});

test("personalPhone ignora o pedaço de grupo do valor composto", () => {
  assert.equal(personalPhone(`${GROUP} ${PERSON}`), "5571988887777");
  assert.equal(personalPhone(GROUP), "");
});

test("corpo do ingest carrega texto, origem e autoria quando existe", () => {
  const body = buildIngestBody({
    payload: { chat_id: GROUP, sender_jid: PERSON, push_name: "Maria Enf", body: "GIRO UPA BARRIS" },
  });
  assert.deepEqual(body, {
    text: "GIRO UPA BARRIS",
    source: "whatsmeow",
    sender_phone: "5571988887777",
    sender_name: "Maria Enf",
  });
});

test("corpo do ingest omite autoria ausente em vez de mandar null", () => {
  const body = buildIngestBody({ payload: { chat_id: GROUP, body: "GIRO UPA BARRIS" } });
  assert.deepEqual(body, { text: "GIRO UPA BARRIS", source: "whatsmeow" });
  assert.equal("sender_phone" in body, false);
  assert.equal("sender_name" in body, false);
});

test("extractText continua lendo body, text, caption e message.text", () => {
  assert.equal(extractText({ payload: { body: "a" } }), "a");
  assert.equal(extractText({ payload: { text: "b" } }), "b");
  assert.equal(extractText({ payload: { caption: "c" } }), "c");
  assert.equal(extractText({ message: { text: "d" } }), "d");
  assert.equal(extractText({}), null);
});

test("mensagem de grupo é repassada ao ingest com a autoria", async () => {
  const chamadas = [];
  const fetchImpl = async (url, opts) => {
    chamadas.push({ url, body: JSON.parse(opts.body) });
    return { status: 200, json: async () => ({ status: "accepted" }) };
  };

  await handlePayload(
    { payload: { chat_id: GROUP, sender_jid: PERSON, push_name: "Maria", body: "GIRO" } },
    { fetchImpl, log: () => {} },
  );

  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].body.sender_phone, "5571988887777");
  assert.equal(chamadas[0].body.sender_name, "Maria");
});

test("mensagem privada (não é grupo) não vai para o ingest", async () => {
  let chamou = false;
  const fetchImpl = async () => {
    chamou = true;
    return { status: 200, json: async () => ({}) };
  };

  await handlePayload({ payload: { chat_id: PERSON, sender_jid: PERSON, body: "oi" } }, { fetchImpl, log: () => {} });

  assert.equal(chamou, false);
});

test("giro sem autoria continua sendo enviado", async () => {
  const chamadas = [];
  const fetchImpl = async (url, opts) => {
    chamadas.push(JSON.parse(opts.body));
    return { status: 200, json: async () => ({ status: "accepted" }) };
  };

  await handlePayload({ payload: { chat_id: GROUP, body: "GIRO" } }, { fetchImpl, log: () => {} });

  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].text, "GIRO");
  assert.equal(chamadas[0].sender_phone, undefined);
});
