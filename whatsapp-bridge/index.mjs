/**
 * Giro de Leitos – WhatsApp Bridge
 *
 * Conecta-se ao WhatsApp via Baileys (multi-device), escuta mensagens de um
 * ou mais grupos configurados e encaminha o texto para a API FastAPI existente.
 *
 * Variáveis de ambiente:
 *   WHATSAPP_GROUP_IDS   – IDs dos grupos separados por vírgula (ex: "120363XXXX@g.us,120363YYYY@g.us").
 *                          Se vazio, aceita TODOS os grupos (cuidado!).
 *   API_BASE_URL         – URL da API FastAPI (default: http://parser-api:8000)
 *   API_INGEST_PATH      – Caminho do endpoint de ingestão (default: /api/ingest/manual)
 *   AUTH_DIR             – Diretório para salvar credenciais (default: ./auth_info)
 *   LOG_LEVEL            – Nível de log do Pino (default: warn)
 *   LIST_GROUPS          – Se "true", ao conectar lista todos os grupos e encerra (para descobrir IDs)
 *   DRY_RUN              – Se "true", imprime as mensagens sem enviar para a API
 */

import {
    makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
    Browsers,
} from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode-terminal";
import fs from "fs";

// ── Humanização (anti-detecção) ─────────────────────────────────────────

/** Retorna um inteiro aleatório entre min e max (inclusive). */
function randInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

/** Delay aleatório para simular comportamento humano. */
function humanDelay(minMs = 1500, maxMs = 4000) {
    return new Promise((r) => setTimeout(r, randInt(minMs, maxMs)));
}

/** Simula "digitando..." antes de enviar uma mensagem no grupo. */
async function simulateTyping(sock, jid, durationMs = undefined) {
    try {
        await sock.sendPresenceUpdate("composing", jid);
        await new Promise((r) => setTimeout(r, durationMs ?? randInt(2000, 5000)));
        await sock.sendPresenceUpdate("paused", jid);
    } catch { /* presença é best-effort */ }
}

/** Escolhe aleatoriamente um item de um array. */
function pickRandom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

// ── Config ──────────────────────────────────────────────────────────────────
const WHATSAPP_GROUP_IDS = (process.env.WHATSAPP_GROUP_IDS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

const API_BASE_URL = (
    process.env.API_BASE_URL || "http://parser-api:8000"
).replace(/\/+$/, "");
const API_INGEST_PATH =
    process.env.API_INGEST_PATH || "/api/ingest/whatsapp-bridge";
const AUTH_DIR = process.env.AUTH_DIR || "./auth_info";
const LOG_LEVEL = process.env.LOG_LEVEL || "warn";
const LIST_GROUPS = process.env.LIST_GROUPS === "true";
const DRY_RUN = process.env.DRY_RUN === "true";

// ── Config de alerta de UPAs inativas ───────────────────────────────────
const STALE_CHECK_INTERVAL_MIN = parseInt(process.env.STALE_CHECK_INTERVAL_MIN || "120", 10);
const STALE_THRESHOLD_HOURS = parseFloat(process.env.STALE_THRESHOLD_HOURS || "6");
const STALE_ALERTS_ENABLED = process.env.STALE_ALERTS_ENABLED !== "false"; // ativado por padrão

// ── Config de notificação Telegram (queda de conexão) ───────────────────
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const TELEGRAM_ADMIN_CHAT_ID = process.env.TELEGRAM_ADMIN_CHAT_ID || "";

const logger = pino({ level: LOG_LEVEL });

// ── Notificação Telegram ────────────────────────────────────────────────
async function notifyTelegram(message) {
    if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_ADMIN_CHAT_ID) return;
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
    try {
        await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                chat_id: TELEGRAM_ADMIN_CHAT_ID,
                text: message,
                parse_mode: "HTML",
            }),
        });
        console.log(`📨 Telegram notificado: ${message.substring(0, 60)}...`);
    } catch (err) {
        console.error(`❌ Falha ao notificar Telegram: ${err.message}`);
    }
}

// ── Formatar mensagem Telegram com dados brutos + digeridos ─────────────
function fmtRoom(room) {
    return room ? room.ratio : "—";
}

