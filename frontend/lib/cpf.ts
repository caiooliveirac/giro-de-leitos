// CPF helpers — formatting and check-digit validation.

export function digitsOnly(value: string): string {
  return (value || '').replace(/\D+/g, '');
}

export function formatCpf(value: string): string {
  const d = digitsOnly(value).slice(0, 11);
  const parts: string[] = [];
  if (d.length > 0) parts.push(d.slice(0, 3));
  if (d.length >= 4) parts[0] = `${d.slice(0, 3)}.${d.slice(3, 6)}`;
  if (d.length >= 7) parts[0] = `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}`;
  if (d.length >= 10) parts[0] = `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9, 11)}`;
  return parts[0] ?? '';
}

export function validateCpf(value: string): boolean {
  const cpf = digitsOnly(value);
  if (cpf.length !== 11) return false;
  if (/^(\d)\1{10}$/.test(cpf)) return false;
  const calc = (slice: number): number => {
    let sum = 0;
    for (let i = 0; i < slice; i++) {
      sum += Number(cpf.charAt(i)) * (slice + 1 - i);
    }
    const mod = (sum * 10) % 11;
    return mod === 10 ? 0 : mod;
  };
  const d1 = calc(9);
  const d2 = calc(10);
  return d1 === Number(cpf.charAt(9)) && d2 === Number(cpf.charAt(10));
}
