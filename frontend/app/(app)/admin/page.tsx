'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { Building2, Check, Copy, MessageCircle, Settings2, UserRound, X } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { qrImageUrl } from '@/lib/qr';

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

interface PendingUser {
  id: string;
  name: string;
  role: string;
  cargo: string | null;
  cpf_masked: string;
  coren_crm: string | null;
  unit_id: string | null;
  created_at: string;
}

interface InviteCreateResponse {
  id: string;
  token: string;
  type: string;
  target_unit_id: string | null;
  expires_at: string;
}

export default function AdminPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, hydrated, isAdmin } = useCurrentUser();
  const [pending, setPending] = useState<PendingUser[]>([]);
  const [units, setUnits] = useState<AdminUnit[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [selectedUnit, setSelectedUnit] = useState<string>('');
  const [invite, setInvite] = useState<InviteCreateResponse | null>(null);
  const [busy, setBusy] = useState(false);

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
      if (rows.length > 0) {
        setSelectedUnit((cur) => cur || rows[0].id);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/admin/login');
        return;
      }
      const msg = err instanceof ApiError ? err.message : 'Falha ao carregar UPAs';
      toast.error(msg);
    } finally {
      setUnitsLoading(false);
    }
  }, [router, toast]);

  const loadPending = useCallback(async () => {
    try {
      const p = await apiFetch<PendingUser[]>('/api/users/pending');
      setPending(p.filter((u) => u.role === 'coordinator'));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/admin/login');
        return;
      }
      const msg = err instanceof ApiError ? err.message : 'Falha ao carregar pendentes';
      toast.error(msg);
    }
  }, [router, toast]);

  useEffect(() => {
    if (hydrated && isAdmin) {
      void loadUnits();
      void loadPending();
    }
  }, [hydrated, isAdmin, loadUnits, loadPending]);

  const inviteCoordinator = async () => {
    if (!selectedUnit) {
      toast.error('Escolha uma UPA');
      return;
    }
    setBusy(true);
    try {
      const res = await apiFetch<InviteCreateResponse>('/api/invites', {
        method: 'POST',
        body: JSON.stringify({ type: 'coordinator', target_unit_id: selectedUnit }),
      });
      setInvite(res);
      toast.success('Convite de coordenador gerado');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao gerar convite';
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/approve`, { method: 'POST' });
      toast.success('Coordenador aprovado');
      setPending((p) => p.filter((u) => u.id !== id));
      void loadUnits();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao aprovar';
      toast.error(msg);
    }
  };

  const reject = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/reject`, { method: 'POST' });
      toast.warning('Cadastro rejeitado');
      setPending((p) => p.filter((u) => u.id !== id));
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao rejeitar';
      toast.error(msg);
    }
  };

  const goToConfigure = (unitId: string) => {
    router.push(`/configurar?unit_id=${unitId}`);
  };

  if (!hydrated) return null;
  if (!isAdmin) return null;

  return (
    <>
      <OfflineBanner />
      <TopBar unitName="Admin global" shiftLabel={user?.name ?? null} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <Section
          title="Convidar coordenador"
          subtitle="Selecione a UPA destino e gere o link"
        >
          <div className="rounded-card border border-border bg-card p-4 space-y-3">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-text-secondary">
                UPA destino
              </span>
              <select
                value={selectedUnit}
                onChange={(e) => setSelectedUnit(e.target.value)}
                disabled={unitsLoading || units.length === 0}
                className="w-full rounded-xl border border-border bg-surface px-3.5 py-3 text-base text-text-primary focus:border-accent-blue focus:outline-none focus:ring-2 focus:ring-accent-blue/30 disabled:opacity-50"
              >
                {unitsLoading && <option>Carregando UPAs…</option>}
                {!unitsLoading && units.length === 0 && <option>Nenhuma UPA cadastrada</option>}
                {units.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.canonical_name}
                  </option>
                ))}
              </select>
            </label>

            {!invite && (
              <button
                type="button"
                disabled={busy || !selectedUnit}
                onClick={inviteCoordinator}
                className="w-full rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              >
                {busy ? 'Gerando…' : 'Gerar convite'}
              </button>
            )}
            {invite && <InviteCard invite={invite} onReset={() => setInvite(null)} />}
          </div>
        </Section>

        <Section title="UPAs ativas" subtitle={`${units.length} unidade(s) cadastrada(s)`}>
          <div className="space-y-2">
            {unitsLoading && <UnitsSkeleton />}
            {!unitsLoading && units.length === 0 && (
              <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
                Nenhuma UPA cadastrada ainda.
              </p>
            )}
            {!unitsLoading &&
              units.map((u) => (
                <div
                  key={u.id}
                  className="flex items-center gap-3 rounded-card border border-border bg-card px-3 py-2.5"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
                    <Building2 size={18} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-text-primary">
                      {u.canonical_name}
                    </p>
                    <p className="truncate text-xs text-text-secondary">
                      {u.coordinator_count} coord · {u.enabled_sector_count} setores · sala vermelha {u.red_capacity}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => goToConfigure(u.id)}
                    aria-label={`Configurar setores de ${u.canonical_name}`}
                    className="flex items-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-xs font-medium text-text-primary transition hover:bg-border/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                  >
                    <Settings2 size={14} /> Setores
                  </button>
                </div>
              ))}
          </div>
        </Section>

        <Section
          title="Coordenadores pendentes"
          subtitle="Toque ✓ pra aprovar · Segure ✗ pra rejeitar"
        >
          <div className="space-y-3">
            {pending.length === 0 && (
              <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
                Nenhum coordenador aguardando aprovação.
              </p>
            )}
            {pending.map((p) => (
              <PendingCard
                key={p.id}
                user={p}
                onApprove={() => approve(p.id)}
                onReject={() => reject(p.id)}
                onConfigureUnit={p.unit_id ? () => goToConfigure(p.unit_id as string) : undefined}
              />
            ))}
          </div>
        </Section>
      </main>
      <ToastViewport />
    </>
  );
}

function UnitsSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-card border border-border bg-card px-3 py-2.5 animate-pulse"
        >
          <div className="h-10 w-10 rounded-pill bg-border/60" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-2/3 rounded bg-border/60" />
            <div className="h-2 w-1/2 rounded bg-border/40" />
          </div>
          <div className="h-7 w-20 rounded-pill bg-border/40" />
        </div>
      ))}
    </div>
  );
}

function InviteCard({
  invite,
  onReset,
}: {
  invite: InviteCreateResponse;
  onReset: () => void;
}) {
  const toast = useToast();
  const inviteUrl =
    typeof window !== 'undefined'
      ? `${window.location.origin}/convite/${invite.token}`
      : `/convite/${invite.token}`;
  const waText = `Convite Giro (coordenador): ${inviteUrl}`;
  const waUrl = `https://wa.me/?text=${encodeURIComponent(waText)}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(inviteUrl);
      toast.success('Link copiado');
    } catch {
      toast.error('Não foi possível copiar');
    }
  };

  return (
    <div className="space-y-3 text-center">
      <div className="mx-auto flex h-44 w-44 items-center justify-center rounded-card border border-border bg-white p-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={qrImageUrl(inviteUrl, 220)} alt="QR do convite" className="h-full w-full" />
      </div>
      <p className="break-all rounded-card bg-surface px-3 py-2 text-xs text-text-secondary">
        {inviteUrl}
      </p>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:bg-border/40"
        >
          <Copy size={14} /> Copiar
        </button>
        <a
          href={waUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-green px-3 py-2 text-sm font-semibold text-white"
        >
          <MessageCircle size={14} /> WhatsApp
        </a>
      </div>
      <button
        type="button"
        onClick={onReset}
        className="text-xs text-text-secondary hover:text-text-primary"
      >
        Gerar outro
      </button>
    </div>
  );
}

function PendingCard({
  user,
  onApprove,
  onReject,
  onConfigureUnit,
}: {
  user: PendingUser;
  onApprove: () => void | Promise<void>;
  onReject: () => void | Promise<void>;
  onConfigureUnit?: () => void;
}) {
  const [rejectProgress, setRejectProgress] = useState(0);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);

  const start = () => {
    firedRef.current = false;
    const startedAt = Date.now();
    const tick = () => {
      const elapsed = Date.now() - startedAt;
      const pct = Math.min(1, elapsed / 500);
      setRejectProgress(pct);
      if (pct >= 1 && !firedRef.current) {
        firedRef.current = true;
        void onReject();
        cancel();
      } else if (rafRef.current !== null) {
        rafRef.current = window.requestAnimationFrame(tick);
      }
    };
    rafRef.current = window.requestAnimationFrame(tick);
  };

  const cancel = () => {
    if (rafRef.current !== null) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setRejectProgress(0);
  };

  return (
    <motion.div
      layout
      className="rounded-card border border-border bg-card p-3"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-accent-blue/10 text-accent-blue">
          <UserRound size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-text-primary">{user.name}</p>
          <p className="truncate text-xs text-text-secondary">
            {user.cargo ?? user.role}
            {user.coren_crm ? ` · ${user.coren_crm}` : ''}
          </p>
          <p className="truncate text-xs text-text-tertiary">CPF {user.cpf_masked}</p>
          {onConfigureUnit && (
            <button
              type="button"
              onClick={onConfigureUnit}
              className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-accent-blue hover:underline"
            >
              <Settings2 size={12} /> Ver setores da UPA
            </button>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => void onApprove()}
            aria-label={`Aprovar ${user.name}`}
            className="flex h-10 w-10 items-center justify-center rounded-pill bg-accent-green/10 text-accent-green transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-green hover:bg-accent-green/20"
          >
            <Check size={18} />
          </button>
          <button
            type="button"
            aria-label={`Rejeitar ${user.name} (segure)`}
            onPointerDown={start}
            onPointerUp={cancel}
            onPointerLeave={cancel}
            onPointerCancel={cancel}
            className="relative flex h-10 w-10 items-center justify-center overflow-hidden rounded-pill bg-accent-red/10 text-accent-red transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-red hover:bg-accent-red/20"
          >
            <AnimatePresence>
              {rejectProgress > 0 && (
                <motion.span
                  key="prog"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="absolute inset-0 bg-accent-red/30"
                  style={{ clipPath: `inset(${(1 - rejectProgress) * 100}% 0 0 0)` }}
                  aria-hidden
                />
              )}
            </AnimatePresence>
            <X size={18} className="relative" />
          </button>
        </div>
      </div>
    </motion.div>
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
    <section className="mt-7 first:mt-2">
      <div className="mb-2.5 px-1">
        <h2 className="text-[22px] font-semibold tracking-tight text-text-primary">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-text-secondary">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}
