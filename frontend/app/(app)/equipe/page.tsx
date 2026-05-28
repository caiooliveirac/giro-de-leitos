'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Check,
  Copy,
  MessageCircle,
  Pause,
  Share2,
  UserPlus,
  UserRound,
  X,
} from 'lucide-react';
import { apiFetch, ApiError, type PairingCodeResponse } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { TopBar } from '@/components/shared/TopBar';
import { OfflineBanner } from '@/components/shared/OfflineBanner';
import { ToastViewport } from '@/components/shared/ToastViewport';
import { UnitPicker } from '@/components/admin/UnitPicker';
import { qrImageUrl } from '@/lib/qr';

const ADMIN_VIEW_KEY = 'gl_admin_viewing_unit';

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

interface UnitMember {
  id: string;
  name: string;
  role: 'admin' | 'coordinator' | 'professional';
  status: 'pending' | 'active' | 'suspended';
  cargo: string | null;
  coren_crm: string | null;
  phone: string | null;
  photo_url: string | null;
  cpf_masked: string;
  created_at: string;
  approved_at: string | null;
}

interface CoordPendingUser {
  id: string;
  name: string;
  role: string;
  cargo: string | null;
  unit_id: string | null;
  created_at: string;
  cpf_masked: string;
  coren_crm: string | null;
}

interface CoordStaffUser {
  id: string;
  name: string;
  role: string;
  cargo: string | null;
  photo_url: string | null;
  status: string;
}

interface InviteCreateResponse {
  id: string;
  token: string;
  type: string;
  target_unit_id: string | null;
  expires_at: string;
}

