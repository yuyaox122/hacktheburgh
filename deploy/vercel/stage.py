"""Stage the bASIC web app + API into one folder that `vercel deploy` can ship.

    cd web && npm run build && cd ..
    .venv/bin/python deploy/vercel/stage.py <out-dir>
    cd <out-dir> && vercel deploy --prod

Layout of <out-dir>: public/ (the built site and its static data), api/index.py (the FastAPI app as one function),
basic/ (the core package with the chip data and results it serves). The Jane Street warm-up has no licence, so it is
left out of both the site and the API.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CORE = REPO / "basic"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "warmup*", ".env", "data", "out")


def no_warmup_bench(src: Path, dst: Path) -> None:
    b = json.loads(src.read_text(encoding="utf-8"))
    b["chips"] = [c for c in b.get("chips", []) if not str(c.get("chip", "")).startswith("warmup")]
    dst.write_text(json.dumps(b, indent=1), encoding="utf-8")


def main(out: Path) -> None:
    dist = REPO / "web" / "dist"
    if not (dist / "index.html").exists():
        sys.exit("Build the site first: cd web && npm run build")
    if not (CORE / "out" / "bench.json").exists():
        sys.exit("No benchmark results: run .venv/bin/python -m basic.bench.run first")
    if out.exists():
        shutil.rmtree(out)

    # the site
    shutil.copytree(dist, out / "public", ignore=shutil.ignore_patterns("warmup*"))
    data = out / "public" / "data"
    if (data / "bench.json").exists():
        no_warmup_bench(data / "bench.json", data / "bench.json")
    idx = data / "designs" / "index.json"
    if idx.exists():   # design.json = the default real chip (a local warm-up build may have overwritten it)
        default = json.loads(idx.read_text(encoding="utf-8"))["default"]
        shutil.copy(data / "designs" / f"{default}.json", data / "design.json")

    # the API: the core package (code only), then the data and results it serves
    code = out / "basic"
    shutil.copytree(CORE, code, ignore=SKIP)
    for d in ("tt08", "pdk"):
        shutil.copytree(CORE / "data" / d, code / "data" / d, ignore=SKIP)
    shutil.copytree(CORE / "out" / "chips", code / "out" / "chips", ignore=SKIP)
    no_warmup_bench(CORE / "out" / "bench.json", code / "out" / "bench.json")
    (out / "api").mkdir()
    shutil.copy(HERE / "index.py", out / "api" / "index.py")
    for f in ("requirements.txt", "vercel.json", "build_cache.py"):
        shutil.copy(HERE / f, out / f)
    print(f"staged -> {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve())