function buildGiroTelegramMsg(emoji, upaLabel, statusText, rawText, apiResult) {
    const d = apiResult?.event?.data;
    const rooms = d?.rooms || {};
    const sp = d?.specialists || {};
    const corridor = d?.corridor_patients || [];
    const otherBeds = rooms?.other_beds || [];
    const warnings = d?.warnings || [];

    // ── Dados digeridos ──
    const lines = [
        `${emoji} <b>Giro — ${upaLabel}</b>`,
        `Status: ${statusText}`,
        "",
    ];

    if (d) {
        lines.push("<b>📊 Dados digeridos:</b>");
        lines.push(`🔴 Vermelha: ${fmtRoom(rooms.red_room)}`);

        // Amarela: mostrar split masc/fem se disponível
        if (rooms.yellow_male || rooms.yellow_female) {
            lines.push(`🟡 Amarela Masc: ${fmtRoom(rooms.yellow_male)}`);
            lines.push(`🟡 Amarela Fem: ${fmtRoom(rooms.yellow_female)}`);
        }
        if (rooms.yellow_room) {
            lines.push(`🟡 Amarela: ${fmtRoom(rooms.yellow_room)}`);
        }

        // Isolamento
        if (rooms.isolation_mode === "split") {
            if (rooms.isolation_male) lines.push(`🟣 Iso Masc: ${fmtRoom(rooms.isolation_male)}`);
            if (rooms.isolation_female) lines.push(`🟣 Iso Fem: ${fmtRoom(rooms.isolation_female)}`);
            if (rooms.isolation_pediatric) lines.push(`🟣 Iso Ped: ${fmtRoom(rooms.isolation_pediatric)}`);
        } else if (rooms.isolation_total) {
            lines.push(`🟣 Isolamento: ${fmtRoom(rooms.isolation_total)}`);
        }

        // Outros leitos
        for (const bed of otherBeds) {
            lines.push(`🛏️ ${bed.label}: ${bed.ratio}`);
        }

        // Especialistas
        lines.push(`🦴 Ortop: ${sp.has_orthopedist ? "✅" : "❌"}  🔪 Cirurg: ${sp.has_surgeon ? "✅" : "❌"}  🧠 Psiq: ${sp.has_psychiatrist ? "✅" : "❌"}`);

        // Corredor
        if (corridor.length > 0) {
            lines.push(`🚶 Corredor: ${corridor.length} paciente(s)`);
        }

        // Avisos
        if (warnings.length > 0) {
            lines.push("");
            lines.push("⚠️ " + warnings.join(" | "));
        }
    }

    // ── Texto bruto (blockquote) ──
    // Telegram limita mensagem a 4096 chars — reservar espaço para o resumo
    const header = lines.join("\n");
    const maxRaw = 4096 - header.length - 60; // margem para tags
    const clipped = rawText.length > maxRaw
        ? rawText.substring(0, maxRaw) + "…"
        : rawText;
    lines.push("");
    lines.push("<b>📝 Texto bruto:</b>");
    lines.push(`<blockquote>${clipped}</blockquote>`);

    return lines.join("\n");
}

// Controle para não spammar Telegram com reconexões rápidas
let lastDisconnectNotify = 0;
let pairingCodeRequested = false;
let disconnectNotifyTimer = null; // timer para notificação adiada de queda
let currentSock = null; // referência ao socket ativo (evita handlers duplicados)

// ── Mapeamento de telefone → UPA ─────────────────────────────────────────
// Formato: número sem "+" e sem espaços → nome canônico da UPA
// O JID do WhatsApp é "5571XXXXXXXX@s.whatsapp.net"
// Para adicionar: descubra o telefone do responsável e coloque aqui.
const PHONE_TO_UNIT = {
    "5571999613394": "PA SÃO MARCOS",
    "5571981469133": "UPA SANTO ANTÔNIO",
    "5571991453128": "12º CENTRO MARBACK - ALFREDO BUREAU",
    "5571997113738": "16º CENTRO MARIA CONCEIÇÃO SANTIAGO IMBASSAHY",
    "5571996937126": "PA PERNAMBUÉS",
    "5571985498451": "PA TANCREDO NEVES - RODRIGO ARGOLO",
    "5571987911206": "UPA BAIRRO DA PAZ - ORLANDO IMBASSAHY",
    "5571996047687": "UPA BARRIS",
    "5571999790543": "UPA DE BROTAS",
    "5571988009102": "UPA HELIO MACHADO",
    "5571997004240": "UPA PARIPE",
    "5571981121760": "UPA PARQUE SÃO CRISTOVÃO",
    "5571996049042": "UPA PERIPERI",
    "5571993810074": "UPA PIRAJA SANTO INÁCIO",
    "5571996933858": "UPA SAN MARTIN",
    "5571996805570": "UPA VALÉRIA",
};

// ── Mapeamento reverso: unit_code → telefone(s) para @mention ────────────
// Usado pelo alerta de UPAs inativas para marcar o responsável
// Mapeamento explícito: nome no PHONE_TO_UNIT → unit_code no banco
const NAME_TO_UNIT_CODE = {
    "PA SÃO MARCOS": "pa_sao_marcos",
    "UPA SANTO ANTÔNIO": "upa_santo_antonio",
    "12º CENTRO MARBACK - ALFREDO BUREAU": "centro_marback_alfredo_bureau",
    "16º CENTRO MARIA CONCEIÇÃO SANTIAGO IMBASSAHY": "centro_maria_conceicao_santiago_imbassahy",
    "PA PERNAMBUÉS": "pa_pernambues",
    "PA TANCREDO NEVES - RODRIGO ARGOLO": "pa_tancredo_neves_rodrigo_argolo",
    "UPA BAIRRO DA PAZ - ORLANDO IMBASSAHY": "upa_bairro_da_paz_orlando_imbassahy",
    "UPA BARRIS": "upa_barris",
    "UPA DE BROTAS": "upa_brotas",
    "UPA HELIO MACHADO": "upa_helio_machado",
    "UPA PARIPE": "upa_paripe",
    "UPA PARQUE SÃO CRISTOVÃO": "upa_parque_sao_cristovao",
    "UPA PERIPERI": "upa_periperi",
    "UPA PIRAJA SANTO INÁCIO": "upa_piraja_santo_inacio",
    "UPA SAN MARTIN": "upa_san_martin",
    "UPA VALÉRIA": "upa_valeria",
};

