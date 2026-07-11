import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

const API_PORT = process.env.AUDIT_API_PORT ?? "29180";
const UI_PORT = Number(process.env.AUDIT_UI_PORT ?? "5188");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: UI_PORT,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  clearScreen: false,
  envPrefix: ["VITE_", "TAURI_"],
});
