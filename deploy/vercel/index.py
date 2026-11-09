"""Vercel entry point for the bASIC API (stage.py copies it to api/index.py).

Vercel's Python runtime has no Yosys, so this puts a `yosys` shim on PATH that runs yowasp-yosys (Yosys compiled to
WebAssembly), unpacks the machine-code cache made by build_cache.py into /tmp (the only writable directory), and then
serves the normal FastAPI app.
"""
import gzip
import os
import shutil
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]      # the staged folder: api/, basic/, public/, yowasp-cache/
sys.path.insert(0, str(TASK))

os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("YOWASP_CACHE_DIR", "/tmp/yowasp-cache")

# Machine-code cache: <digest>.gz -> $YOWASP_CACHE_DIR/yowasp_yosys/yosys.wasm/<digest>
for gz in (TASK / "yowasp-cache").glob("*.gz"):
    dst = Path(os.environ["YOWASP_CACHE_DIR"]) / "yowasp_yosys" / "yosys.wasm" / gz.stem
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".part")
        with gzip.open(gz, "rb") as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 22)
        tmp.replace(dst)

# yowasp mounts a private temp dir over /tmp inside the sandbox, which would hide the API's own temp files
# (/tmp/tmpXXXX/cells.v). The shim mounts the real /tmp there instead and skips yowasp's cleanup of it.
SHIM = f"""#!{sys.executable}
import sys, types, tempfile, shutil, yowasp_runtime, yowasp_yosys
yowasp_runtime.tempfile = types.SimpleNamespace(mkdtemp=lambda *a, **k: tempfile.gettempdir())
yowasp_runtime.shutil = types.SimpleNamespace(rmtree=lambda *a, **k: None)
sys.exit(yowasp_yosys.run_yosys(sys.argv[1:]))
"""
if shutil.which("yosys") is None:
    bindir = Path("/tmp/bin")
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "yosys"
    shim.write_text(SHIM)
    shim.chmod(0o755)
    os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"

from basic.api.main import app  # noqa: E402,F401  (Vercel serves this ASGI app)