const UNIT_TO_PHONES = {};
for (const [phone, unitName] of Object.entries(PHONE_TO_UNIT)) {
    const code = NAME_TO_UNIT_CODE[unitName];
    if (!code) {
        console.warn(`⚠️  Sem unit_code mapeado para "${unitName}" — verifique NAME_TO_UNIT_CODE`);
        continue;
    }
    if (!UNIT_TO_PHONES[code]) UNIT_TO_PHONES[code] = [];
    UNIT_TO_PHONES[code].push(phone);
}

// ── Cache de JIDs resolvidos para mentions ──────────────────────────────
// phone number → JID real verificado pelo WhatsApp (pode ser @s.whatsapp.net ou @lid)
const resolvedJids = {};

/**
 * Resolve os números de telefone cadastrados para JIDs verificados pelo WhatsApp.
 * Também consulta metadata do grupo para encontrar participantes.
 * Deve ser chamada após a conexão estar aberta.
 */
async function resolvePhoneJids(sock) {
    console.log("🔍 Resolvendo JIDs dos telefones cadastrados...");

    // 1. Resolver via onWhatsApp (retorna o JID oficial)
    for (const phone of Object.keys(PHONE_TO_UNIT)) {
        try {
            const results = await sock.onWhatsApp(phone);
            if (results && results.length > 0 && results[0].exists) {
                resolvedJids[phone] = results[0].jid;
                console.log(`   ✓ ${phone} → ${results[0].jid}`);
            } else {
                console.log(`   ✗ ${phone} não encontrado no WhatsApp`);
            }
        } catch (err) {
            console.error(`   ✗ Erro ao verificar ${phone}: ${err.message}`);
        }
    }

    // 2. Complementar com metadata dos grupos (participantes reais)
    for (const groupJid of WHATSAPP_GROUP_IDS) {
        try {
            const meta = await sock.groupMetadata(groupJid);
            console.log(
                `   📋 Grupo "${meta.subject}": ${meta.participants.length} participante(s)`
            );
            for (const p of meta.participants) {
                // p.id pode ser "557199613394@s.whatsapp.net" (sem nono dígito) ou um LID
                const pPhone = p.id.replace(/@.*$/, "");
                const pNormalized = normalizePhoneBR(pPhone);
                if (PHONE_TO_UNIT[pNormalized] && !resolvedJids[pNormalized]) {
                    resolvedJids[pNormalized] = p.id;
                    console.log(`   ✓ ${pNormalized} → ${p.id} (via grupo)`);
                }
            }
        } catch (err) {
            console.error(
                `   ⚠️  Erro ao buscar metadata do grupo ${groupJid}: ${err.message}`
            );
        }
    }

    const resolved = Object.keys(resolvedJids).length;
    const total = Object.keys(PHONE_TO_UNIT).length;
    console.log(`   Resolvidos: ${resolved}/${total} telefone(s)\n`);
}

/**
 * Retorna o JID verificado para um telefone, com fallback para o formato padrão.
 */
function getVerifiedJid(phone) {
    return resolvedJids[phone] || `${phone}@s.whatsapp.net`;
}

/**
 * Extrai o número de telefone de um JID do WhatsApp.
 * Ex: "5571999613394@s.whatsapp.net" → "5571999613394"
 */
function phoneFromJid(jid) {
    if (!jid) return null;
    return jid.replace(/@.*$/, "");
}

/**
 * Normaliza um número BR para o formato usado no mapa PHONE_TO_UNIT.
 * O WhatsApp pode remover o nono dígito (9) dos celulares brasileiros no JID.
 * Ex: JID "557199613394" → mapa "5571999613394"
 *     JID "5571999613394" → mapa "5571999613394" (já normalizado)
 */
function normalizePhoneBR(phone) {
    if (!phone) return null;
    // Busca direta primeiro
    if (PHONE_TO_UNIT[phone]) return phone;
    // Celulares BR: 55 + DDD(2) + 9 + número(8) = 13 dígitos
    // Se veio com 12 dígitos (sem o 9), inserir o 9 após o DDD
    if (/^55\d{10}$/.test(phone)) {
        const withNine = phone.slice(0, 4) + "9" + phone.slice(4);
        if (PHONE_TO_UNIT[withNine]) return withNine;
    }
    return phone;
}

