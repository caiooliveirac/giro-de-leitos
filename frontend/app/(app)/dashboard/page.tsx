'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Activity,
  ArrowRightLeft,
  BedDouble,
  HeartPulse,
  ScanLine,
  Skull,
  Stethoscope,
  Users,
} from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { UnitPicker } from '@/components/admin/UnitPicker';

type Period = 'today' | '7d' | '30d' | 'all';

interface AdminUnit {
  id: string;
  code: string;
  canonical_name: string;
  coordinator_count: number;
  enabled_sector_count: number;
}

interface Totals {
  red_patients: number;
  deaths: number;
  discharges: number;
  transfers: number;
  yellow_avg: number | null;
  yellow_max: number | null;
  yellow_current: number | null;
  isolation_avg: number | null;
  isolation_current: number | null;
  days_no_ortho: number;
  days_with_ortho: number;
  days_xray_out: number;
  giros_count: number;
}

interface UnitRow {
  unit_code: string;
  unit_name: string;
  red_patients: number;
  deaths: number;
  discharges: number;
  transfers: number;
  yellow_avg: number | null;
  yellow_current: number | null;
  isolation_current: number | null;
  days_no_ortho: number;
  days_with_ortho: number;
  days_xray_out: number;
  giros_count: number;
}

interface MetricsResponse {
  status: string;
  period: Period;
  unit: string | null;
  totals: Totals;
  by_unit: UnitRow[];
}

