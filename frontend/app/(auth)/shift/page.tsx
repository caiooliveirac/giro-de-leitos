'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { UserRound } from 'lucide-react';
import { apiFetch, ApiError, type StaffMember } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { PinPad } from '@/components/auth/PinPad';
import { ToastViewport } from '@/components/shared/ToastViewport';

function initials(name: string): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

export default function ShiftPage() {
  const router = useRouter();
  const toast = useToast();
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [unitName, setUnitName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pinError, setPinError] = useState<string | null>(null);
  const [showNotFound, setShowNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const rows = await apiFetch<StaffMember[]>('/api/auth/me/unit/staff');
        if (!cancelled) {
          const active = rows.filter((u) => u.status === 'active');
          setStaff(active);
        }
        // Best-effort: tentar pegar nome da unidade do localStorage (gravado em pair)
        try {
          const cached = window.localStorage.getItem('gl_unit_name');
          if (cached && !cancelled) setUnitName(cached);
        } catch {
          /* ignore */
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            router.replace('/pair');
            return;
          }
          setError(err instanceof ApiError ? err.message : 'Falha ao carregar equipe');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const startShift = async (user: StaffMember, pin: string) => {
    setPinError(null);
    try {
      await apiFetch('/api/auth/shift/start', {
        method: 'POST',
        body: JSON.stringify({ user_id: user.id, pin }),
      });
      try {
        window.localStorage.setItem(
          'gl_shift_user',
          JSON.stringify({
            id: user.id,
            name: user.name,
            role: user.role,
            unit_id: user.unit_id,
          }),
        );
      } catch {
        /* ignore */
      }
      toast.success(`Plantão iniciado — ${user.name}`);
      router.push('/');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'PIN incorreto';
      setPinError(msg);
      throw err;
    }
  };

  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-5 pb-12 pt-8">
      <header className="mb-6 flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-3">
            {unitName ?? 'Plantão'}
          </p>
          <h1 className="mt-1 text-[28px] font-semibold leading-[1.15] tracking-tight text-ink">
            Quem está no<br />plantão?
          </h1>
          <p className="mt-2 text-[15px] text-ink-2">Toque no seu nome e digite seu PIN.</p>
        </div>
      </header>

      {loading && (
        <div className="flex flex-col gap-2" aria-label="Carregando equipe">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton-card" />
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-card border border-critical/30 bg-critical-soft p-4 text-sm text-critical-ink">
          {error}
        </div>
      )}

      {!loading && !error && staff.length === 0 && (
        <p className="text-center text-sm text-ink-2">
          Ninguém da equipe disponível. Peça ao coordenador pra aprovar profissionais.
        </p>
      )}

      <div className="flex flex-col gap-2">
        {staff.map((user) => {
          const isSelected = selectedId === user.id;
          return (
            <motion.div
              layout
              key={user.id}
              transition={{ type: 'spring', stiffness: 320, damping: 32 }}
              className="person-card"
              data-expanded={isSelected}
              style={{ display: 'block', padding: 0 }}
            >
              <button
                type="button"
                onClick={() => {
                  setPinError(null);
                  setSelectedId((prev) => (prev === user.id ? null : user.id));
                }}
                aria-expanded={isSelected}
                aria-label={`${user.name}${user.cargo ? ', ' + user.cargo : ''}`}
                className="flex w-full items-center gap-3.5 p-3.5 text-left focus-visible:outline-none"
              >
                <div className="person-avatar" aria-hidden>
                  {user.photo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={user.photo_url} alt="" />
                  ) : (
                    <span>{initials(user.name)}</span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="name truncate">{user.name}</div>
                  {(user.cargo || user.role) && (
                    <div className="role truncate">{user.cargo ?? user.role}</div>
                  )}
                </div>
              </button>

              <AnimatePresence initial={false}>
                {isSelected && (
                  <motion.div
                    key="pin"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ type: 'spring', stiffness: 320, damping: 32 }}
                    className="overflow-hidden"
                  >
                    <div className="border-t border-line px-3 pb-4 pt-3">
                      <p className="mb-2 text-center text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">
                        PIN de {user.name.split(' ')[0]} · 4 dígitos
                      </p>
                      <PinPad
                        length={4}
                        title=""
                        compact
                        error={pinError}
                        onSubmit={(pin) => startShift(user, pin)}
                        onCancel={() => setSelectedId(null)}
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          );
        })}
      </div>

      {!loading && !error && (
        <div className="mt-6 text-center">
          <button
            type="button"
            onClick={() => setShowNotFound((v) => !v)}
            className="text-[13px] text-ink-3 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            Não encontro meu nome
          </button>
          <AnimatePresence>
            {showNotFound && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ type: 'spring', stiffness: 320, damping: 30 }}
                className="overflow-hidden"
              >
                <p className="mx-auto mt-3 max-w-[360px] rounded-card border border-line bg-surface p-3 text-left text-[13px] leading-relaxed text-ink-2">
                  Seu coordenador precisa te cadastrar e aprovar primeiro. Peça pra ele te
                  enviar um convite ou liberar seu acesso e atualize esta tela.
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <ToastViewport />
    </main>
  );
}