/**
 * Verifica se existe mapeamento telefone→UPA para o remetente.
 * Retorna o nome canônico da UPA ou null.
 */
function unitFromPhone(participantJid) {
    const phone = phoneFromJid(participantJid);
    if (!phone) return null;
    const normalized = normalizePhoneBR(phone);
    return PHONE_TO_UNIT[normalized] || null;
}

// Track forwarded message IDs to avoid duplicates on reconnect
const forwardedIds = new Set();
const MAX_FORWARDED_IDS = 5000;

// ── Helpers ─────────────────────────────────────────────────────────────────

function trimForwardedIds() {
    if (forwardedIds.size > MAX_FORWARDED_IDS) {
        const arr = [...forwardedIds];
        const toDelete = arr.slice(0, arr.length - MAX_FORWARDED_IDS / 2);
        for (const id of toDelete) forwardedIds.delete(id);
    }
}

async function forwardToApi(text, source, unitHint = null, senderPhone = null) {
    const url = `${API_BASE_URL}${API_INGEST_PATH}`;
    const payload = {
        text,
        source: source || "whatsapp-bridge",
    };
    if (unitHint) {
        payload.unit_hint = unitHint;
    }
    if (senderPhone) {
        payload.sender_phone = senderPhone;
    }
    const body = JSON.stringify(payload);

    if (DRY_RUN) {
        console.log("\n┌─ DRY RUN ──────────────────────────────────────");
        console.log(`│ Fonte: ${source}`);
        if (unitHint) console.log(`│ Remetente → UPA: ${unitHint}`);
        if (senderPhone) console.log(`│ Telefone: ${senderPhone}`);
        console.log(`│ Texto (${text.length} chars):`);
        text.split("\n").forEach((l) => console.log(`│   ${l}`));
        console.log("└────────────────────────────────────────────────\n");
        return null;
    }

    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });
        const data = await res.json();
        if (res.ok) {
            const unit =
                data?.event?.data?.upa_name || data?.event?.data?.unit_code || "?";
            const status = data?.status || "?";
            console.log(
                `✅ API respondeu: status=${status} unidade=${unit} | fonte=${source}`
            );
            return data;
        } else {
            console.error(
                `⚠️  API retornou ${res.status}: ${JSON.stringify(data)}`
            );
            return null;
        }
    } catch (err) {
        console.error(`❌ Erro ao encaminhar para API: ${err.message}`);
        return null;
    }
}

function isTargetGroup(jid) {
    if (!jid?.endsWith("@g.us")) return false;
    if (WHATSAPP_GROUP_IDS.length === 0) return true; // aceita todos
    return WHATSAPP_GROUP_IDS.includes(jid);
}

// ── Filtro de conteúdo (aceita apenas mensagens de giro de leitos) ──────────