const PERIODS: { key: Period; label: string }[] = [
  { key: 'today', label: 'Hoje' },
  { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' },
  { key: 'all', label: 'Tudo' },
];

export default function DashboardPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, hydrated, isAdmin } = useCurrentUser();

  const [period, setPeriod] = useState<Period>('all');
  const [units, setUnits] = useState<AdminUnit[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [selectedUnit, setSelectedUnit] = useState<string>(''); // '' = todas
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!hydrated) return;
    if (!isAdmin) {
      router.replace('/admin/login');
    }
  }, [hydrated, isAdmin, router]);

  const loadUnits = useCallback(async () => {
    setUnitsLoading(true);
    try {
      const rows = await apiFetch<AdminUnit[]>('/api/admin/units');
      setUnits(rows);
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

  const loadMetrics = useCallback(async () => {
    setLoading(true);
    try {
      const code = selectedUnit ? units.find((u) => u.id === selectedUnit)?.code : undefined;
      const params = new URLSearchParams({ period });
      if (code) params.set('unit', code);
      const data = await apiFetch<MetricsResponse>(`/api/dashboard/metrics?${params.toString()}`);
      setMetrics(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/admin/login');
        return;
      }
      toast.error(err instanceof ApiError ? err.message : 'Falha ao carregar indicadores');
    } finally {
      setLoading(false);
    }
  }, [period, selectedUnit, units, router, toast]);

  useEffect(() => {
    if (hydrated && isAdmin) void loadUnits();
  }, [hydrated, isAdmin, loadUnits]);

  useEffect(() => {
    if (hydrated && isAdmin) void loadMetrics();
  }, [hydrated, isAdmin, loadMetrics]);

  if (!hydrated || !isAdmin) return null;

  const t = metrics?.totals;
  const fmt = (v: number | null | undefined) => (v === null || v === undefined ? '—' : String(v));

  return (
    <>
      <OfflineBanner />
      <TopBar unitName="Indicadores" shiftLabel={user?.name ?? null} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4 space-y-4">
        {/* Filtro de período */}
        <div className="flex gap-1.5 rounded-pill border border-border bg-card p-1">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPeriod(p.key)}
              className={`flex-1 rounded-pill px-2 py-1.5 text-xs font-semibold transition ${
                period === p.key
                  ? 'bg-accent-blue text-white'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Filtro de unidade */}
        <div>
          <span className="mb-1.5 block text-xs font-medium text-text-secondary">Unidade</span>
          <UnitPicker
            units={units}
            value={selectedUnit}
            onChange={setSelectedUnit}
            loading={unitsLoading}
            placeholder="Todas as UPAs"
          />
        </div>

        {/* Cards de métricas */}
        <div className="grid grid-cols-2 gap-2.5">
          <MetricCard icon={Users} label="Pacientes na vermelha" value={fmt(t?.red_patients)}
            hint="distintos no período" tone="red" loading={loading} />
          <MetricCard icon={Skull} label="Óbitos" value={fmt(t?.deaths)} tone="obit" loading={loading} />
          <MetricCard icon={HeartPulse} label="Altas" value={fmt(t?.discharges)} tone="green" loading={loading} />
          <MetricCard icon={ArrowRightLeft} label="Transferências" value={fmt(t?.transfers)} tone="blue" loading={loading} />
          <MetricCard icon={BedDouble} label="Amarela (atual)" value={fmt(t?.yellow_current)}
            hint={t?.yellow_avg != null ? `média ${t.yellow_avg} · pico ${fmt(t?.yellow_max)}` : undefined}
            tone="yellow" loading={loading} />
          <MetricCard icon={Activity} label="Isolamento (atual)" value={fmt(t?.isolation_current)}
            hint={t?.isolation_avg != null ? `média ${t.isolation_avg}` : undefined} tone="blue" loading={loading} />
          <MetricCard icon={Stethoscope} label="Dias sem ortopedista" value={fmt(t?.days_no_ortho)}
            hint={`${fmt(t?.days_with_ortho)} dias com`} tone="obit" loading={loading} />
          <MetricCard icon={ScanLine} label="Dias radiografia fora" value={fmt(t?.days_xray_out)}
            hint="best-effort" tone="yellow" loading={loading} />
        </div>

        <p className="px-1 text-xs text-text-secondary">
          {fmt(t?.giros_count)} giros registrados no período selecionado.
        </p>

        {/* Breakdown por unidade */}
        {metrics && metrics.by_unit.length > 0 && (
          <section className="rounded-card border border-border bg-card p-3">
            <h2 className="mb-2 px-1 text-sm font-semibold text-text-primary">Por unidade</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-text-secondary">
                  <tr className="border-b border-border">
                    <th className="py-1.5 pr-2 font-medium">UPA</th>
                    <th className="px-1.5 py-1.5 text-right font-medium" title="Óbitos">Ób.</th>
                    <th className="px-1.5 py-1.5 text-right font-medium" title="Altas">Alt.</th>
                    <th className="px-1.5 py-1.5 text-right font-medium" title="Transferências">Tr.</th>
                    <th className="px-1.5 py-1.5 text-right font-medium" title="Dias sem ortopedista">s/Ort.</th>
                    <th className="py-1.5 pl-1.5 text-right font-medium" title="Giros">Giros</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.by_unit.map((u) => (
                    <tr key={u.unit_code} className="border-b border-border/50 last:border-0">
                      <td className="py-1.5 pr-2 text-text-primary">{u.unit_name}</td>
                      <td className="px-1.5 py-1.5 text-right tabular-nums text-text-primary">{u.deaths}</td>
                      <td className="px-1.5 py-1.5 text-right tabular-nums text-text-primary">{u.discharges}</td>
                      <td className="px-1.5 py-1.5 text-right tabular-nums text-text-primary">{u.transfers}</td>
                      <td className="px-1.5 py-1.5 text-right tabular-nums text-text-primary">{u.days_no_ortho}</td>
                      <td className="py-1.5 pl-1.5 text-right tabular-nums text-text-secondary">{u.giros_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>

      <ToastViewport />
    </>
  );
}

const TONES: Record<string, string> = {
  red: 'text-accent-red bg-accent-red/10',
  obit: 'text-[var(--obit)] bg-[var(--obit-soft)]',
  green: 'text-accent-green bg-accent-green/10',
  blue: 'text-accent-blue bg-accent-blue/10',
  yellow: 'text-accent-amber bg-accent-amber/10',
};

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'blue',
  loading = false,
}: {
  icon: typeof Users;
  label: string;
  value: string;
  hint?: string;
  tone?: keyof typeof TONES | string;
  loading?: boolean;
}) {
  return (
    <div className="rounded-card border border-border bg-card p-3">
      <div className={`mb-2 flex h-8 w-8 items-center justify-center rounded-pill ${TONES[tone] ?? TONES.blue}`}>
        <Icon size={16} />
      </div>
      <p className="text-xs font-medium text-text-secondary">{label}</p>
      {loading ? (
        <div className="mt-1 h-7 w-12 animate-pulse rounded bg-border/60" />
      ) : (
        <p className="mt-0.5 text-2xl font-bold tabular-nums text-text-primary">{value}</p>
      )}
      {hint && !loading && <p className="mt-0.5 text-[11px] text-text-secondary">{hint}</p>}
    </div>
  );
}
