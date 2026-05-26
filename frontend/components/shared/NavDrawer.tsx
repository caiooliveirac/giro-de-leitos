'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import {
  LayoutDashboard,
  Settings2,
  Users,
  ShieldCheck,
  LogOut,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/lib/toast';

interface NavDrawerProps {
  open: boolean;
  onClose: () => void;
}

interface NavItem {
  href: string;
  label: string;
  description?: string;
  icon: LucideIcon;
  visible: boolean;
}

export function NavDrawer({ open, onClose }: NavDrawerProps) {
  const router = useRouter();
  const toast = useToast();
  const { isAdmin, isCoordinator } = useCurrentUser();

  const items: NavItem[] = [
    {
      href: '/',
      label: 'Operação',
      description: 'Painel ao vivo',
      icon: LayoutDashboard,
      visible: true,
    },
    {
      href: '/configurar',
      label: 'Configurar setores',
      description: 'Ativar e ajustar capacidades',
      icon: Settings2,
      visible: isCoordinator,
    },
    {
      href: '/equipe',
      label: 'Equipe',
      description: 'Convites e aprovações da unidade',
      icon: Users,
      visible: isCoordinator,
    },
    {
      href: '/admin',
      label: 'Admin global',
      description: 'UPAs e coordenadores',
      icon: ShieldCheck,
      visible: isAdmin,
    },
  ];

  const go = (href: string) => {
    onClose();
    router.push(href);
  };

  const endShift = async () => {
    try {
      await apiFetch('/api/auth/shift/end', { method: 'POST', body: JSON.stringify({}) });
    } catch {
      // ignore — backend may already be inactive
    }
    try {
      window.localStorage.removeItem('gl_shift_user');
    } catch {
      // ignore
    }
    toast.success('Plantão encerrado');
    onClose();
    router.push('/shift');
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
            onClick={onClose}
            aria-hidden
          />
          <motion.aside
            key="drawer"
            role="dialog"
            aria-label="Menu de navegação"
            initial={{ x: '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: '-100%' }}
            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
            className="fixed inset-y-0 left-0 z-50 w-[86%] max-w-[360px] border-r border-border bg-surface px-4 pb-6 pt-5 shadow-2xl"
          >
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-text-secondary">Menu</p>
              <button
                type="button"
                onClick={onClose}
                aria-label="Fechar menu"
                className="flex h-9 w-9 items-center justify-center rounded-pill border border-border bg-card text-text-secondary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:text-text-primary"
              >
                <X size={16} />
              </button>
            </div>

            <nav className="mt-5 space-y-2">
              {items
                .filter((i) => i.visible)
                .map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.href}
                      type="button"
                      onClick={() => go(item.href)}
                      className="flex w-full items-center gap-3 rounded-card border border-border bg-card px-4 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue hover:bg-border/30"
                    >
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
                        <Icon size={18} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-text-primary">{item.label}</p>
                        {item.description && (
                          <p className="truncate text-xs text-text-secondary">{item.description}</p>
                        )}
                      </div>
                    </button>
                  );
                })}
            </nav>

            <div className="mt-8 border-t border-border pt-5">
              <button
                type="button"
                onClick={() => void endShift()}
                className="flex w-full items-center gap-3 rounded-card border border-border bg-card px-4 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-red hover:bg-accent-red/10"
              >
                <div className="flex h-9 w-9 items-center justify-center rounded-pill bg-accent-red/10 text-accent-red">
                  <LogOut size={18} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-text-primary">Trocar plantão</p>
                  <p className="truncate text-xs text-text-secondary">Encerra sessão atual</p>
                </div>
              </button>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
