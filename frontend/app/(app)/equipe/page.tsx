'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Check,
  Copy,
  MessageCircle,
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
import { qrImageUrl } from '@/lib/qr';

interface PendingUser {
  id: string;
  name: string;
  role: string;
  cargo: string | null;
  unit_id: string | null;
  created_at: string;
  cpf_masked: string;
  coren_crm: string | null;
}

interface StaffUser {
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
  const router = useRouter();
  const toast = useToast();
  const { user, hydrated, isCoordinator, isAdmin } = useCurrentUser();

  useEffect(() => {
    if (hydrated && isAdmin) {
      router.replace('/admin');
    }
  }, [hydrated, isAdmin, router]);
  const [pending, setPending] = useState<PendingUser[]>([]);
  const [staff, setStaff] = useState<StaffUser[]>([]);
  const [invite, setInvite] = useState<InviteCreateResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        apiFetch<PendingUser[]>('/api/users/pending'),
        apiFetch<StaffUser[]>('/api/auth/me/unit/staff'),
      ]);
      setPending(p);
      setStaff(s);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao carregar equipe';
      toast.error(msg);
    }
  }, [toast]);

  useEffect(() => {
    if (hydrated && isCoordinator) void load();
  }, [hydrated, isCoordinator, load]);

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
      const msg = err instanceof ApiError ? err.message : 'Falha ao gerar convite';
      toast.error(msg);
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
      const msg = err instanceof ApiError ? err.message : 'Falha ao aprovar';
      toast.error(msg);
    }
  };

  const reject = async (id: string) => {
    try {
      await apiFetch(`/api/users/${id}/reject`, { method: 'POST' });
      toast.warning('Profissional rejeitado');
      setPending((p) => p.filter((u) => u.id !== id));
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha ao rejeitar';
      toast.error(msg);
    }
  };

  if (hydrated && isAdmin) {
    return null;
  }

  if (hydrated && !isCoordinator) {
    return (
      <main className="mx-auto min-h-dvh w-full max-w-[520px] px-4 pt-12 text-center">
        <p className="text-sm text-text-secondary">
          Apenas coordenadores podem acessar essa tela.
        </p>
      </main>
    );
  }

  return (
    <>
      <OfflineBanner />
      <TopBar unitName="Equipe da UPA" shiftLabel={user?.name ?? null} />

      <main className="mx-auto w-full max-w-[520px] px-4 pb-24 pt-4">
        <Section
          title="Convidar profissional"
          subtitle="Gera um link com QR válido por 48h"
        >
          <div className="rounded-card border border-border bg-card p-4">
            {!invite && (
              <button
                type="button"
                onClick={generateInvite}
                disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              >
                <UserPlus size={18} /> {busy ? 'Gerando…' : 'Convidar profissional'}
              </button>
            )}

            {invite && (
              <InviteCard invite={invite} onReset={() => setInvite(null)} />
            )}
          </div>
        </Section>

        <Section
          title="Adicionar aparelho"
          subtitle="Gera um código de 6 dígitos pra parear um tablet desta UPA"
        >
          <PairingCodeBlock unitId={user?.unit_id ?? null} />
        </Section>

        <Section title="Pendentes" subtitle="Toque ✓ para aprovar. Mantenha ✗ pra rejeitar.">
          <div className="space-y-3">
            {pending.length === 0 && (
              <p className="rounded-card border border-border bg-card p-4 text-center text-sm text-text-secondary">
                Nenhum cadastro aguardando aprovação.
              </p>
            )}
            {pending.map((p) => (
              <PendingCard
                key={p.id}
                user={p}
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
              <div
                key={s.id}
                className="flex items-center gap-3 rounded-card border border-border bg-card px-3 py-2.5"
              >
                <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-full border border-border bg-surface">
                  {s.photo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={s.photo_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <UserRound size={20} className="text-text-tertiary" aria-hidden />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-text-primary">{s.name}</p>
                  <p className="truncate text-xs text-text-secondary">{s.cargo ?? s.role}</p>
                </div>
              </div>
            ))}
          </div>
        </Section>
      </main>
      <ToastViewport />
    </>
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
  const waText = `Olá! Você foi convidado para a UPA. Acesse: ${inviteUrl}`;
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
        // ignore — cancelled
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
      <div className="mx-auto flex h-48 w-48 items-center justify-center rounded-card border border-border bg-white p-2">
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
          className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:bg-border/40"
        >
          <Copy size={14} /> Copiar
        </button>
        <a
          href={waUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-green px-3 py-2 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-green"
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

function PendingCard({
  user,
  onApprove,
  onReject,
}: {
  user: PendingUser;
  onApprove: () => void | Promise<void>;
  onReject: () => void | Promise<void>;
}) {
  const [rejectProgress, setRejectProgress] = useState(0);
  const timerRef = useRef<number | null>(null);
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
      } else if (timerRef.current !== null) {
        timerRef.current = window.requestAnimationFrame(tick);
      }
    };
    timerRef.current = window.requestAnimationFrame(tick);
  };

  const cancel = () => {
    if (timerRef.current !== null) {
      window.cancelAnimationFrame(timerRef.current);
      timerRef.current = null;
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
      toast.error('Sua unidade não foi identificada.');
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
      const msg = err instanceof ApiError ? err.message : 'Falha ao gerar código.';
      toast.error(msg);
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
          className="flex w-full items-center justify-center gap-2 rounded-pill bg-accent-blue px-5 py-3 text-base font-semibold text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
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
              className="flex items-center justify-center gap-1.5 rounded-pill border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:bg-border/40"
            >
              <Copy size={14} /> Copiar
            </button>
            <a
              href={waUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-1.5 rounded-pill bg-accent-green px-3 py-2 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-green"
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
