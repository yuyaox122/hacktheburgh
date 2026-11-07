import { defineConfig } from "vite";

// Static-first: /data/* is served from public/data (generated, gitignored); /api/* goes to the FastAPI backend.
// DECAP_API overrides the backend (e.g. a second checkout running the API on another port).
const api = process.env.DECAP_API ?? "http://127.0.0.1:8000";
export default defineConfig({
  server: {
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 1200 },
});
