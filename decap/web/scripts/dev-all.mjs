// npm run dev:all — start the Decap API (uvicorn) and the Vite dev server together, so the demo never shows "API offline".
// Ports: API_PORT (default 8000), WEB_PORT (default 5173). Ctrl+C stops both.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const decap = join(web, "..");
const apiPort = process.env.API_PORT ?? "8000", webPort = process.env.WEB_PORT ?? "5173";
const uvicorn = [join(decap, ".venv", "Scripts", "uvicorn.exe"), join(decap, ".venv", "bin", "uvicorn")].find(existsSync);
if (!uvicorn) { console.error("No decap/.venv found: create it and pip install the API requirements first."); process.exit(1); }

const kids = [
  spawn(uvicorn, ["api.main:app", "--port", apiPort], { cwd: decap, stdio: "inherit" }),
  spawn(process.platform === "win32" ? "npx.cmd" : "npx", ["vite", "--port", webPort, "--strictPort"],
    { cwd: web, stdio: "inherit", shell: process.platform === "win32", env: { ...process.env, DECAP_API: `http://127.0.0.1:${apiPort}` } }),
];
const stop = () => { for (const k of kids) k.kill(); process.exit(); };
process.on("SIGINT", stop); process.on("SIGTERM", stop);
for (const k of kids) k.on("exit", (code) => { if (code) { console.error(`a child exited with ${code}`); stop(); } });