export default function EquipePage() {
  const { user, hydrated, isAdmin, isCoordinator } = useCurrentUser();

  if (!hydrated) return null;

  if (isAdmin) return <AdminEquipe userName={user?.name ?? null} />;
  if (isCoordinator) return <CoordinatorEquipe userName={user?.name ?? null} unitId={user?.unit_id ?? null} />;
  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-4 pt-12 text-center">
      <p className="text-sm text-text-secondary">
        Apenas coordenadores e admins podem acessar essa tela.
      </p>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Admin: picker de UPA + gerenciamento completo da unidade selecionada
// ---------------------------------------------------------------------------
function AdminEquipe({ userName }: { userName: string | null }) {
  const toast = useToast();
  const [units, setUnits] = useState<AdminUnit[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [selectedUnit, setSelectedUnit] = useState<string>('');
  const [members, setMembers] = useState<UnitMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [invite, setInvite] = useState<InviteCreateResponse | null>(null);
  const [busyInvite, setBusyInvite] = useState(false);
  const [inviteType, setInviteType] = useState<'professional' | 'coordinator'>('professional');

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
      toast.error(err instanceof ApiError ? err.message : 'Falha ao carregar UPAs');
    } finally {
      setUnitsLoading(false);
    }
  }, [toast]);

  const loadMembers = useCallback(
    async (unitId: string) => {
      if (!unitId) {
        setMembers([]);
        return;
      }
      setMembersLoading(true);
      try {
        const rows = await apiFetch<UnitMember[]>(`/api/admin/units/${unitId}/users`);
        setMembers(rows);
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : 'Falha ao carregar membros');
      } finally {
        setMembersLoading(false);
      }
    },
    [toast],
  );

  useEffect(() => {
    void loadUnits();
  }, [loadUnits]);

  useEffect(() => {
    void loadMembers(selectedUnit);
    setInvite(null);
  }, [selectedUnit, loadMembers]);

  const onPick = (id: string) => {
    setSelectedUnit(id);
    try {
      window.localStorage.setItem(ADMIN_VIEW_KEY, id);
    } catch {
      /* ignore */
    }
  };

  const generateInvite = async () => {
    if (!selectedUnit) {
      toast.error('Escolha uma UPA primeiro');
      return;
    }
    setBusyInvite(true);
    try {
      const res = await apiFetch<InviteCreateResponse>('/api/invites', {
        method: 'POST',
        body: JSON.stringify({ type: inviteType, target_unit_id: selectedUnit }),
      });
      setInvite(res);
      toast.success(`Convite ${inviteType === 'coordinator' ? 'de coordenador' : 'de profissional'} gerado`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao gerar convite');
    } finally {
      setBusyInvite(false);
    }
  };

  const approve = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/approve`, { method: 'POST' });
      toast.success('Cadastro aprovado');
      void loadMembers(selectedUnit);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao aprovar');
    }
  };

  const reject = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/reject`, { method: 'POST' });
      toast.warning('Cadastro rejeitado');
      void loadMembers(selectedUnit);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao rejeitar');
    }
  };

  const suspend = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/suspend`, { method: 'POST' });
      toast.warning('Acesso suspenso');
      void loadMembers(selectedUnit);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao suspender');
    }
  };

  const reactivate = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/approve`, { method: 'POST' });
      toast.success('Acesso reativado');
      void loadMembers(selectedUnit);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao reativar');
    }
  };

  const grouped = useMemo(() => groupMembers(members), [members]);
  const selectedUnitName =
    units.find((u) => u.id === selectedUnit)?.canonical_name ?? 'Equipe';

  return (
    <>
      <OfflineBanner />
      <TopBar unitName={selectedUnitName} shiftLabel={userName ? `Admin · ${userName}` : 'Admin'} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <section className="mt-2">
          <div className="mb-2 px-1">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
              UPA em gestão
            </h2>
          </div>
          <UnitPicker
            units={units}
            value={selectedUnit}
            onChange={onPick}
            loading={unitsLoading}
          />
        </section>

        {!selectedUnit && !unitsLoading && (
          <p className="mt-8 rounded-card border border-line bg-surface p-4 text-center text-sm text-ink-2">
            Nenhuma UPA cadastrada.
          </p>
        )}

        {selectedUnit && (
          <>
            <Section
              title="Convidar membro"
              subtitle="Link com QR válido por 7 dias"
            >
              <div className="rounded-card border border-border bg-card p-4 space-y-3">
                <div className="grid grid-cols-2 gap-1.5 rounded-[14px] border border-border bg-surface p-1">
                  {(['professional', 'coordinator'] as const).map((t) => {
                    const on = inviteType === t;
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => {
                          setInviteType(t);
                          setInvite(null);
                        }}
                        className={`rounded-[11px] px-2 py-2.5 text-sm font-medium transition ${
                          on
                            ? 'bg-card text-text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08),0_0_0_1px_var(--line-strong)]'
                            : 'text-text-secondary'
                        }`}
                      >
                        {t === 'professional' ? 'Profissional' : 'Coordenador'}
                      </button>
                    );
                  })}
                </div>

                {!invite && (
                  <button
                    type="button"
                    onClick={generateInvite}
                    disabled={busyInvite}
                    className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                  >
                    <UserPlus size={18} /> {busyInvite ? 'Gerando…' : 'Gerar convite'}
                  </button>
                )}

                {invite && <InviteCard invite={invite} onReset={() => setInvite(null)} />}
              </div>
            </Section>

            <Section
              title="Adicionar aparelho"
              subtitle="Código de 6 dígitos para parear um tablet desta UPA"
            >
              <PairingCodeBlock unitId={selectedUnit} />
            </Section>

            <Section title="Pendentes" subtitle="Aguardando aprovação">
              <MemberList
                loading={membersLoading}
                members={grouped.pending}
                emptyText="Nenhum cadastro aguardando aprovação."
                actions={(m) => (
                  <>
                    <ApproveButton onApprove={() => approve(m.id)} />
                    <RejectButton onReject={() => reject(m.id)} />
                  </>
                )}
              />
            </Section>

            <Section title="Ativos" subtitle={`${grouped.active.length} membro(s) com acesso`}>
              <MemberList
                loading={membersLoading}
                members={grouped.active}
                emptyText="Nenhum membro ativo."
                actions={(m) => (
                  <SuspendButton onSuspend={() => suspend(m.id)} label={`Suspender ${m.name}`} />
                )}
              />
            </Section>

            {grouped.suspended.length > 0 && (
              <Section title="Suspensos" subtitle="Sem acesso atualmente">
                <MemberList
                  loading={false}
                  members={grouped.suspended}
                  emptyText=""
                  actions={(m) => (
                    <ApproveButton
                      onApprove={() => reactivate(m.id)}
                      label={`Reativar ${m.name}`}
                    />
                  )}
                />
              </Section>
            )}
          </>
        )}
      </main>
      <ToastViewport />
    </>
  );
}

