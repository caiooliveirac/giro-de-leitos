'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { Menu, Moon, Sun } from 'lucide-react';
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
        className="sticky top-0 z-30 border-b border-line bg-[color-mix(in_oklch,var(--bg)_92%,transparent)] px-5 py-3 backdrop-blur-xl"
      >
        <div className="mx-auto flex max-w-[520px] items-center gap-3">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Abrir menu"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] text-ink-2 transition-transform active:scale-90 active:bg-surface-2"
          >
            <Menu size={20} />
          </button>

          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[19px] font-semibold tracking-tight text-ink">
              {unitName}
            </h1>
            {shiftLabel && (
              <p className="truncate text-xs text-ink-3" style={{ marginTop: 2 }}>
                {shiftLabel}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <div className="live-pill" aria-label="Conexão ao vivo">
              <span className="live-dot" />
              <span>ao vivo</span>
            </div>

            <button
              type="button"
              onClick={toggle}
              aria-label="Alternar tema claro/escuro"
              className="flex h-11 w-11 items-center justify-center rounded-[14px] text-ink-2 transition-transform active:scale-90 active:bg-surface-2"
            >
              {mounted && theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </div>
      </motion.header>

      <NavDrawer open={navOpen} onClose={() => setNavOpen(false)} />
    </>
  );
}
