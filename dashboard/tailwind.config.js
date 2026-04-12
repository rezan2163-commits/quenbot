/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0f172a",
          card: "#1e293b",
          hover: "#334155",
          border: "#334155",
        },
        accent: {
          DEFAULT: "#818cf8",
          dim: "#6366f1",
        },
        bull: "#22c55e",
        bear: "#ef4444",
        warn: "#f59e0b",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
    },
  },
  plugins: [],
};