// Palavras-chave obrigatórias de salas – ao menos UMA deve estar presente
const ROOM_KEYWORDS = [
    /sala\s+vermelha/i,
    /sala\s+amarela/i,
    /vermelha\s*[:\-]?\s*\(?\s*\d{1,2}\s*\/\s*\d{1,2}/i,
    /amarela\s*[:\-]?\s*\(?\s*\d{1,2}\s*\/\s*\d{1,2}/i,
    /isolamento/i,
    /leitos?\s+de\s+observa/i,
    /obs\s+masc/i,
    /obs\s+fem/i,
];

// Padrão de ocupação típico: "03/04", "3/8", "(2/5)"
const RATIO_PATTERN = /\(?\s*\d{1,2}\s*\/\s*\d{1,2}\s*\)?/;

// Sinais fortes adicionais que reforçam ser um giro
const STRONG_SIGNALS = [
    /\bUPA\b/i,
    /\bunidade\b/i,
    /\bcorredor\b/i,
    /ortoped(?:ia|ista)/i,
    /cirurgi[ãa]/i,
    /psiquiatr/i,
    /sem\s+sala\s+amarela/i,
    /n[ãa]o\s+disp[õo]e/i,
    /giro\s+de\s+leitos/i,
    /hor[áa]rio\s*[:\-]/i,
    /\b\d{1,2}\s*[:h]\s*\d{2}\b/,
];

/**
 * Verifica se o texto parece ser uma mensagem de giro de leitos.
 *
 * Critérios (todos devem passar):
 *  1. Mínimo de 100 caracteres
 *  2. Ao menos 1 keyword de sala (vermelha, amarela, isolamento, etc.)
 *  3. Ao menos 1 padrão de ocupação no formato X/Y
 *  4. Pelo menos 2 sinais fortes adicionais (UPA, corredor, ortopedista, etc.)
 *
 * Retorna { pass: boolean, reason: string }
 */
function looksLikeGiro(text) {
    // 1. Tamanho mínimo
    if (text.length < 100) {
        return { pass: false, reason: `Muito curta (${text.length} chars < 100)` };
    }

    // 2. Pelo menos uma keyword de sala
    const roomHits = ROOM_KEYWORDS.filter((rx) => rx.test(text));
    if (roomHits.length === 0) {
        return {
            pass: false,
            reason: "Nenhuma menção a sala (vermelha/amarela/isolamento/obs)",
        };
    }

    // 3. Pelo menos um padrão de ocupação X/Y
    const ratioMatches = text.match(new RegExp(RATIO_PATTERN.source, "g"));
    if (!ratioMatches || ratioMatches.length === 0) {
        return { pass: false, reason: "Nenhum padrão de ocupação (X/Y)" };
    }

    // 4. Pelo menos 2 sinais fortes adicionais
    const strongHits = STRONG_SIGNALS.filter((rx) => rx.test(text));
    if (strongHits.length < 2) {
        return {
            pass: false,
            reason: `Poucos sinais de giro (${strongHits.length}/2 mínimo)`,
        };
    }

    return {
        pass: true,
        reason: `OK: ${roomHits.length} sala(s), ${ratioMatches.length} razão(ões), ${strongHits.length} sinal(is)`,
    };
}

// ── Alerta de UPAs inativas ─────────────────────────────────────────────

let staleAlertInterval = null;

/**
 * Retorna o threshold dinâmico:
 *  - Entre 22h e 06h (horário de Salvador): 12h
 *  - Fora desse horário: STALE_THRESHOLD_HOURS (padrão 6h)
 */
function getCurrentThreshold() {
    const now = new Date();
    // Salvador = UTC-3
    const brtHour = (now.getUTCHours() - 3 + 24) % 24;
    if (brtHour >= 22 || brtHour < 6) {
        return 12;
    }
    return STALE_THRESHOLD_HOURS;
}

async function checkAndAlertStaleUnits(sock) {
    const triggerThreshold = getCurrentThreshold(); // 12h à noite, 6h de dia
    // Sempre buscar todas as unidades com mais de 6h (mínimo)
    const url = `${API_BASE_URL}/api/stale-units?hours=6`;
    try {
        const res = await fetch(url);
        if (!res.ok) {
            console.error(`⚠️  Stale check falhou: HTTP ${res.status}`);
            return;
        }
        const data = await res.json();
        let stale = data.stale_units || [];

        if (stale.length === 0) {
            return;
        }

        // Filtrar: só incluir unidades acima do threshold ativo
        stale = stale.filter((u) => u.hours_ago >= triggerThreshold);
        if (stale.length === 0) return;

        // Construir mensagem natural com @mentions
        const mentions = [];
        const lines = [
            `Alerta: ${stale.length === 1 ? "1 unidade" : stale.length + " unidades"} sem atualizar o giro`,
            "",
        ];
        for (const u of stale) {
            const phones = UNIT_TO_PHONES[u.unit_code] || [];
            const mentionParts = [];
            for (const p of phones) {
                const jid = getVerifiedJid(p);
                mentions.push(jid);
                mentionParts.push(`@${jid.replace(/@.*$/, "")}`);
            }
            const mentionSuffix =
                mentionParts.length > 0 ? " " + mentionParts.join(" ") : "";

            let timeDesc;
            if (u.hours_ago >= 999) {
                timeDesc = "mais de 12h sem atualização";
            } else {
                timeDesc = `mais de ${Math.floor(u.hours_ago)}h sem atualização`;
            }

            lines.push(
                `${u.displayed_name} — ${timeDesc}${mentionSuffix}`
            );
        }
        lines.push("");
        lines.push(pickRandom([
            "Favor enviar o giro atualizado.",
            "Por gentileza, enviem a atualização do giro.",
            "Pedimos que atualizem o giro quando possível.",
            "Aguardando atualização do giro.",
            "Necessário atualizar o giro.",
        ]));

        const text = lines.join("\n");

        if (DRY_RUN) {
            console.log(`\n🔔 [DRY RUN] Alerta de UPAs inativas:\n${text}\n`);
            console.log(`   Mentions: ${mentions.join(", ")}\n`);
            return;
        }

        // Enviar para todos os grupos alvo
        for (const groupJid of WHATSAPP_GROUP_IDS) {
            try {
                const msgPayload = { text };
                if (mentions.length > 0) {
                    msgPayload.mentions = mentions;
                }
                await simulateTyping(sock, groupJid);
                await sock.sendMessage(groupJid, msgPayload);
                console.log(
                    `🔔 Alerta enviado para ${groupJid} (${stale.length} unidade(s), ${mentions.length} mention(s), threshold=${triggerThreshold}h)`
                );
            } catch (err) {
                console.error(
                    `❌ Erro ao enviar alerta para ${groupJid}: ${err.message}`
                );
            }
        }

        // Notificar admin via Telegram com cópia da mensagem
        const telegramCopy = text.replace(/@\d+/g, "").replace(/  +/g, " ").trim();
        notifyTelegram(
            `📢 <b>Alerta enviado no grupo</b>\n\n${telegramCopy}`
        );
    } catch (err) {
        console.error(`❌ Erro ao verificar UPAs inativas: ${err.message}`);
    }
}

function startStaleAlertTimer(sock) {
    if (staleAlertInterval) clearInterval(staleAlertInterval);
    const intervalMs = STALE_CHECK_INTERVAL_MIN * 60 * 1000;
    staleAlertInterval = setInterval(
        () => checkAndAlertStaleUnits(sock),
        intervalMs
    );
    // Primeira verificação após 2 minutos (dar tempo do sistema estabilizar)
    setTimeout(() => checkAndAlertStaleUnits(sock), 2 * 60 * 1000);
}

// ── Main ────────────────────────────────────────────────────────────────────

async function startBridge() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true });
    }

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await fetchLatestBaileysVersion();

    console.log("🔌 Giro de Leitos – WhatsApp Bridge");
    console.log(`   Baileys version: ${version.join(".")}`);
    console.log(
        `   Grupos alvo: ${WHATSAPP_GROUP_IDS.length
            ? WHATSAPP_GROUP_IDS.join(", ")
            : "TODOS (nenhum filtro)"
        }`
    );
    console.log(`   API: ${API_BASE_URL}${API_INGEST_PATH}`);
    console.log(`   Dry run: ${DRY_RUN}`);
    console.log("");

    const sock = makeWASocket({
        version,
        logger,
        browser: Browsers.macOS("Safari"),
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        printQRInTerminal: false,
        generateHighQualityLinkPreview: false,
        keepAliveIntervalMs: randInt(25_000, 55_000),
        markOnlineOnConnect: false,
    });
    currentSock = sock;

    // ── QR Code ─────────────────────────────────────────────────────────────
    sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log("\n📱 Escaneie o QR code abaixo com seu WhatsApp:\n");
            qrcode.generate(qr, { small: true });
            console.log(
                "\n   (Abra WhatsApp → Configurações → Dispositivos conectados → Conectar)\n"
            );
        }

        if (connection === "open") {
            console.log("✅ Conectado ao WhatsApp com sucesso!\n");

            // Cancelar notificação de queda pendente (reconectou rápido)
            if (disconnectNotifyTimer) {
                clearTimeout(disconnectNotifyTimer);
                disconnectNotifyTimer = null;
                console.log("↩️  Reconectou antes do timeout — notificação de queda cancelada");
            }

            // Se havia notificado queda, avisar que voltou
            if (lastDisconnectNotify > 0) {
                const now = new Date().toLocaleString("pt-BR", { timeZone: "America/Bahia" });
                notifyTelegram(`✅ <b>WhatsApp Bridge reconectado</b>\n${now}`);
                lastDisconnectNotify = 0;
            }
            pairingCodeRequested = false;

            // ── List groups mode ──────────────────────────────────────────────
            if (LIST_GROUPS) {
                console.log("📋 Listando todos os grupos...\n");
                try {
                    const groups = await sock.groupFetchAllParticipating();
                    const entries = Object.values(groups);
                    entries.sort((a, b) =>
                        (a.subject || "").localeCompare(b.subject || "")
                    );
                    console.log(`Encontrados ${entries.length} grupo(s):\n`);
                    for (const g of entries) {
                        console.log(`  ID: ${g.id}`);
                        console.log(`  Nome: ${g.subject}`);
                        console.log(
                            `  Participantes: ${g.participants?.length || "?"}`
                        );
                        console.log("");
                    }
                } catch (err) {
                    console.error("Erro ao listar grupos:", err.message);
                }
                console.log(
                    "👆 Copie o ID do grupo desejado e configure WHATSAPP_GROUP_IDS."
                );
                console.log("   Encerrando...");
                process.exit(0);
            }

            // ── Resolver JIDs para mentions ─────────────────────────
            await resolvePhoneJids(sock);

            // ── Iniciar alerta periódico de UPAs inativas ────────────────
            if (STALE_ALERTS_ENABLED && WHATSAPP_GROUP_IDS.length > 0) {
                console.log(
                    `⏰ Alerta de UPAs inativas: a cada ${STALE_CHECK_INTERVAL_MIN}min, threshold ${STALE_THRESHOLD_HOURS}h`
                );
                startStaleAlertTimer(sock);
            }
        }

        if (connection === "close") {
            const statusCode =
                lastDisconnect?.error?.output?.statusCode ??
                lastDisconnect?.error?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            const reason = shouldReconnect ? "Reconectando..." : "Deslogado – escaneie QR novamente.";
            console.log(`⚡ Conexão fechada (reason=${statusCode}). ${reason}`);

            if (shouldReconnect) {
                // Só notifica Telegram se ficar desconectado por mais de 60s
                // (evita spam — a maioria das quedas reconecta em <5s)
                if (!disconnectNotifyTimer) {
                    disconnectNotifyTimer = setTimeout(() => {
                        disconnectNotifyTimer = null;
                        const now = new Date().toLocaleString("pt-BR", { timeZone: "America/Bahia" });
                        lastDisconnectNotify = Date.now();
                        notifyTelegram(
                            `⚠️ <b>WhatsApp Bridge</b>\nConexão instável — desconectado há mais de 1 minuto (code=${statusCode}).\nTentando reconectar automaticamente...\n${now}`
                        );
                    }, 60_000);
                }
                setTimeout(() => startBridge(), 3000);
            } else {
                // Deslogado é grave — notifica imediatamente
                if (disconnectNotifyTimer) {
                    clearTimeout(disconnectNotifyTimer);
                    disconnectNotifyTimer = null;
                }
                lastDisconnectNotify = Date.now();
                const now = new Date().toLocaleString("pt-BR", { timeZone: "America/Bahia" });
                notifyTelegram(
                    `🚨 <b>WhatsApp Bridge DESLOGADO</b>\n\nA sessão WhatsApp foi encerrada (code=${statusCode}).\n\n<b>Como reconectar do celular:</b>\n1. Abra o WhatsApp no celular\n2. Vá em Configurações > Dispositivos conectados\n3. Toque em "Conectar dispositivo"\n4. O bot vai gerar um código de pareamento e enviar aqui no Telegram\n\nAguardando 60s para gerar o código...\n${now}`
                );
                // Clear auth to force new pairing/QR
                fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                // Esperar um pouco mais para dar tempo de ler a mensagem
                setTimeout(() => startBridgeWithPairingCode(), 60 * 1000);
            }
        }
    });

    sock.ev.on("creds.update", saveCreds);

    // Registrar handlers de mensagem
    registerMessageHandlers(sock);
}

