/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,jsx}',
    './components/**/*.{js,jsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-plex-sans)', 'system-ui', 'sans-serif'],
        body: ['var(--font-karla)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-plex-mono)', 'ui-monospace', 'monospace'],
      },
      colors: {
        surface: '#FFFFFF',
        surfaceMuted: '#F4F4F5',
        appbg: '#FAFAFA',
        border: '#E4E4E7',
        borderStrong: '#D4D4D8',
        ink: '#09090B',
        inkSecondary: '#52525B',
        inkMuted: '#A1A1AA',
        accent: '#0055FF',
        confHigh: '#059669',
        confMid: '#D97706',
        confLow: '#DC2626',
        dupBg: '#FEF2F2',
        dupText: '#7F1D1D',
        dupBorder: '#FCA5A5',
      },
      borderRadius: {
        none: '0',
        sm: '2px',
      },
      boxShadow: {
        hard: '2px 2px 0 0 rgb(0 0 0)',
      },
    },
  },
  plugins: [],
};