// ---------------------------------------------------------------------------
// Coordinator: equipe da própria UPA (estrutura original)
// ---------------------------------------------------------------------------
function CoordinatorEquipe({
  userName,
  unitId,
}: {
  userName: string | null;
  unitId: string | null;
}) {
  const toast = useToast();
  const [pending, setPending] = useState<CoordPendingUser[]>([]);
  const [staff, setStaff] = useState<CoordStaffUser[]>([]);
  const [invite, setInvite] = useState<InviteCreateResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        apiFetch<CoordPendingUser[]>('/api/users/pending'),
        apiFetch<CoordStaffUser[]>('/api/auth/me/unit/staff'),
      ]);
      setPending(p);
      setStaff(s);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao carregar equipe');
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const generateInvite = async () => {
    setBusy(true);
    try {
      const res = await apiFetch<InviteCreateResponse>('/api/invites', {
        method: 'POST',
        body: JSON.stringify({ type: 'professional' }),
      });
      setInvite(res);
      toast.success('Convite gerado');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao gerar convite');
    } finally {
      setBusy(false);
    }
  };

  const approve = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/approve`, { method: 'POST' });
      toast.success('Profissional aprovado');
      setPending((p) => p.filter((u) => u.id !== id));
      void load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao aprovar');
    }
  };

  const reject = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/reject`, { method: 'POST' });
      toast.warning('Profissional rejeitado');
      setPending((p) => p.filter((u) => u.id !== id));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao rejeitar');
    }
  };

  return (
    <>
      <OfflineBanner />
      <TopBar unitName="Equipe da UPA" shiftLabel={userName} />
      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <Section title="Convidar profissional" subtitle="Link com QR válido por 7 dias">
          <div className="rounded-card border border-border bg-card p-4">
            {!invite && (
              <button
                type="button"
                onClick={generateInvite}
                disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50"
              >
                <UserPlus size={18} /> {busy ? 'Gerando…' : 'Convidar profissional'}
              </button>
            )}
            {invite && <InviteCard invite={invite} onReset={() => setInvite(null)} />}
          </div>
        </Section>

        <Section
          title="Adicionar aparelho"
          subtitle="Gera código de 6 dígitos para parear um tablet"
        >
          <PairingCodeBlock unitId={unitId} />
        </Section>

        <Section title="Pendentes" subtitle="Toque ✓ pra aprovar. Segure ✗ pra rejeitar.">
          <div className="space-y-3">
            {pending.length === 0 && (
              <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
                Nenhum cadastro aguardando aprovação.
              </p>
            )}
            {pending.map((p) => (
              <PendingRow
                key={p.id}
                name={p.name}
                role={p.cargo ?? p.role}
                subtitle={p.coren_crm ? `${p.cargo ?? p.role} · ${p.coren_crm}` : (p.cargo ?? p.role)}
                cpf={p.cpf_masked}
                photoUrl={null}
                onApprove={() => approve(p.id)}
                onReject={() => reject(p.id)}
              />
            ))}
          </div>
        </Section>

        <Section title="Equipe ativa" subtitle="Profissionais aprovados na unidade">
          <div className="space-y-2">
            {staff.length === 0 && (
              <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
                Nenhum profissional ativo ainda.
              </p>
            )}
            {staff.map((s) => (
              <ActiveRow
                key={s.id}
                name={s.name}
                subtitle={s.cargo ?? s.role}
                photoUrl={s.photo_url}
              />
            ))}
          </div>
        </Section>
      </main>
      <ToastViewport />
    </>
  );
}

// ---------------------------------------------------------------------------
// Subcomponentes compartilhados
// ---------------------------------------------------------------------------

function groupMembers(members: UnitMember[]) {
  const pending: UnitMember[] = [];
  const active: UnitMember[] = [];
  const suspended: UnitMember[] = [];
  for (const m of members) {
    if (m.status === 'pending') pending.push(m);
    else if (m.status === 'active') active.push(m);
    else if (m.status === 'suspended') suspended.push(m);
  }
  return { pending, active, suspended };
}

