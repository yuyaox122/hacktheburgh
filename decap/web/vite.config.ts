import { defineConfig } from "vite";

// Static-first: /data/* is served from public/data (generated, gitignored); /api/* goes to the FastAPI backend.
export default defineConfig({
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 1200 },
});
