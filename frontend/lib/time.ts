// Lightweight relative time formatter (pt-BR), no external deps.

export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  if (Number.isNaN(diffMs)) return '';
  const sec = Math.round(diffMs / 1000);
  if (sec < 45) return 'agora';
  const min = Math.round(sec / 60);
  if (min < 60) return `há ${min}min`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `há ${hr}h`;
  const day = Math.round(hr / 24);
  if (day < 7) return `há ${day}d`;
  return then.toLocaleDateString('pt-BR');
}
