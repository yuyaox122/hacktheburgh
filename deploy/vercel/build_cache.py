"""Vercel build step: compile yowasp-yosys to machine code once, on Vercel's Linux builder, and ship it gzipped.

Without this every cold start spends ~80 s compiling Yosys from WebAssembly. The raw cache is ~210 MB (too big for the
250 MB function limit next to the dependencies); gzipped it is ~50 MB and api/index.py unpacks it to /tmp in ~1.5 s.
If the runtime CPU differs from the builder's, wasmtime refuses the cache and yowasp simply compiles as before.
"""
import gzip
import hashlib
import importlib.resources
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
req = [l.strip() for l in (HERE / "requirements.txt").read_text().splitlines() if l.startswith(("yowasp", "wasmtime"))]
site = Path(tempfile.mkdtemp())
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", str(site), *req], check=True)
sys.path.insert(0, str(site))
import wasmtime  # noqa: E402

wasm = importlib.resources.files("yowasp_yosys").joinpath("yosys.wasm").read_bytes()
digest = hashlib.sha1(wasm).hexdigest()
config = wasmtime.Config()
config.wasm_exceptions = True   # same config as yowasp_runtime.run_wasm, or the cache will not load
blob = wasmtime.Module(wasmtime.Engine(config), wasm).serialize()
out = HERE / "yowasp-cache" / f"{digest}.gz"
out.parent.mkdir(exist_ok=True)
out.write_bytes(gzip.compress(blob, 6))
shutil.rmtree(site, ignore_errors=True)
print(f"yowasp cache: {len(blob) >> 20} MB -> {out.stat().st_size >> 20} MB gzipped ({digest})")
