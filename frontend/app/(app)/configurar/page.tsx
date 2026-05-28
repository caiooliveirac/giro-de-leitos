'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Save } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { SECTOR_LIST, type SectorKey, type SectorMeta } from '@/lib/sectors';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';

interface SectorConfig {
  sector_key: string;
  enabled: boolean;
  capacity: number | null;
}

interface AdminUnit {
  id: string;
  canonical_name: string;
}

export default function ConfigurarPage() {
  const toast = useToast();
  const searchParams = useSearchParams();
  const { user, hydrated, isCoordinator, isAdmin } = useCurrentUser();

  // Admins may override unit via ?unit_id=<id>. Coordinators always use their own unit.
  const queryUnitId = searchParams?.get('unit_id') ?? null;
  const unitId = isAdmin
    ? queryUnitId || user?.unit_id || null
    : user?.unit_id ?? null;

  const [items, setItems] = useState<Record<string, SectorConfig>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [unitName, setUnitName] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!unitId) {
      setLoading(false);
      return;
    }
    try {
      const rows = await apiFetch<SectorConfig[]>(`/api/unit/${unitId}/sectors/config`);
      const map: Record<string, SectorConfig> = {};
      for (const r of rows) map[r.sector_key] = r;
      // Ensure all 19 keys exist locally even if backend omits some.
      for (const meta of SECTOR_LIST) {
        if (!map[meta.key]) {
          map[meta.key] = {
            sector_key: meta.key,
            enabled: false,
            capacity: meta.type === 'counter' || meta.key === 'red_room' ? 0 : null,
          };
        }
      }
      setItems(map);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao carregar configuração';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [unitId, toast]);

  // Fetch UPA display name (admin uses /api/admin/units; coord uses /state).
  const loadUnitName = useCallback(async () => {
    if (!unitId) return;
    try {
      if (isAdmin) {
        const list = await apiFetch<AdminUnit[]>('/api/admin/units');
        const match = list.find((u) => u.id === unitId);
        if (match) setUnitName(match.canonical_name);
      } else {
        const state = await apiFetch<{ unit?: { canonical_name?: string } }>(
          `/api/unit/${unitId}/state`,
        );
        if (state?.unit?.canonical_name) setUnitName(state.unit.canonical_name);
      }
    } catch {
      // header label is non-critical
    }
  }, [unitId, isAdmin]);

  useEffect(() => {
    if (hydrated) {
      void load();
      void loadUnitName();
    }
  }, [hydrated, load, loadUnitName]);

  const toggle = (key: SectorKey, enabled: boolean) => {
    setItems((prev) => ({
      ...prev,
      [key]: { ...prev[key], enabled },
    }));
  };

  const setCapacity = (key: SectorKey, capacity: number) => {
    const safe = Number.isFinite(capacity) && capacity >= 0 ? Math.floor(capacity) : 0;
    setItems((prev) => ({
      ...prev,
      [key]: { ...prev[key], capacity: safe },
    }));
  };

  const save = async () => {
    if (!unitId) return;

    // Client-side validation.
    const redRoom = items['red_room'];
    if (redRoom?.enabled && (!redRoom.capacity || redRoom.capacity <= 0)) {
      toast.warning('Sala vermelha ativada exige capacidade > 0.');
      return;
    }
    for (const m of SECTOR_LIST) {
      const cfg = items[m.key];
      if (!cfg) continue;
      if (cfg.enabled && m.type === 'counter') {
        if (cfg.capacity == null || cfg.capacity < 0) {
          toast.warning(`Capacidade inválida em ${m.label}.`);
          return;
        }
      }
    }

    setSaving(true);
    try {
      const payload = {
        items: SECTOR_LIST.map((m) => ({
          sector_key: m.key,
          enabled: items[m.key]?.enabled ?? false,
          capacity:
            m.type === 'counter' || m.key === 'red_room'
              ? Number.isFinite(items[m.key]?.capacity ?? NaN)
                ? items[m.key]?.capacity
                : 0
              : null,
        })),
      };
      await apiFetch(`/api/unit/${unitId}/sectors/config`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      toast.success('Configuração salva');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao salvar';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const grouped = useMemo(() => {
    const groups: Record<string, SectorMeta[]> = { beds: [], counter: [], specialist: [], exam: [] };
    for (const m of SECTOR_LIST) groups[m.type].push(m);
    return groups;
  }, []);

  if (hydrated && !isCoordinator) {
    return (
      <main className="mx-auto min-h-dvh w-full max-w-[520px] px-4 pt-12 text-center">
        <p className="text-sm text-text-secondary">
          Apenas coordenadores e admins podem configurar setores.
        </p>
      </main>
    );
  }

  const topBarLabel = unitName
    ? `Configurando: ${unitName}`
    : 'Configurar setores';

  return (
    <>
      <OfflineBanner />
      <TopBar unitName={topBarLabel} shiftLabel={user?.name ?? null} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-28 pt-4">
        {isAdmin && queryUnitId && (
          <div className="mb-3 rounded-card border border-accent-blue/30 bg-accent-blue/5 px-3 py-2 text-xs text-text-secondary">
            Modo admin: editando setores de <strong>{unitName ?? queryUnitId}</strong>.
          </div>
        )}

        {loading && (
          <p className="text-center text-sm text-text-secondary">Carregando configuração…</p>
        )}
        {!loading && !unitId && (
          <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
            Nenhuma UPA associada à sua sessão.
          </p>
        )}

        {!loading && unitId && (
          <>
            <Group label="Sala vermelha" metas={grouped.beds} items={items} onToggle={toggle} onCapacity={setCapacity} />
            <Group label="Counters (gênero/isolamento)" metas={grouped.counter} items={items} onToggle={toggle} onCapacity={setCapacity} />
            <Group label="Especialistas" metas={grouped.specialist} items={items} onToggle={toggle} onCapacity={setCapacity} />
            <Group label="Exames" metas={grouped.exam} items={items} onToggle={toggle} onCapacity={setCapacity} />
          </>
        )}
      </main>

      {!loading && unitId && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/90 px-4 py-3 backdrop-blur-xl">
          <div className="mx-auto flex max-w-[520px] justify-end">
            <motion.button
              type="button"
              whileTap={{ scale: 0.96 }}
              onClick={() => void save()}
              disabled={saving}
              className="flex items-center gap-2 rounded-pill bg-accent-blue px-6 py-3 text-sm font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              <Save size={16} /> {saving ? 'Salvando…' : 'Salvar'}
            </motion.button>
          </div>
        </div>
      )}
      <ToastViewport />
    </>
  );
}

