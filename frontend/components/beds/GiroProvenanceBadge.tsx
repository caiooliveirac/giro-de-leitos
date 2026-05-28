'use client';

import { useEffect, useMemo, useState } from 'react';
import { apiFetch, type GiroProvenance, type GiroHistoryEntry } from '@/lib/api';

type Freshness = 'fresh' | 'stale' | 'old' | 'none';

function freshnessFor(iso: string | null | undefined): Freshness {
  if (!iso) return 'none';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'none';
  const ageMin = (Date.now() - t) / 60_000;
  if (ageMin < 120) return 'fresh';
  if (ageMin < 360) return 'stale';
  return 'old';
}

function fmtClock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function fmtDateClock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const today = new Date();
  const sameDay =
    d.getDate() === today.getDate() &&
    d.getMonth() === today.getMonth() &&
    d.getFullYear() === today.getFullYear();
  const hh = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return `hoje, ${hh}`;
  const dd = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
  return `${dd} · ${hh}`;
}

function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const diffMin = Math.round((Date.now() - t) / 60_000);
  if (diffMin < 1) return 'agora';
  if (diffMin < 60) return `há ${diffMin} min`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `há ${h}h`;
  const d = Math.floor(h / 24);
  return `há ${d}d`;
}

const TONE: Record<Freshness, { wrap: string; dot: string; label: string }> = {
  fresh: {
    wrap: 'border-success/30 bg-success-soft text-success-ink',
    dot: 'bg-success',
    label: 'atualizado',
  },
  stale: {
    wrap: 'border-warning/35 bg-warning-soft text-warning-ink',
    dot: 'bg-warning',
    label: 'pode estar defasado',
  },
  old: {
    wrap: 'border-critical/30 bg-critical-soft text-critical-ink',
    dot: 'bg-critical',
    label: 'defasado',
  },
  none: {
    wrap: 'border-line bg-surface text-ink-2',
    dot: 'bg-ink-3',
    label: 'sem giro',
  },
};

function originLabel(prov: GiroProvenance | null | undefined): string {
  if (!prov || !prov.latest_kind) return 'Sem giro registrado';
  if (prov.latest_kind === 'whatsapp') return 'Giro atualizado pelo WhatsApp';
  const name = prov.manual?.user_name?.trim();
  return name ? `Atualizado no site por ${name}` : 'Atualizado no site';
}

export function GiroProvenanceBadge({
  unitId,
  provenance,
}: {
  unitId: string;
  provenance: GiroProvenance | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<GiroHistoryEntry[] | null>(null);
  const [loadingHist, setLoadingHist] = useState(false);
  const [, setTick] = useState(0);

  // Re-render a cada 60s pra atualizar o relativo ("há X min") e o tom.
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  const freshness = useMemo(() => freshnessFor(provenance?.latest_at), [provenance?.latest_at]);
  const tone = TONE[freshness];
  const origin = originLabel(provenance);
  const reported = provenance?.latest_kind === 'whatsapp' ? provenance.whatsapp?.reported_at : provenance?.manual?.reported_at;
  const received = provenance?.latest_at;

  async function ensureHistory() {
    if (history || loadingHist) return;
    setLoadingHist(true);
    try {
      const res = await apiFetch<{ items: GiroHistoryEntry[] }>(`/api/unit/${unitId}/giro-history?limit=10`);
      setHistory(res.items ?? []);
    } catch {
      setHistory([]);
    } finally {
      setLoadingHist(false);
    }
  }

  function toggle() {
    setOpen((v) => {
      const next = !v;
      if (next) void ensureHistory();
      return next;
    });
  }

  return (
    <div className={`mt-4 rounded-card border ${tone.wrap}`}>
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left"
        aria-expanded={open}
      >
        <span className={`mt-[3px] h-2 w-2 shrink-0 rounded-full ${tone.dot}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold leading-tight">{origin}</span>
          <span className="mt-0.5 block text-[11px] leading-tight opacity-80">
            {reported ? <>Declarado às {fmtClock(reported)}</> : 'Sem horário declarado'}
            {received ? <> · {fmtRelative(received)}</> : null}
          </span>
        </span>
        <span className="shrink-0 text-[11px] font-medium uppercase tracking-[0.08em] opacity-70">
          {open ? 'fechar' : 'histórico'}
        </span>
      </button>

      {open && (
        <div className="border-t border-current/15 px-3.5 py-3 text-[12px]">
          <dl className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="opacity-70">Declarado</dt>
            <dd className="font-medium">{fmtDateClock(reported)}</dd>
            <dt className="opacity-70">Recebido pelo sistema</dt>
            <dd className="font-medium">{fmtDateClock(received)}</dd>
            <dt className="opacity-70">Origem</dt>
            <dd className="font-medium">
              {provenance?.latest_kind === 'whatsapp'
                ? 'WhatsApp (bridge automático)'
                : provenance?.latest_kind === 'site'
                ? provenance.manual?.user_name
                  ? `Site · ${provenance.manual.user_name}`
                  : 'Site'
                : '—'}
            </dd>
          </dl>

          <div className="text-[11px] font-semibold uppercase tracking-[0.1em] opacity-70">
            Últimos giros recebidos
          </div>
          <ul className="mt-2 space-y-1.5">
            {loadingHist && <li className="opacity-70">Carregando…</li>}
            {!loadingHist && history && history.length === 0 && (
              <li className="opacity-70">Nenhum giro anterior registrado.</li>
            )}
            {!loadingHist &&
              history?.map((h) => (
                <li key={h.id} className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-medium">{fmtDateClock(h.reported_at ?? h.received_at)}</span>
                    <span className="opacity-70">
                      {' · '}
                      {h.source === 'whatsapp' ? 'WhatsApp' : h.source_raw ?? 'fonte desconhecida'}
                    </span>
                  </span>
                  <span className="shrink-0 tabular-nums opacity-80">
                    {h.red.occupied ?? '—'}/{h.red.capacity ?? '—'} V
                    {' · '}
                    {h.yellow.occupied ?? '—'}/{h.yellow.capacity ?? '—'} A
                    {h.is_critical && <span className="ml-1 font-semibold">·crítico</span>}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
