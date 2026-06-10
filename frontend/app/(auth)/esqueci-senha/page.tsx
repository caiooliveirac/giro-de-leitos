'use client';

import { useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { KeyRound } from 'lucide-react';
import { apiFetch, ApiError } from '@/lib/api';
import { ToastViewport } from '@/components/shared/ToastViewport';

export default function EsqueciSenhaPage() {
  const [login, setLogin] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const loginTrim = login.trim();
  const canSubmit = loginTrim.length >= 3 && !loading;

  const submit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    try {
      await apiFetch('/api/auth/password/forgot', {
        method: 'POST',
        body: JSON.stringify({ login: loginTrim }),
      });
      setSent(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Muitas tentativas. Tente novamente em alguns minutos.');
      } else {
        // Anti-enumeração: trate qualquer outra falha como sucesso genérico.
        setSent(true);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto min-h-dvh w-full max-w-[520px] px-5 pb-12 pt-10">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 32 }}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <KeyRound size={22} />
        </div>
        <h1 className="mt-4 text-[28px] font-semibold leading-[1.15] tracking-tight text-ink">
          Esqueci minha senha
        </h1>
        <p className="mt-2 text-[15px] text-ink-2">
          Informe seu CPF, e-mail ou nome de usuário. Enviaremos um link de
          redefinição para o e-mail cadastrado.
        </p>
      </motion.div>

      {sent ? (
        <div className="mt-6 space-y-4">
          <p className="rounded-card border border-accent-blue/30 bg-accent-blue/5 p-4 text-sm text-ink-2">
            Se houver uma conta, enviamos um link para o e-mail cadastrado.
            Verifique sua caixa de entrada (e o spam). O link vale por 1 hora.
          </p>
          <div className="text-center">
            <Link href="/pair" className="back-btn">
              Voltar
            </Link>
          </div>
        </div>
      ) : (
        <div className="mt-6 space-y-4">
          <div className="field">
            <label className="field-label" htmlFor="fp-login">
              CPF, e-mail ou usuário
            </label>
            <input
              id="fp-login"
              type="text"
              inputMode="text"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              autoComplete="username"
              value={login}
              onChange={(e) => {
                setLogin(e.target.value);
                if (error) setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit();
              }}
              className="w-full rounded-[12px] border border-line bg-surface px-3.5 py-3 text-base text-ink placeholder:text-ink-3 focus:border-[var(--accent)] focus:bg-surface-elev focus:outline-none"
              placeholder="seu CPF, e-mail ou nome de usuário"
              disabled={loading}
            />
          </div>

          {error && (
            <p className="rounded-card border border-critical/30 bg-critical-soft p-3 text-sm text-critical-ink">
              {error}
            </p>
          )}

          <button
            type="button"
            onClick={() => void submit()}
            disabled={!canSubmit}
            className="cta mt-2"
          >
            {loading ? 'Enviando…' : 'Enviar link de redefinição'}
          </button>

          <div className="text-center">
            <Link href="/pair" className="back-btn">
              Voltar
            </Link>
          </div>
        </div>
      )}

      <ToastViewport />
    </main>
  );
}
