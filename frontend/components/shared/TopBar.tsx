'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { Building2, Menu, Moon, Sun } from 'lucide-react';
import { useTheme } from '@/lib/theme';
import { NavDrawer } from '@/components/shared/NavDrawer';

interface TopBarProps {
  unitName: string;
  shiftLabel?: string | null;
}

export function TopBar({ unitName, shiftLabel }: TopBarProps) {
  const { theme, toggle, mounted } = useTheme();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <>
      <motion.header
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 320, damping: 32 }}
        className="sticky top-0 z-40 border-b border-border/60 bg-surface/80 px-5 py-3 backdrop-blur-xl supports-[backdrop-filter]:bg-surface/60"
      >
        <div className="mx-auto flex max-w-[520px] items-center gap-3">
          <motion.button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Abrir menu"
            whileTap={{ scale: 0.92 }}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill border border-border bg-card text-text-secondary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface hover:text-text-primary"
          >
            <Menu size={16} />
          </motion.button>

          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
            <Building2 size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-semibold tracking-tight text-text-primary">
              {unitName}
            </h1>
            {shiftLabel && (
              <p className="truncate text-xs text-text-secondary">Plantão: {shiftLabel}</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <div
              className="flex items-center gap-1.5 rounded-pill bg-accent-green/10 px-2.5 py-1 text-[11px] font-medium text-accent-green"
              aria-label="Conexão ao vivo"
            >
              <motion.span
                className="h-1.5 w-1.5 rounded-full bg-accent-green"
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
              />
              <span>ao vivo</span>
            </div>

            <motion.button
              type="button"
              onClick={toggle}
              aria-label="Alternar tema claro/escuro"
              whileTap={{ scale: 0.92 }}
              className="flex h-9 w-9 items-center justify-center rounded-pill border border-border bg-card text-text-secondary transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-surface hover:text-text-primary"
            >
              {mounted && theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </motion.button>
          </div>
        </div>
      </motion.header>

      <NavDrawer open={navOpen} onClose={() => setNavOpen(false)} />
    </>
  );
}