function MemberList({
  loading,
  members,
  emptyText,
  actions,
}: {
  loading: boolean;
  members: UnitMember[];
  emptyText: string;
  actions: (m: UnitMember) => React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="space-y-2" aria-label="Carregando membros">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton-card h-16" />
        ))}
      </div>
    );
  }
  if (members.length === 0) {
    if (!emptyText) return null;
    return (
      <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
        {emptyText}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {members.map((m) => (
        <motion.div
          key={m.id}
          layout
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-card border border-border bg-card p-3"
        >
          <div className="flex items-center gap-3">
            <Avatar name={m.name} photoUrl={m.photo_url} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-text-primary">
                {m.name}{' '}
                <RoleBadge role={m.role} />
              </p>
              <p className="truncate text-xs text-text-secondary">
                {m.cargo ?? '—'}
                {m.coren_crm ? ` · ${m.coren_crm}` : ''}
              </p>
              <p className="truncate text-[11px] text-text-tertiary">
                CPF {m.cpf_masked}
                {m.phone ? ` · ${m.phone}` : ''}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">{actions(m)}</div>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

function RoleBadge({ role }: { role: string }) {
  const meta =
    role === 'coordinator'
      ? { label: 'coord', cls: 'bg-accent-blue/10 text-accent-blue' }
      : role === 'admin'
        ? { label: 'admin', cls: 'bg-accent-blue/10 text-accent-blue' }
        : { label: 'prof', cls: 'bg-surface-2 text-ink-2' };
  return (
    <span
      className={`ml-1 inline-block rounded-full px-1.5 py-0.5 align-middle text-[10px] font-medium uppercase tracking-wide ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

function Avatar({ name, photoUrl }: { name: string; photoUrl: string | null }) {
  const initials = name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('') || '?';
  return (
    <div className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-surface text-xs font-semibold text-text-secondary">
      {photoUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={photoUrl} alt={`Foto de ${name}`} className="h-full w-full object-cover" />
      ) : (
        <span>{initials}</span>
      )}
    </div>
  );
}

function ApproveButton({
  onApprove,
  label,
}: {
  onApprove: () => void | Promise<void>;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => void onApprove()}
      aria-label={label ?? 'Aprovar'}
      className="flex h-10 w-10 items-center justify-center rounded-pill bg-accent-green/10 text-accent-green transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-green hover:bg-accent-green/20"
    >
      <Check size={18} />
    </button>
  );
}

function RejectButton({ onReject }: { onReject: () => void | Promise<void> }) {
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);

  const start = () => {
    firedRef.current = false;
    const startedAt = Date.now();
    const tick = () => {
      const elapsed = Date.now() - startedAt;
      const pct = Math.min(1, elapsed / 500);
      setProgress(pct);
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
    setProgress(0);
  };

  return (
    <button
      type="button"
      aria-label="Rejeitar (segure)"
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      className="relative flex h-10 w-10 items-center justify-center overflow-hidden rounded-pill bg-accent-red/10 text-accent-red transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-red hover:bg-accent-red/20"
    >
      <AnimatePresence>
        {progress > 0 && (
          <motion.span
            key="prog"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-accent-red/30"
            style={{ clipPath: `inset(${(1 - progress) * 100}% 0 0 0)` }}
            aria-hidden
          />
        )}
      </AnimatePresence>
      <X size={18} className="relative" />
    </button>
  );
}

function SuspendButton({
  onSuspend,
  label,
}: {
  onSuspend: () => void | Promise<void>;
  label: string;
}) {
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);

  const start = () => {
    firedRef.current = false;
    const startedAt = Date.now();
    const tick = () => {
      const elapsed = Date.now() - startedAt;
      const pct = Math.min(1, elapsed / 700);
      setProgress(pct);
      if (pct >= 1 && !firedRef.current) {
        firedRef.current = true;
        void onSuspend();
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
    setProgress(0);
  };

  return (
    <button
      type="button"
      aria-label={`${label} (segure)`}
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      className="relative flex h-10 w-10 items-center justify-center overflow-hidden rounded-pill bg-warning-soft text-warning-ink transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning hover:bg-warning-soft/80"
    >
      <AnimatePresence>
        {progress > 0 && (
          <motion.span
            key="prog"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-warning/30"
            style={{ clipPath: `inset(${(1 - progress) * 100}% 0 0 0)` }}
            aria-hidden
          />
        )}
      </AnimatePresence>
      <Pause size={16} className="relative" />
    </button>
  );
}

function PendingRow({
  name,
  subtitle,
  cpf,
  photoUrl,
  onApprove,
  onReject,
}: {
  name: string;
  role: string;
  subtitle: string;
  cpf: string;
  photoUrl: string | null;
  onApprove: () => void | Promise<void>;
  onReject: () => void | Promise<void>;
}) {
  return (
    <div className="rounded-card border border-border bg-card p-3">
      <div className="flex items-center gap-3">
        <Avatar name={name} photoUrl={photoUrl} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-text-primary">{name}</p>
          <p className="truncate text-xs text-text-secondary">{subtitle}</p>
          <p className="truncate text-[11px] text-text-tertiary">CPF {cpf}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ApproveButton onApprove={onApprove} label={`Aprovar ${name}`} />
          <RejectButton onReject={onReject} />
        </div>
      </div>
    </div>
  );
}

function ActiveRow({
  name,
  subtitle,
  photoUrl,
}: {
  name: string;
  subtitle: string;
  photoUrl: string | null;
}) {
  return (
    <div className="flex items-center gap-3 rounded-card border border-border bg-card px-3 py-2.5">
      <Avatar name={name} photoUrl={photoUrl} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text-primary">{name}</p>
        <p className="truncate text-xs text-text-secondary">{subtitle}</p>
      </div>
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
  const waText = `Convite Giro: ${inviteUrl}`;
  const waUrl = `https://wa.me/?text=${encodeURIComponent(waText)}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(inviteUrl);
      toast.success('Link copiado');
    } catch {
      toast.error('Não foi possível copiar');
    }
  };

  const share = async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: 'Convite Giro', text: waText, url: inviteUrl });
      } catch {
        /* cancelled */
      }
    } else {
      window.open(waUrl, '_blank', 'noopener,noreferrer');
    }
  };

  return (
    <div className="space-y-3 text-center">
      <p className="text-xs text-text-secondary">
        Mostre o QR ou envie o link. Expira em{' '}
        {new Date(invite.expires_at).toLocaleString('pt-BR')}.
      </p>
      <div className="mx-auto flex h-44 w-44 items-center justify-center rounded-card border border-border bg-white p-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={qrImageUrl(inviteUrl, 240)} alt="QR do convite" className="h-full w-full" />
      </div>
      <p className="break-all rounded-card bg-surface px-3 py-2 text-xs text-text-secondary">
        {inviteUrl}
      </p>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition hover:bg-border/40"
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
      <div className="flex items-center justify-center gap-3 pt-1">
        <button
          type="button"
          onClick={() => void share()}
          className="flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary"
        >
          <Share2 size={14} /> Compartilhar
        </button>
        <button
          type="button"
          onClick={onReset}
          className="text-xs text-text-secondary hover:text-text-primary"
        >
          Gerar outro
        </button>
      </div>
    </div>
  );
}