// ── Handler de mensagens (compartilhado entre startBridge e pairing) ────────
function registerMessageHandlers(sock) {
    sock.ev.on("messages.upsert", async ({ messages, type }) => {
        console.log(`📩 messages.upsert: type=${type}, count=${messages.length}`);

        // Ignore history sync on first connect
        if (type !== "notify") return;

        for (const msg of messages) {
            const jid = msg.key.remoteJid || "?";
            const fromMe = msg.key.fromMe;
            const hasText = !!(msg.message?.conversation || msg.message?.extendedTextMessage?.text);
            console.log(`   → jid=${jid} fromMe=${fromMe} hasText=${hasText} isGroup=${jid.endsWith("@g.us")} isTarget=${isTargetGroup(jid)}`);

            // Skip status broadcasts
            if (msg.key.remoteJid === "status@broadcast") continue;

            // Skip own messages
            if (msg.key.fromMe) continue;

            // Check if it's from a target group
            if (!isTargetGroup(msg.key.remoteJid)) continue;

            // Deduplicate
            const msgId = msg.key.id;
            if (forwardedIds.has(msgId)) continue;
            forwardedIds.add(msgId);
            trimForwardedIds();

            // Extract text
            const text =
                msg.message?.conversation ||
                msg.message?.extendedTextMessage?.text ||
                "";

            if (!text || text.trim().length < 10) {
                // Ignore very short messages, images without caption, etc.
                continue;
            }

            // ── Filtro de conteúdo ──────────────────────────────────────
            const verdict = looksLikeGiro(text);
            if (!verdict.pass) {
                console.log(
                    `🚫 Ignorada (${text.length} chars): ${verdict.reason}`
                );
                continue;
            }

            // Build source label
            const groupName =
                msg.key.remoteJid?.replace("@g.us", "") || "unknown";
            const source = `whatsapp-bridge:${groupName}`;

            // ── Identificar UPA pelo remetente ──────────────────────────
            const senderJid = msg.key.participant; // JID do remetente no grupo
            const senderPhone = phoneFromJid(senderJid);
            const unitHint = unitFromPhone(senderJid);
            if (unitHint) {
                console.log(
                    `📨 Giro detectado (${text.length} chars) → remetente identificado como ${unitHint}`
                );
            } else {
                console.log(
                    `📨 Giro detectado no grupo ${groupName} (${text.length} chars) → ${verdict.reason}`
                );
            }

            const apiResult = await forwardToApi(text, source, unitHint, senderPhone);

            // ── Notificar admin via Telegram sobre giro recebido ────────
            // Usar UPA identificada pela API se o remetente não está mapeado
            const apiUnit = apiResult?.event?.data?.upa_name
                || apiResult?.event?.data?.unit_code;
            const upaLabel = unitHint || apiUnit || "UPA não identificada";

            const apiStatus = apiResult?.status;
            const isSuccess = apiStatus === "ok" || apiStatus === "accepted";
            const isPending = apiStatus === "pending";

            const statusEmoji = isSuccess ? "✅" : isPending ? "⚠️" : "❌";
            const statusText = isSuccess ? "processado"
                : isPending ? "pendente (dados faltando)"
                    : `erro: ${apiResult?.detail || apiStatus || "desconhecido"}`;

            notifyTelegram(buildGiroTelegramMsg(
                statusEmoji, upaLabel, statusText, text, apiResult
            ));

            // ── Responder no grupo se faltar dados ──────────────────────
            if (apiResult?.status === "pending" && apiResult?.reply_text) {
                console.log(
                    `⚠️  Giro pendente — respondendo no grupo com aviso de dados faltantes`
                );
                try {
                    await humanDelay(2000, 6000);
                    await simulateTyping(sock, msg.key.remoteJid);
                    await sock.sendMessage(msg.key.remoteJid, {
                        text: apiResult.reply_text,
                    }, { quoted: msg });
                    console.log(`📤 Aviso enviado ao grupo com sucesso`);
                } catch (replyErr) {
                    console.error(`❌ Erro ao responder no grupo: ${replyErr.message}`);
                }
            }
        }
    });
}

