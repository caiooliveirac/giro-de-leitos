'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Building2, ChevronDown, Search, X } from 'lucide-react';

export interface UnitPickerOption {
  id: string;
  canonical_name: string;
  coordinator_count: number;
  enabled_sector_count: number;
}

interface UnitPickerProps {
  units: UnitPickerOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
  emptyLabel?: string;
  placeholder?: string;
}

export function UnitPicker({
  units,
  value,
  onChange,
  disabled = false,
  loading = false,
  emptyLabel = 'Nenhuma UPA cadastrada',
  placeholder = 'Selecione uma UPA',
}: UnitPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const selected = units.find((u) => u.id === value) ?? null;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return units;
    return units.filter((u) => u.canonical_name.toLowerCase().includes(q));
  }, [units, query]);

  // Focus the search input when opening, without yanking scroll.
  useEffect(() => {
    if (open) {
      const t = window.setTimeout(() => {
        searchInputRef.current?.focus({ preventScroll: true });
      }, 60);
      return () => window.clearTimeout(t);
    } else {
      setQuery('');
    }
  }, [open]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDocPointer = (e: PointerEvent) => {
      const root = containerRef.current;
      if (root && !root.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onDocPointer);
    return () => document.removeEventListener('pointerdown', onDocPointer);
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const headerLabel = selected ? selected.canonical_name : placeholder;
  const headerSubtitle = selected
    ? `${selected.coordinator_count} coord · ${selected.enabled_sector_count} setores`
    : loading
      ? 'Carregando UPAs…'
      : units.length === 0
        ? emptyLabel
        : 'Toque para escolher';

  const handlePick = (id: string) => {
    onChange(id);
    setOpen(false);
  };

  const noResults = filtered.length === 0;
  const isDisabled = disabled || loading || units.length === 0;

  return (
    <div ref={containerRef} className="w-full">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={isDisabled}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 rounded-xl border border-border bg-surface px-3.5 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/30 disabled:opacity-50"
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-pill bg-accent-blue/10 text-accent-blue">
          <Building2 size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-text-primary">{headerLabel}</p>
          <p className="truncate text-xs text-text-secondary">{headerSubtitle}</p>
        </div>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          className="text-text-secondary"
          aria-hidden
        >
          <ChevronDown size={18} />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="picker-panel"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 32 }}
            className="overflow-hidden"
          >
            <div className="mt-2 rounded-xl border border-border bg-card">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                <Search size={14} className="shrink-0 text-text-tertiary" aria-hidden />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Buscar UPA…"
                  aria-label="Buscar UPA"
                  className="min-w-0 flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => {
                      setQuery('');
                      searchInputRef.current?.focus({ preventScroll: true });
                    }}
                    aria-label="Limpar busca"
                    className="text-text-tertiary hover:text-text-secondary"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>

              <ul
                ref={listRef}
                role="listbox"
                aria-label="UPAs"
                tabIndex={-1}
                className="max-h-[320px] overflow-y-auto overscroll-contain py-1"
              >
                {noResults && (
                  <li className="px-3 py-4 text-center text-xs text-text-secondary">
                    Nenhuma UPA encontrada.
                  </li>
                )}
                {filtered.map((u) => {
                  const isSelected = u.id === value;
                  return (
                    <li
                      key={u.id}
                      role="option"
                      aria-selected={isSelected}
                    >
                      <button
                        type="button"
                        onClick={() => handlePick(u.id)}
                        className={`flex w-full items-center gap-3 px-3 py-2.5 text-left transition focus-visible:outline-none focus-visible:bg-border/30 hover:bg-border/30 ${
                          isSelected ? 'bg-accent-blue/5' : ''
                        }`}
                      >
                        <div
                          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-pill ${
                            isSelected
                              ? 'bg-accent-blue text-white'
                              : 'bg-accent-blue/10 text-accent-blue'
                          }`}
                        >
                          <Building2 size={14} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p
                            className={`truncate text-sm ${
                              isSelected
                                ? 'font-semibold text-accent-blue'
                                : 'font-medium text-text-primary'
                            }`}
                          >
                            {u.canonical_name}
                          </p>
                          <p className="truncate text-xs text-text-secondary">
                            {u.coordinator_count} coord · {u.enabled_sector_count} setores
                          </p>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
