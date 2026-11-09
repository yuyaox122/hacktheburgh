# Deploying bASIC to Vercel

One Vercel project serves both the web app and the API. The site is static files. The FastAPI app runs as one Python function, with Yosys compiled to WebAssembly ([yowasp](https://yowasp.org)), since Vercel has no Yosys binary. This is how https://basic-asic.vercel.app was built.

```bash
# from the repo root, with the data fetched and the benchmark run (see the root README)
cd web && npm run stage-data && npm run sync-data && npm run build && cd ..
.venv/bin/python deploy/vercel/stage.py ../basic-deploy      # a folder outside the repo
cd ../basic-deploy && npx vercel deploy --prod               # first time: link or create the project
```

- **The AI guide** (Ask) needs `ANTHROPIC_API_KEY` set in the Vercel project's environment variables. Without it, the chat says it's offline and everything else works.
- **The Jane Street warm-up has no licence.** `stage.py` leaves it out of both the site and the API. Never deploy `web/dist` directly from a machine that has warm-up data in `web/public/data`.
- **Minecraft textures** in `web/public/mc` are stripped from every build by `web/vite.config.ts`.
- On Vercel's builder, `build_cache.py` compiles Yosys from WebAssembly to machine code once, so a cold start takes about 2 s instead of about 80 s.
- To try the staged folder locally the way Vercel runs it: `cd ../basic-deploy && <a venv with requirements.txt + uvicorn>/bin/uvicorn api.index:app`. Take `yosys` off `PATH` to exercise the WebAssembly shim.

Not deployed here: the Witness agent (`basic serve`) and the Minecraft bridge. Both need long-running processes, so they run locally.
