/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Theme is driven by CSS variables toggled via the `data-theme` attribute
  // (see src/index.css). We keep `class` here only so any stray `dark:`
  // utilities keep working; the real switching happens through the variables.
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        surface2: "var(--surface-2)",
        border: "var(--line)",
        fg: "var(--ink)",
        muted: "var(--muted)",
        brand: {
          DEFAULT: "var(--brand)",
          600: "var(--brand-2)",
          500: "var(--brand)",
          400: "var(--brand-2)",
        },
        accent: "var(--accent)",
        success: "var(--ok)",
        warning: "var(--warn)",
        danger: "var(--bad)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
      },
      boxShadow: {
        card: "0 4px 20px var(--shadow)",
      },
      backgroundImage: {
        brandgrad: "linear-gradient(135deg,var(--brand),var(--brand-2))",
      },
    },
  },
  plugins: [],
}
