/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          900: '#0b1220',
          800: '#111a2e',
          700: '#1b2740'
        }
      }
    }
  },
  plugins: []
};
