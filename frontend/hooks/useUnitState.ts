'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiFetch,
  ApiError,
  type UnitState,
  type Bed,
  type Counter,
  type Specialist,
  type Exam,
} from '@/lib/api';
import { UnitWebSocket, type UnitWsStatus, type UnitWsEvent } from '@/lib/ws';

function mergeBed(state: UnitState, bed: Bed): UnitState {
  const beds = state.beds.some((b) => b.bed_number === bed.bed_number)
    ? state.beds.map((b) => (b.bed_number === bed.bed_number ? bed : b))
    : [...state.beds, bed];
  return { ...state, beds, updated_at: bed.last_updated_at ?? state.updated_at };
}

function mergeCounter(state: UnitState, counter: Counter): UnitState {
  const counters = state.counters.some((c) => c.sector_key === counter.sector_key)
    ? state.counters.map((c) => (c.sector_key === counter.sector_key ? counter : c))
    : [...state.counters, counter];
  return { ...state, counters, updated_at: counter.last_updated_at ?? state.updated_at };
}

function mergeSpecialist(state: UnitState, sp: Specialist): UnitState {
  const specialists = state.specialists.some((s) => s.sector_key === sp.sector_key)
    ? state.specialists.map((s) => (s.sector_key === sp.sector_key ? sp : s))
    : [...state.specialists, sp];
  return { ...state, specialists, updated_at: sp.last_updated_at ?? state.updated_at };
}

function mergeExam(state: UnitState, ex: Exam): UnitState {
  const exams = state.exams.some((e) => e.sector_key === ex.sector_key)
    ? state.exams.map((e) => (e.sector_key === ex.sector_key ? ex : e))
    : [...state.exams, ex];
  return { ...state, exams, updated_at: ex.last_updated_at ?? state.updated_at };
}

export function useUnitState(unitId: string | null) {
  const queryClient = useQueryClient();
  const [connectionStatus, setConnectionStatus] = useState<UnitWsStatus>('closed');
  const wsRef = useRef<UnitWebSocket | null>(null);

  const query = useQuery<UnitState | null>({
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

  useEffect(() => {
    if (!unitId) return;
    const queryKey = ['unit-state', unitId] as const;

    const applyEvent = (evt: UnitWsEvent) => {
      // Takeover da vermelha muda a fonte dos leitos (parser ↔ manual);
      // re-busca o estado completo em vez de fazer merge incremental.
      if (evt.type === 'red_room_assumed' || evt.type === 'red_room_released') {
        queryClient.invalidateQueries({ queryKey });
        return;
      }
      queryClient.setQueryData<UnitState | null>(queryKey, (prev) => {
        if (!prev) return prev;
        const payload = evt.payload as any;
        switch (evt.type) {
          case 'bed_updated':
            return payload ? mergeBed(prev, payload as Bed) : prev;
          case 'counter_updated':
            return payload ? mergeCounter(prev, payload as Counter) : prev;
          case 'specialist_updated':
            return payload ? mergeSpecialist(prev, payload as Specialist) : prev;
          case 'exam_updated':
            return payload ? mergeExam(prev, payload as Exam) : prev;
          case 'unit_snapshot':
            return (payload as UnitState) ?? prev;
          default:
            return prev;
        }
      });
    };

    const ws = new UnitWebSocket({
      unitId,
      onEvent: applyEvent,
      onStatusChange: setConnectionStatus,
      onReopen: () => {
        queryClient.invalidateQueries({ queryKey });
      },
    });
    wsRef.current = ws;

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [unitId, queryClient]);

  return {
    ...query,
    connectionStatus,
  };
}