// ── Entry ───────────────────────────────────────────────────────────────────

// Número vinculado ao WhatsApp (para pairing code remoto)
const WHATSAPP_PHONE_NUMBER = process.env.WHATSAPP_PHONE_NUMBER || "557181619480";

/**
 * Inicia o bridge com pairing code em vez de QR code.
 * Permite reconectar remotamente: o código é enviado via Telegram
 * e o usuário digita no WhatsApp do celular.
 */
async function startBridgeWithPairingCode() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true });
    }

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await fetchLatestBaileysVersion();

    console.log("🔌 Giro de Leitos – Reconexão via Pairing Code");

    const sock = makeWASocket({
        version,
        logger,
        browser: Browsers.macOS("Safari"),
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        printQRInTerminal: false,
        generateHighQualityLinkPreview: false,
        keepAliveIntervalMs: randInt(25_000, 55_000),
        markOnlineOnConnect: false,
    });
    currentSock = sock;

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
        const { connection, lastDisconnect } = update;

        if (connection === "open") {
            console.log("✅ Reconectado via pairing code!\n");
            pairingCodeRequested = false;
            if (disconnectNotifyTimer) {
                clearTimeout(disconnectNotifyTimer);
                disconnectNotifyTimer = null;
            }
            const now = new Date().toLocaleString("pt-BR", { timeZone: "America/Bahia" });
            notifyTelegram(`✅ <b>WhatsApp Bridge reconectado via pairing code</b>\n${now}`);

            // Resolver JIDs e iniciar timer
            await resolvePhoneJids(sock);
            if (STALE_ALERTS_ENABLED && WHATSAPP_GROUP_IDS.length > 0) {
                console.log(
                    `⏰ Alerta de UPAs inativas: a cada ${STALE_CHECK_INTERVAL_MIN}min, threshold ${STALE_THRESHOLD_HOURS}h`
                );
                startStaleAlertTimer(sock);
            }
        }

        if (connection === "close") {
            const statusCode =
                lastDisconnect?.error?.output?.statusCode ??
                lastDisconnect?.error?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            console.log(`⚡ Pairing: conexão fechada (reason=${statusCode})`);

            if (shouldReconnect) {
                setTimeout(() => startBridge(), 3000);
            } else {
                const now = new Date().toLocaleString("pt-BR", { timeZone: "America/Bahia" });
                notifyTelegram(
                    `🚨 <b>Pairing code falhou</b>\nNão foi possível reconectar automaticamente.\nAcesse o servidor para escanear QR code manualmente.\n${now}`
                );
                fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                setTimeout(() => startBridge(), 5000);
            }
        }
    });

    // Solicitar pairing code se ainda não autenticado
    if (!state.creds.registered && !pairingCodeRequested) {
        pairingCodeRequested = true;
        // Aguardar socket estabilizar
        await new Promise((r) => setTimeout(r, 5000));
        try {
            const code = await sock.requestPairingCode(WHATSAPP_PHONE_NUMBER);
            console.log(`🔑 Pairing code: ${code}`);
            notifyTelegram(
                `🔑 <b>Código de pareamento WhatsApp</b>\n\n<code>${code}</code>\n\nAbra o WhatsApp no celular:\n1. Configurações > Dispositivos conectados\n2. Conectar dispositivo\n3. Toque em "Conectar com número de telefone"\n4. Digite o código acima\n\nO código expira em poucos minutos.`
            );
        } catch (err) {
            console.error(`❌ Erro ao solicitar pairing code: ${err.message}`);
            notifyTelegram(
                `❌ <b>Erro ao gerar pairing code</b>\n${err.message}\n\nÉ necessário acessar o servidor para reconectar.`
            );
        }
    }

    // Registrar handlers de mensagem (mesmos do startBridge)
    registerMessageHandlers(sock);
}

startBridge().catch((err) => {
    console.error("❌ Erro fatal:", err);
    process.exit(1);
});
