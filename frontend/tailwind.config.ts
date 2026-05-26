import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['selector', '[data-theme="dark"]'],
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // tokens do design
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        'surface-elev': 'var(--surface-elev)',
        line: 'var(--line)',
        'line-strong': 'var(--line-strong)',
        ink: 'var(--ink)',
        'ink-2': 'var(--ink-2)',
        'ink-3': 'var(--ink-3)',
        critical: 'var(--critical)',
        'critical-soft': 'var(--critical-soft)',
        'critical-ink': 'var(--critical-ink)',
        success: 'var(--success)',
        'success-soft': 'var(--success-soft)',
        'success-ink': 'var(--success-ink)',
        warning: 'var(--warning)',
        'warning-soft': 'var(--warning-soft)',
        'warning-ink': 'var(--warning-ink)',
        obit: 'var(--obit)',
        'obit-soft': 'var(--obit-soft)',
        'accent-soft': 'var(--accent-soft)',
        neutral: 'var(--neutral)',

        // aliases legados (mantém Fase 5 viva enquanto migra)
        card: 'var(--surface-elev)',
        border: 'var(--line)',
        text: {
          primary: 'var(--ink)',
          secondary: 'var(--ink-2)',
          tertiary: 'var(--ink-3)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          red: 'var(--critical)',
          amber: 'var(--warning)',
          green: 'var(--success)',
          blue: 'var(--accent)',
        },
      },
      borderRadius: {
        sm: 'var(--r-sm)',
        md: 'var(--r-md)',
        lg: 'var(--r-lg)',
        xl: 'var(--r-xl)',
        card: 'var(--r-lg)',
        pill: '999px',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'Geist', 'system-ui', '-apple-system', 'sans-serif'],
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        pop: 'var(--shadow-pop)',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.34, 1.2, 0.4, 1)',
        'ease-soft': 'cubic-bezier(0.4, 0.0, 0.2, 1)',
      },
    },
  },
  plugins: [],
};

export default config;
