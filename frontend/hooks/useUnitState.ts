'use client';

import { useQuery } from '@tanstack/react-query';
import { apiFetch, ApiError, type UnitState } from '@/lib/api';

export function useUnitState(unitId: string | null) {
  return useQuery<UnitState | null>({
    queryKey: ['unit-state', unitId],
    enabled: Boolean(unitId),
    queryFn: async () => {
      if (!unitId) return null;
      try {
        return await apiFetch<UnitState>(`/api/unit/${unitId}/state`);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
  });
}
