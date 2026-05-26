'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { UserRound } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { PinPad } from '@/components/auth/PinPad';
import { ToastViewport } from '@/components/shared/ToastViewport';

interface StaffUser {
  id: string;
  name: string;
  role: string;
  cargo: string | null;
  photo_url: string | null;
  status: string;
  unit_id: string | null;
  cpf_masked: string;
}

export default function ShiftPage() {
  const router = useRouter();
  const toast = useToast();
  const [staff, setStaff] = useState<StaffUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pinError, setPinError] = useState<string | null>(null);
  const [hasSession, setHasSession] = useState(false);

  useEffect(() => {
    try {
      setHasSession(Boolean(window.localStorage.getItem('gl_shift_user')));
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const rows = await apiFetch<StaffUser[]>('/api/auth/me/unit/staff');
        if (!cancelled) setStaff(rows.filter((u) => u.status === 'active'));
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

  const startShift = async (user: StaffUser, pin: string) => {
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
        // ignore
      }
      toast.success(`Plantão iniciado — ${user.name}`);
      router.push('/');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'PIN incorreto';
      setPinError(msg);
      throw err;
    }
  };

  const endShift = async () => {
    try {
      await apiFetch('/api/auth/shift/end', {
        method: 'POST',
        body: JSON.stringify({}),
      });
    } catch {
      // ignore — clear local anyway
    }
    try {
      window.localStorage.removeItem('gl_shift_user');
    } catch {
      // ignore
    }
    setHasSession(false);
    toast.success('Plantão encerrado');
  };

  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-4 pb-10 pt-8">
      <header className="mb-5 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
            Iniciar plantão
          </p>
          <h1 className="mt-0.5 text-2xl font-semibold tracking-tight text-text-primary">
            Escolha o profissional
          </h1>
        </div>
        {hasSession && (
          <button
            type="button"
            onClick={() => void endShift()}
            className="rounded-pill border border-border bg-card px-3 py-1.5 text-xs font-medium text-text-secondary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:text-text-primary"
          >
            Trocar plantão
          </button>
        )}
      </header>

      {loading && (
        <p className="text-center text-sm text-text-secondary">Carregando equipe…</p>
      )}

      {error && (
        <div className="rounded-card border border-accent-red/30 bg-accent-red/5 p-4 text-sm text-accent-red">
          {error}
        </div>
      )}

      {!loading && !error && staff.length === 0 && (
        <p className="text-center text-sm text-text-secondary">
          Ninguém da equipe disponível. Peça ao coordenador pra aprovar profissionais.
        </p>
      )}

      <LayoutGroup>
        <div className="grid grid-cols-2 gap-3">
          {staff.map((user) => {
            const isSelected = selectedId === user.id;
            return (
              <motion.button
                key={user.id}
                layout
                type="button"
                whileTap={{ scale: 0.97 }}
                onClick={() => {
                  setPinError(null);
                  setSelectedId((prev) => (prev === user.id ? null : user.id));
                }}
                className={`col-span-${isSelected ? '2' : '1'} flex flex-col items-center gap-2 rounded-card border bg-card p-4 text-center transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
                  isSelected ? 'border-accent-blue' : 'border-border'
                }`}
                style={isSelected ? { gridColumn: 'span 2 / span 2' } : undefined}
                aria-pressed={isSelected}
              >
                <div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-full border border-border bg-surface">
                  {user.photo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={user.photo_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <UserRound size={28} className="text-text-tertiary" aria-hidden />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-text-primary">{user.name}</p>
                  {user.cargo && (
                    <p className="truncate text-xs text-text-secondary">{user.cargo}</p>
                  )}
                </div>
                <AnimatePresence>
                  {isSelected && (
                    <motion.div
                      key="pinpad"
                      layout
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ type: 'spring', stiffness: 320, damping: 30 }}
                      className="w-full overflow-hidden"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="mt-3" onClick={(e) => e.stopPropagation()}>
                        <PinPad
                          length={4}
                          title="Confirme seu PIN"
                          description={`Iniciando plantão como ${user.name}`}
                          error={pinError}
                          onSubmit={(pin) => startShift(user, pin)}
                          onCancel={() => setSelectedId(null)}
                        />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.button>
            );
          })}
        </div>
      </LayoutGroup>
      <ToastViewport />
    </main>
  );
}
