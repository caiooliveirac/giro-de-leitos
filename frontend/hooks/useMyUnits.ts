'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';

export interface MyUnit {
  id: string;
  name: string;
  is_primary: boolean;
}

const VIEW_KEY = 'gl_coord_viewing_unit';

/**
 * UPAs que o operador logado pode visualizar (coordenador multi-UPA) e qual
 * está ativa. A seleção persiste em ``localStorage`` (análogo ao
 * ``gl_admin_viewing_unit`` do admin) e cai na UPA primária por padrão.
 *
 * Em qualquer falha (endpoint ausente, profissional sem permissão, etc.)
 * degrada para a UPA primária — o seletor simplesmente não aparece.
 */
export function useMyUnits(primaryUnitId: string | null | undefined) {
  const [units, setUnits] = useState<MyUnit[]>([]);
  const [selected, setSelectedState] = useState<string | null>(primaryUnitId ?? null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const rows = await apiFetch<MyUnit[]>('/api/auth/me/units');
        if (!alive) return;
        setUnits(rows);

        let stored: string | null = null;
        try {
          stored = window.localStorage.getItem(VIEW_KEY);
        } catch {
          /* ignore */
        }
        const valid = rows.find((u) => u.id === stored);
        const primary =
          rows.find((u) => u.id === primaryUnitId) ??
          rows.find((u) => u.is_primary) ??
          rows[0] ??
          null;
        setSelectedState(valid ? valid.id : primary?.id ?? primaryUnitId ?? null);
      } catch {
        if (alive) {
          setUnits([]);
          setSelectedState(primaryUnitId ?? null);
        }
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [primaryUnitId]);

  const setSelected = useCallback((id: string) => {
    setSelectedState(id);
    try {
      window.localStorage.setItem(VIEW_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  return { units, selected, setSelected, loaded };
}
