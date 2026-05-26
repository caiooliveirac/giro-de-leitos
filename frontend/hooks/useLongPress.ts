'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useLongPress — espelha o hook do design (design/src/cards.jsx).
 * Retorna `lp` (progresso 0–1) pra renderizar ring visível,
 * `active` (segurando), e `bind` (handlers de pointer) pra
 * acoplar no botão.
 */
export function useLongPress(
  onFire: () => void,
  onShortPress?: () => void,
  duration = 500,
) {
  const [lp, setLp] = useState(0);
  const [active, setActive] = useState(false);
  const startRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);

  const cancel = useCallback(() => {
    startRef.current = null;
    setActive(false);
    setLp(0);
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const tick = useCallback(() => {
    if (startRef.current == null) return;
    const elapsed = performance.now() - startRef.current;
    const p = Math.min(1, elapsed / duration);
    setLp(p);
    if (p >= 1) {
      firedRef.current = true;
      onFire();
      cancel();
      return;
    }
    rafRef.current = requestAnimationFrame(tick);
  }, [duration, onFire, cancel]);

  const start = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      if (firedRef.current) return;
      firedRef.current = false;
      startRef.current = performance.now();
      setActive(true);
      rafRef.current = requestAnimationFrame(tick);
    },
    [tick],
  );

  const end = useCallback(() => {
    if (!firedRef.current && startRef.current != null) {
      onShortPress?.();
    }
    firedRef.current = false;
    cancel();
  }, [cancel, onShortPress]);

  useEffect(() => () => cancel(), [cancel]);

  return {
    bind: {
      onPointerDown: start,
      onPointerUp: end,
      onPointerLeave: cancel,
      onPointerCancel: cancel,
    },
    lp,
    active,
  };
}
