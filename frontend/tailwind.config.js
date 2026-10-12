/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--bg-rgb) / <alpha-value>)',
        panel: 'rgb(var(--panel-rgb) / <alpha-value>)',
        elev: 'rgb(var(--elev-rgb) / <alpha-value>)',
        line: 'rgb(var(--line-rgb) / <alpha-value>)',
        fg: 'rgb(var(--fg-rgb) / <alpha-value>)',
        muted: 'rgb(var(--muted-rgb) / <alpha-value>)',
        faint: 'rgb(var(--faint-rgb) / <alpha-value>)',
        accent: 'rgb(var(--accent-rgb) / <alpha-value>)',
        ok: 'rgb(var(--ok-rgb) / <alpha-value>)',
        warn: 'rgb(var(--warn-rgb) / <alpha-value>)',
        err: 'rgb(var(--err-rgb) / <alpha-value>)',
        build: 'rgb(var(--build-rgb) / <alpha-value>)',
        plan: 'rgb(var(--plan-rgb) / <alpha-value>)',
        flow: 'rgb(var(--flow-rgb) / <alpha-value>)',
      },
      fontFamily: {
        mono: ['ui-monospace', '"JetBrains Mono"', '"SF Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
};
