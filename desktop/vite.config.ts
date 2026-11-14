import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI is a client of the engine's HTTP API (calco serve, port 7878).
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:7878" } },
  build: { target: "es2022", chunkSizeWarningLimit: 1200 },
});
