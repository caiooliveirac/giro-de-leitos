'use client';

import * as Toast from '@radix-ui/react-toast';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle2, AlertTriangle, Info, XCircle } from 'lucide-react';
import { useToastStore, type ToastVariant } from '@/lib/toast';

function variantStyles(variant: ToastVariant): { bg: string; icon: JSX.Element } {
  switch (variant) {
    case 'success':
      return {
        bg: 'border-accent-green/40',
        icon: <CheckCircle2 size={18} className="text-accent-green" />,
      };
    case 'error':
      return {
        bg: 'border-accent-red/40',
        icon: <XCircle size={18} className="text-accent-red" />,
      };
    case 'warning':
      return {
        bg: 'border-accent-amber/40',
        icon: <AlertTriangle size={18} className="text-accent-amber" />,
      };
    default:
      return {
        bg: 'border-border',
        icon: <Info size={18} className="text-accent-blue" />,
      };
  }
}

export function ToastViewport() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <Toast.Viewport
      className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] left-1/2 z-[100] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2 outline-none"
    >
      <AnimatePresence>
        {toasts.map((t) => {
          const { bg, icon } = variantStyles(t.variant);
          return (
            <Toast.Root
              key={t.id}
              asChild
              open
              duration={3200}
              onOpenChange={(open) => {
                if (!open) dismiss(t.id);
              }}
            >
              <motion.div
                layout
                initial={{ opacity: 0, y: 24, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 12, scale: 0.96 }}
                transition={{ type: 'spring', stiffness: 380, damping: 32 }}
                className={`flex items-center gap-3 rounded-card border bg-card/95 px-4 py-3 text-sm shadow-card backdrop-blur ${bg}`}
              >
                <span className="shrink-0">{icon}</span>
                <Toast.Description className="flex-1 text-text-primary">
                  {t.message}
                </Toast.Description>
              </motion.div>
            </Toast.Root>
          );
        })}
      </AnimatePresence>
    </Toast.Viewport>
  );
}
