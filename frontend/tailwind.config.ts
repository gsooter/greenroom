import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        surface: "var(--surface)",
        muted: "var(--muted)",
        border: "var(--border)",
        accent: {
          DEFAULT: "var(--accent)",
          foreground: "var(--accent-foreground)",
        },
        bg: {
          base: "var(--color-bg-base)",
          surface: "var(--color-bg-surface)",
          white: "var(--color-bg-white)",
        },
        text: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          inverse: "var(--color-text-inverse)",
        },
        green: {
          dark: "var(--color-green-dark)",
          primary: "var(--color-green-primary)",
          soft: "var(--color-green-soft)",
        },
        blush: {
          soft: "var(--color-blush-soft)",
          accent: "var(--color-blush-accent)",
        },
        navy: {
          dark: "var(--color-navy-dark)",
          soft: "var(--color-navy-soft)",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Inter",
          "system-ui",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
export default config;
