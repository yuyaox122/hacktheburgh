import { rmSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// Static-first: /data/* is served from public/data (generated, gitignored); /api/* goes to the FastAPI backend;
// /redstone/* goes to minecraft/redstone/bridge.py (Minecraft).
// /witness/* goes to the Witness agent API (agent/).
// DECAP_API overrides the backend (e.g. a second checkout running the API on another port); REDSTONE_API the bridge; WITNESS_API the agent.
const api = process.env.DECAP_API ?? "http://127.0.0.1:8000";
const redstone = process.env.REDSTONE_API ?? "http://127.0.0.1:8002";
const witness = process.env.WITNESS_API ?? "http://127.0.0.1:8791";   // agent/ witness_api (SSE runs)
export default defineConfig({
  server: {
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: true }, "/redstone": { target: redstone, changeOrigin: true }, "/witness": { target: witness, changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target: api, changeOrigin: true }, "/redstone": { target: redstone, changeOrigin: true }, "/witness": { target: witness, changeOrigin: true } },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 1200 },
  // public/mc holds Mojang's textures from the local client jar (npm run mc-assets): dev only, never in a build.
  plugins: [{ name: "strip-mc-assets", apply: "build", closeBundle() { rmSync(resolve(__dirname, "dist/mc"), { recursive: true, force: true }); } }],
});
