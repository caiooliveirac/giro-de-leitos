// Brazilian phone formatting helpers.

export function digitsOnly(value: string): string {
  return (value || '').replace(/\D+/g, '');
}

export function formatPhoneBR(value: string): string {
  const d = digitsOnly(value).slice(0, 11);
  if (d.length === 0) return '';
  if (d.length <= 2) return `(${d}`;
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

export function isValidPhoneBR(value: string): boolean {
  const d = digitsOnly(value);
  return d.length === 10 || d.length === 11;
}
