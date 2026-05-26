'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { Tablet } from 'lucide-react';
import { CodePad } from '@/components/auth/CodePad';
import { apiFetch, ApiError } from '@/lib/api';
import { getOrCreateDeviceFingerprint } from '@/lib/device';
import { useToast } from '@/lib/toast';
import { ToastViewport } from '@/components/shared/ToastViewport';

export default function PairPage() {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const pair = async (code: string) => {
    setLoading(true);
    setError(null);
    try {
      const fingerprint = getOrCreateDeviceFingerprint();
      await apiFetch('/api/auth/device/pair', {
        method: 'POST',
        body: JSON.stringify({
          pairing_code: code,
          device_fingerprint: fingerprint,
          label:
            typeof navigator !== 'undefined' ? navigator.userAgent.slice(0, 120) : 'tablet',
        }),
      });
      toast.success('Tablet pareado');
      router.push('/shift');
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Falha no pareamento';
      setError(msg);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[520px] flex-col px-4 pb-10 pt-12">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 30 }}
        className="text-center"
      >
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <Tablet size={26} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Parear tablet
        </h1>
        <p className="mx-auto mt-2 max-w-[320px] text-sm text-text-secondary">
          Peça um código de 6 dígitos ao coordenador da UPA e digite abaixo.
        </p>
      </motion.div>

      <div className="mt-8">
        <CodePad
          length={6}
          onSubmit={pair}
          error={error}
          loading={loading}
          autoSubmit={false}
          submitLabel="Parear"
          title="Código de pareamento"
          description="6 dígitos · expira em 10 minutos"
        />
      </div>
      <ToastViewport />
    </main>
  );
}
