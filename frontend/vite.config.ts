import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendRoutes =
  "^/(analyze|upload|register|login|logout|session|orders|merge|files|qr|inventory|printers|analytics|notifications|progress|search|whatsapp|email|branches|ws)(/|$)";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      [backendRoutes]: {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