function PairingCodeBlock({ unitId }: { unitId: string | null }) {
  const toast = useToast();
  const [code, setCode] = useState<PairingCodeResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!code) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [code]);

  useEffect(() => {
    setCode(null);
  }, [unitId]);

  const expiresMs = code ? new Date(code.expires_at).getTime() : 0;
  const remainingSec = code ? Math.max(0, Math.floor((expiresMs - now) / 1000)) : 0;
  const expired = code != null && remainingSec === 0;

  useEffect(() => {
    if (expired) setCode(null);
  }, [expired]);

  const mmss = (() => {
    const m = Math.floor(remainingSec / 60);
    const s = remainingSec % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  })();

  const generate = async () => {
    if (!unitId) {
      toast.error('Escolha uma UPA primeiro.');
      return;
    }
    setBusy(true);
    try {
      const res = await apiFetch<PairingCodeResponse>(
        '/api/auth/device/generate-code',
        {
          method: 'POST',
          body: JSON.stringify({ unit_id: unitId }),
        },
      );
      setCode(res);
      setNow(Date.now());
      toast.success('Código gerado · válido por 10 min');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Falha ao gerar código');
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.pairing_code);
      toast.success('Código copiado');
    } catch {
      toast.error('Não foi possível copiar');
    }
  };

  const waUrl = code
    ? `https://wa.me/?text=${encodeURIComponent(
        `Código de pareamento: ${code.pairing_code} (válido por 10min)`,
      )}`
    : '#';

  return (
    <div className="rounded-card border border-border bg-card p-4">
      {!code && (
        <button
          type="button"
          onClick={() => void generate()}
          disabled={busy || !unitId}
          className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50"
        >
          {busy ? 'Gerando…' : 'Gerar código de pareamento'}
        </button>
      )}

      {code && (
        <div className="space-y-3 text-center">
          <p className="text-xs text-text-secondary">
            Digite este código no aparelho a ser pareado.
          </p>
          <div className="tnum text-[40px] font-semibold tracking-[0.32em] text-text-primary">
            {code.pairing_code}
          </div>
          <p className="text-[11px] uppercase tracking-wider text-text-tertiary">
            expira em <span className="tnum text-text-secondary">{mmss}</span>
          </p>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition hover:bg-border/40"
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
            onClick={() => setCode(null)}
            className="text-xs text-text-secondary hover:text-text-primary"
          >
            Gerar outro código
          </button>
        </div>
      )}
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
    <section className="mt-7 first:mt-2">
      <div className="mb-2.5 px-1">
        <h2 className="text-[22px] font-semibold tracking-tight text-text-primary">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-text-secondary">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}
