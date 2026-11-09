# bASIC

**An agentic IDE for ASIC chip design.** It reads a real chip back as code, proves every line, and shows you what it's doing, even in Minecraft.

🥈 Runner-up, JetBrains track, HackTheBurgh 2026 · [basic-asic.vercel.app](https://basic-asic.vercel.app)

> You can't review what you can't read.

- **Explore:** a real Tiny Tapeout chip in 3D, from its public layout. Click any gate and run it.
- **Explain:** the stripped netlist reads back as short Verilog, proven equal to the gates with Yosys.
- **Agents:** AI agents propose, a formal checker decides. Every claim and edit comes back proven, refuted or tested only.
- **Find:** a hidden trigger comes back as a line of code nobody wrote.
- **Minecraft:** the same chip, built in real redstone.

| Held-out test set (18 chips) | |
|---|---|
| Proven equal by Yosys | 15 |
| Planted triggers found blind | 16 / 16 |
| False alarms on clean chips | 3 / 16 |

## Run it

Needs `gh`, `yosys`, `iverilog`, `uv` and Node 22.

```bash
uv venv -p 3.12 .venv && uv pip install -e '.[dev]'
.venv/bin/python -m basic.ingest.fetch    # chip data
.venv/bin/python -m basic.bench.run       # decompile and prove every chip
cd web && npm install && npm run stage-data && npm run sync-data && npm run dev:all
```

## Repo

| | |
|---|---|
| [`basic/`](basic/README.md) | Decompiler, proofs, benchmark, API |
| [`agent/`](agent/README.md) | Witness agents, `basic` CLI, MCP server |
| [`web/`](web/README.md) | The app |
| [`minecraft/`](minecraft/README.md) | Redstone builds and the chip as a world |
| [`deploy/`](deploy/vercel/README.md) | Vercel |
| [`docs/`](docs/README.md) | How chips work, hackathon archive |

Built by Yuyao Xie, Ian Zhang and Alex De Vrieze. Team rules: [AGENTS.md](AGENTS.md).
