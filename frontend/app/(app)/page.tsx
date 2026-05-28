'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { LayoutGroup } from 'framer-motion';
import { useRouter } from 'next/navigation';
import {
  apiFetch,
  apiMutate,
  ApiError,
  type Bed,
  type Counter,
  type Exam,
  type SectorConfig,
  type Specialist,
} from '@/lib/api';
import { SECTORS, type SectorKey } from '@/lib/sectors';
import { useToast } from '@/lib/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { useUnitState } from '@/hooks/useUnitState';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { UnitPicker } from '@/components/admin/UnitPicker';
import { RedRoomBed } from '@/components/beds/RedRoomBed';
import { CounterSector } from '@/components/beds/CounterSector';
import { SpecialistCard } from '@/components/beds/SpecialistCard';
import { ExamCard } from '@/components/beds/ExamCard';
import { GiroProvenanceBadge } from '@/components/beds/GiroProvenanceBadge';

const ADMIN_VIEW_KEY = 'gl_admin_viewing_unit';
const SHIFT_UNIT_NAME_KEY = 'gl_unit_name';

interface AdminUnit {
  id: string;
  code: string;
  canonical_name: string;
  slug: string;
  active: boolean;
  coordinator_count: number;
  enabled_sector_count: number;
  red_capacity: number;
}

export default function HomePage() {
  const router = useRouter();
  const { user, hydrated, isAdmin } = useCurrentUser();

  useEffect(() => {
    if (!hydrated) return;
    if (!user) router.replace('/pair');
  }, [hydrated, user, router]);

  if (!hydrated) return null;
  if (!user) return null;

  if (isAdmin) return <AdminHome />;
  return <ShiftHome unitId={user.unit_id ?? null} userName={user.name} />;
}

// ---------------------------------------------------------------------------
// Admin: picker + giro de UPA selecionada
// ---------------------------------------------------------------------------
function AdminHome() {
  const router = useRouter();
  const toast = useToast();
  const { user } = useCurrentUser();

  const [units, setUnits] = useState<AdminUnit[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [selectedUnit, setSelectedUnit] = useState<string>('');

  const loadUnits = useCallback(async () => {
    setUnitsLoading(true);
    try {
      const rows = await apiFetch<AdminUnit[]>('/api/admin/units');
      setUnits(rows);
      let initial = '';
      try {
        initial = window.localStorage.getItem(ADMIN_VIEW_KEY) ?? '';
      } catch {
        /* ignore */
      }
      const valid = rows.find((u) => u.id === initial);
      setSelectedUnit(valid ? valid.id : rows[0]?.id ?? '');
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/admin/login');
        return;
      }
      toast.error(err instanceof ApiError ? err.message : 'Falha ao carregar UPAs');
    } finally {
      setUnitsLoading(false);
    }
  }, [router, toast]);

  useEffect(() => {
    void loadUnits();
  }, [loadUnits]);

  const onPick = (id: string) => {
    setSelectedUnit(id);
    try {
      window.localStorage.setItem(ADMIN_VIEW_KEY, id);
    } catch {
      /* ignore */
    }
  };

  const selected = units.find((u) => u.id === selectedUnit) ?? null;
  const unitName = selected?.canonical_name ?? 'Selecione uma UPA';

  return (
    <>
      <OfflineBanner />
      <TopBar unitName={unitName} shiftLabel={user ? `Admin · ${user.name}` : 'Admin'} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <section className="mt-2">
          <div className="mb-2 px-1">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
              UPA em exibição
            </h2>
          </div>
          <UnitPicker
            units={units}
            value={selectedUnit}
            onChange={onPick}
            loading={unitsLoading}
          />
        </section>

        {selectedUnit ? (
          <UnitGiro unitId={selectedUnit} />
        ) : (
          !unitsLoading && (
            <p className="mt-8 rounded-card border border-line bg-surface p-4 text-center text-sm text-ink-2">
              Nenhuma UPA disponível. Cadastre uma no painel admin.
            </p>
          )
        )}
      </main>

      <ToastViewport />
    </>
  );
}

// ---------------------------------------------------------------------------
// Shift / profissional / coordenador: giro da própria unidade
// ---------------------------------------------------------------------------
function ShiftHome({ unitId, userName }: { unitId: string | null; userName: string }) {
  const router = useRouter();
  const [cachedName, setCachedName] = useState<string | null>(null);

  useEffect(() => {
    try {
      setCachedName(window.localStorage.getItem(SHIFT_UNIT_NAME_KEY));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!unitId) router.replace('/pair');
  }, [unitId, router]);

  if (!unitId) return null;

  return (
    <>
      <OfflineBanner />
      <TopBar unitName={cachedName ?? 'Plantão'} shiftLabel={userName} />
      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <UnitGiro unitId={unitId} />
      </main>
      <ToastViewport />
    </>
  );
}