function Group({
  label,
  metas,
  items,
  onToggle,
  onCapacity,
}: {
  label: string;
  metas: SectorMeta[];
  items: Record<string, SectorConfig>;
  onToggle: (key: SectorKey, v: boolean) => void;
  onCapacity: (key: SectorKey, v: number) => void;
}) {
  if (metas.length === 0) return null;
  return (
    <section className="mt-6 first:mt-2">
      <h2 className="mb-2.5 px-1 text-[15px] font-semibold uppercase tracking-wider text-text-tertiary">
        {label}
      </h2>
      <div className="space-y-2">
        {metas.map((m) => {
          const cfg = items[m.key];
          const Icon = m.icon;
          const enabled = cfg?.enabled ?? false;
          const showCapacity = enabled && (m.type === 'counter' || m.key === 'red_room');
          return (
            <div
              key={m.key}
              className="flex items-center gap-3 rounded-card border border-border bg-card px-3 py-3"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
                <Icon size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-text-primary">{m.label}</p>
                <p className="text-xs text-text-secondary">
                  {m.type === 'counter' ? 'Setor por contagem' : m.type === 'beds' ? 'Leitos individuais' : m.type === 'specialist' ? 'Especialista' : 'Exame'}
                </p>
              </div>

              {showCapacity && (
                <input
                  type="number"
                  min={0}
                  max={50}
                  inputMode="numeric"
                  value={cfg?.capacity ?? 0}
                  onChange={(e) => onCapacity(m.key, Number(e.target.value))}
                  aria-label={`Capacidade de ${m.label}`}
                  className="w-16 rounded-xl border border-border bg-surface px-2 py-1.5 text-center text-sm text-text-primary focus:border-accent-blue focus:outline-none focus:ring-2 focus:ring-accent-blue/30"
                />
              )}

              <Switch
                checked={enabled}
                onChange={(v) => onToggle(m.key, v)}
                label={`Ativar ${m.label}`}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-card ${
        checked ? 'bg-accent-green' : 'bg-border'
      }`}
    >
      <motion.span
        layout
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        className={`inline-block h-5 w-5 rounded-full bg-white shadow ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}
