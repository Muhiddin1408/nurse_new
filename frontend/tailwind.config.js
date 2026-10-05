/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        display: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        brand: {
          50: '#ecfdf8', 100: '#d1faee', 200: '#a7f3de', 300: '#6ee7c8', 400: '#34d3ad',
          500: '#10b995', 600: '#059679', 700: '#047863', 800: '#065f50', 900: '#064e43', 950: '#022c27',
        },
        ink: {
          50: '#f6f8fb', 100: '#eceff5', 200: '#d6dce8', 300: '#b2bdd1', 400: '#8696b2',
          500: '#647596', 600: '#4f5d7c', 700: '#414c65', 800: '#2a3245', 900: '#171c2b', 950: '#0c101b',
        },
        sky2: { 500: '#3b82f6' },
      },
      boxShadow: {
        soft: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px -6px rgba(16,24,40,.08)',
        lift: '0 2px 4px rgba(16,24,40,.04), 0 18px 40px -12px rgba(16,24,40,.18)',
        glow: '0 10px 40px -10px rgba(16,185,149,.55)',
      },
      borderRadius: { '4xl': '2rem' },
      keyframes: {
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        float: { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-10px)' } },
      },
      animation: {
        shimmer: 'shimmer 1.6s infinite',
        float: 'float 6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