// ---------------------------------------------------------------------------
// Visão "giro" comum: lê unit state e renderiza apenas sectors habilitados.
// ---------------------------------------------------------------------------
function UnitGiro({ unitId }: { unitId: string }) {
  const toast = useToast();
  const { data, isLoading, error, refetch } = useUnitState(unitId);

  const enabledKeys = useMemo(() => {
    const set = new Set<string>();
    (data?.sectors_config ?? []).forEach((s) => s.enabled && set.add(s.sector_key));
    return set;
  }, [data?.sectors_config]);

  const redCapacity = useMemo(() => {
    const cfg = data?.sectors_config.find((s) => s.sector_key === 'red_room');
    return cfg?.capacity ?? 0;
  }, [data?.sectors_config]);

  const bedsByNumber = useMemo(() => {
    const map = new Map<number, Bed>();
    (data?.beds ?? []).forEach((b) => map.set(b.bed_number, b));
    return map;
  }, [data?.beds]);

  const countersByKey = useMemo(() => indexBy(data?.counters ?? [], (c) => c.sector_key), [data?.counters]);
  const specialistsByKey = useMemo(() => indexBy(data?.specialists ?? [], (s) => s.sector_key), [data?.specialists]);
  const examsByKey = useMemo(() => indexBy(data?.exams ?? [], (e) => e.sector_key), [data?.exams]);

  if (isLoading && !data) return <GiroSkeleton />;
  if (error) {
    return (
      <div className="mt-6 rounded-card border border-critical/30 bg-critical-soft p-4 text-sm text-critical-ink">
        Falha ao carregar estado da unidade.
      </div>
    );
  }
  if (!data) return null;

  const redAssumed = data.red_room_assumed ?? false;
  const maxBedNumber = (data.beds ?? []).reduce((m, b) => Math.max(m, b.bed_number), 0);
  const redBedCount = Math.max(redCapacity, maxBedNumber);

  const assumeRedRoom = async () => {
    try {
      await apiMutate(`/api/unit/${unitId}/red-room/assume`, { method: 'POST' });
      await refetch();
      toast.success('Você assumiu a sala vermelha · edição liberada');
    } catch (err) {
      toast.error(apiErrorMsg(err, 'Falha ao assumir o giro'));
    }
  };
  const releaseRedRoom = async () => {
    try {
      await apiMutate(`/api/unit/${unitId}/red-room/release`, { method: 'POST' });
      await refetch();
      toast.show('Sala vermelha de volta ao modo ao vivo (WhatsApp)');
    } catch (err) {
      toast.error(apiErrorMsg(err, 'Falha ao liberar o giro'));
    }
  };

  const yellowKeys = filterEnabledByPrefix(enabledKeys, ['yellow_']);
  const isolationKeys = filterEnabledByPrefix(enabledKeys, ['isolation_']);
  const otherCounterKeys = filterEnabledByKey(enabledKeys, [
    'medication_room',
    'ward_internment',
    'ward_pediatric_internment',
    'pediatric_observation',
    'obituary',
  ]);
  const specialistKeys = filterEnabledByType(enabledKeys, 'specialist');
  const examKeys = filterEnabledByType(enabledKeys, 'exam');

  return (
    <>
      <GiroProvenanceBadge unitId={unitId} provenance={data.provenance ?? null} />

      {enabledKeys.has('red_room') && redBedCount > 0 && (
        <Section title="Sala vermelha" subtitle="Leitos críticos com paciente identificado">
          {redAssumed ? (
            <div className="mb-3 flex items-center justify-between gap-3 rounded-card border border-line bg-surface px-3.5 py-2.5">
              <span className="min-w-0 text-[12px] leading-tight text-ink-2">
                Em edição manual
                {data.red_room_assumed_by ? <> · assumido por {data.red_room_assumed_by}</> : null}
              </span>
              <button
                type="button"
                onClick={() => void releaseRedRoom()}
                className="shrink-0 rounded-full border border-line px-3 py-1.5 text-[12px] font-medium text-ink-2"
              >
                Voltar ao ao vivo
              </button>
            </div>
          ) : (
            <div className="mb-3 flex items-center justify-between gap-3 rounded-card border border-warning/35 bg-warning-soft px-3.5 py-2.5 text-warning-ink">
              <span className="min-w-0 text-[12px] font-medium leading-tight">
                Ao vivo via WhatsApp · assuma para editar os leitos
              </span>
              <button
                type="button"
                onClick={() => void assumeRedRoom()}
                className="shrink-0 rounded-full bg-warning px-3 py-1.5 text-[12px] font-semibold text-[var(--ink-on-color)]"
              >
                Assumir giro
              </button>
            </div>
          )}
          <LayoutGroup>
            <div className="space-y-3">
              {Array.from({ length: redBedCount }, (_, i) => i + 1).map((bedNumber) => {
                const bed = bedsByNumber.get(bedNumber) ?? null;
                return (
                  <RedRoomBed
                    key={bedNumber}
                    bed={bed}
                    bedNumber={bedNumber}
                    live={!redAssumed}
                    isExtra={bedNumber > redCapacity}
                    onSave={async ({ patient_sigla, clinical_summary }) => {
                      try {
                        await apiMutate(
                          `/api/unit/${unitId}/beds/${bedNumber}`,
                          {
                            method: 'PUT',
                            headers: ifMatch(bed?.version),
                            body: JSON.stringify({ patient_sigla, clinical_summary }),
                          },
                          { offlineQueue: true },
                        );
                        toast.success(`Leito ${bedNumber} salvo`);
                      } catch (err) {
                        toast.error(apiErrorMsg(err, 'Falha ao salvar leito'));
                      }
                    }}
                    onDischarge={async () => {
                      try {
                        await apiMutate(
                          `/api/unit/${unitId}/beds/${bedNumber}/discharge`,
                          { method: 'POST', headers: ifMatch(bed?.version) },
                          { offlineQueue: true },
                        );
                        toast.success(`Alta no leito ${bedNumber}`);
                      } catch (err) {
                        toast.error(apiErrorMsg(err, 'Falha na alta'));
                      }
                    }}
                    onDeath={async (pin) => {
                      try {
                        await apiMutate(`/api/unit/${unitId}/beds/${bedNumber}/death`, {
                          method: 'POST',
                          headers: { ...ifMatch(bed?.version), 'X-PIN-Confirm': pin },
                        });
                        toast.show(`Óbito registrado no leito ${bedNumber}`, 'warning');
                      } catch (err) {
                        toast.error(apiErrorMsg(err, 'Falha ao registrar óbito'));
                        throw err;
                      }
                    }}
                    onTransfer={async () => {
                      try {
                        await apiMutate(
                          `/api/unit/${unitId}/beds/${bedNumber}/transfer`,
                          {
                            method: 'POST',
                            headers: ifMatch(bed?.version),
                            body: JSON.stringify({ destination: null }),
                          },
                          { offlineQueue: true },
                        );
                        toast.show(`Transferência no leito ${bedNumber}`, 'warning');
                      } catch (err) {
                        toast.error(apiErrorMsg(err, 'Falha na transferência'));
                      }
                    }}
                    onClear={async () => {
                      try {
                        await apiMutate(
                          `/api/unit/${unitId}/beds/${bedNumber}/clear`,
                          { method: 'POST', headers: ifMatch(bed?.version) },
                          { offlineQueue: true },
                        );
                        toast.show(`Leito ${bedNumber} esvaziado`);
                      } catch (err) {
                        toast.error(apiErrorMsg(err, 'Falha ao esvaziar'));
                      }
                    }}
                  />
                );
              })}
            </div>
          </LayoutGroup>
        </Section>
      )}

      {yellowKeys.length > 0 && (
        <Section title="Sala amarela" subtitle="Ocupação por gênero">
          <CounterList keys={yellowKeys} counters={countersByKey} unitId={unitId} />
        </Section>
      )}

      {isolationKeys.length > 0 && (
        <Section title="Isolamento" subtitle="Quartos com precaução">
          <CounterList keys={isolationKeys} counters={countersByKey} unitId={unitId} />
        </Section>
      )}

      {otherCounterKeys.length > 0 && (
        <Section title="Outros setores">
          <CounterList keys={otherCounterKeys} counters={countersByKey} unitId={unitId} />
        </Section>
      )}

      {specialistKeys.length > 0 && (
        <Section title="Especialistas" subtitle="Toque para alterar disponibilidade">
          <div className="grid grid-cols-2 gap-3">
            {specialistKeys.map((key) => {
              const meta = SECTORS[key];
              const sp = specialistsByKey.get(key);
              return (
                <SpecialistCard
                  key={key}
                  sectorKey={key}
                  label={shortSpecialistLabel(meta.label)}
                  status={sp?.status ?? 'unavailable'}
                  icon={meta.icon}
                  onChange={async (next) => {
                    try {
                      await apiMutate(
                        `/api/unit/${unitId}/specialists/${key}`,
                        {
                          method: 'PUT',
                          headers: ifMatch(sp?.version),
                          body: JSON.stringify({ status: next }),
                        },
                        { offlineQueue: true },
                      );
                      toast.success(`${meta.label}: ${next}`);
                    } catch (err) {
                      toast.error(apiErrorMsg(err, 'Falha ao atualizar especialista'));
                    }
                  }}
                />
              );
            })}
          </div>
        </Section>
      )}

      {examKeys.length > 0 && (
        <Section title="Exames" subtitle="Disponibilidade de equipamentos">
          <div className="space-y-2.5">
            {examKeys.map((key) => {
              const meta = SECTORS[key];
              const ex = examsByKey.get(key);
              return (
                <ExamCard
                  key={key}
                  sectorKey={key}
                  label={meta.label}
                  status={ex?.status ?? 'working'}
                  unavailable_reason={ex?.unavailable_reason ?? null}
                  icon={meta.icon}
                  onChange={async (next) => {
                    try {
                      await apiMutate(
                        `/api/unit/${unitId}/exams/${key}`,
                        {
                          method: 'PUT',
                          headers: ifMatch(ex?.version),
                          body: JSON.stringify(next),
                        },
                        { offlineQueue: true },
                      );
                      toast.success(`${meta.label} atualizado`);
                    } catch (err) {
                      toast.error(apiErrorMsg(err, 'Falha ao atualizar exame'));
                    }
                  }}
                />
              );
            })}
          </div>
        </Section>
      )}

      {enabledKeys.size === 0 && (
        <p className="mt-8 rounded-card border border-line bg-surface p-4 text-center text-sm text-ink-2">
          Nenhum setor habilitado nesta UPA. Configure em <em>Setores</em>.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function CounterList({
  keys,
  counters,
  unitId,
}: {
  keys: SectorKey[];
  counters: Map<string, Counter>;
  unitId: string;
}) {
  const toast = useToast();
  return (
    <div className="grid grid-cols-1 gap-3">
      {keys.map((key) => {
        const meta = SECTORS[key];
        const c = counters.get(key);
        return (
          <CounterSector
            key={key}
            sector={{
              key,
              label: counterLabel(meta.label),
              occupancy: c?.occupancy ?? 0,
              capacity: c?.capacity ?? 0,
              version: c?.version ?? 0,
              icon: meta.icon,
            }}
            onSave={async (next) => {
              try {
                await apiMutate(
                  `/api/unit/${unitId}/counters/${key}`,
                  {
                    method: 'PUT',
                    headers: ifMatch(c?.version),
                    body: JSON.stringify(next),
                  },
                  { offlineQueue: true },
                );
                toast.success(`${counterLabel(meta.label)} atualizado`);
              } catch (err) {
                toast.error(apiErrorMsg(err, 'Falha ao atualizar contador'));
              }
            }}
          />
        );
      })}
    </div>
  );
}

function GiroSkeleton() {
  return (
    <div className="mt-6 space-y-4" aria-label="Carregando giro">
      {[0, 1, 2].map((i) => (
        <div key={i} className="skeleton-card h-24" />
      ))}
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6 first:mt-2">
      <div className="mb-2 flex items-baseline justify-between gap-3 px-1 pt-3">
        <h2 className="truncate text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
          {title}
        </h2>
        {subtitle && (
          <p className="shrink-0 text-[13px] tabular-nums text-ink-2">{subtitle}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function ifMatch(version: number | null | undefined): Record<string, string> {
  if (typeof version !== 'number' || version <= 0) return {};
  return { 'If-Match': String(version) };
}

function apiErrorMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return 'Outro plantonista atualizou antes de você. Recarregando…';
    return err.message || fallback;
  }
  return fallback;
}

function indexBy<T>(items: T[], key: (item: T) => string): Map<string, T> {
  const map = new Map<string, T>();
  for (const it of items) map.set(key(it), it);
  return map;
}

function filterEnabledByPrefix(enabled: Set<string>, prefixes: string[]): SectorKey[] {
  return (Object.keys(SECTORS) as SectorKey[])
    .filter((k) => enabled.has(k) && prefixes.some((p) => k.startsWith(p)))
    .sort((a, b) => SECTORS[a].order - SECTORS[b].order);
}

function filterEnabledByKey(enabled: Set<string>, keys: SectorKey[]): SectorKey[] {
  return keys.filter((k) => enabled.has(k)).sort((a, b) => SECTORS[a].order - SECTORS[b].order);
}

function filterEnabledByType(enabled: Set<string>, type: 'specialist' | 'exam'): SectorKey[] {
  return (Object.keys(SECTORS) as SectorKey[])
    .filter((k) => enabled.has(k) && SECTORS[k].type === type)
    .sort((a, b) => SECTORS[a].order - SECTORS[b].order);
}

function counterLabel(full: string): string {
  // "Sala amarela — Feminino" → "Feminino"; "Isolamento adulto M" → keep
  const dash = full.split(' — ');
  return dash.length > 1 ? dash[1]! : full;
}

function shortSpecialistLabel(full: string): string {
  return full;
}
