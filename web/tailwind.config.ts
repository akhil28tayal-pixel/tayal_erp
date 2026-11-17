import type { Config } from "tailwindcss";

// Tokens: white and blue — white panels on a very light blue page, strong blue for the menu, tabs and buttons, dark navy text.
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        concrete: "#F3F6FC",
        slab: "#FFFFFF",
        seam: "#DCE4F2",
        ink: "#13234A",
        steel: "#5B6B86",
        blue: { DEFAULT: "#1B4FD8", dark: "#143CA6", tint: "#E6EEFF" },
        hivis: "#FFFFFF", // highlight on the blue menu
        ok: "#2E7D4F",
        warn: "#A86B12",
        bad: "#B23A3A",
      },
      fontFamily: { sans: ['"Archivo Variable"', "system-ui", "sans-serif"] },
      fontSize: { "2xs": ["11px", "16px"] },
    },
  },
  plugins: [],
} satisfies Config;
