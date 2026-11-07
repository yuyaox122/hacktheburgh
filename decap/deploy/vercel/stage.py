"""Stage the bASIC web app + API into one folder that `vercel deploy` can ship.

    python -I deploy/vercel/stage.py <out-dir>      (run from decap/, after `npm run build` in web/)
    cd <out-dir> && vercel deploy --prod

Layout: public/ (the built site and its static data), api/index.py (the FastAPI app as one function),
decap/ (core, api, Ian's decap_agent, chip data). The Jane Street warm-up has no licence, so it is left out
of both the site and the API, as on the team deploy.
"""
import json
import shutil
import sys
from pathlib import Path

DECAP = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def no_warmup_bench(src: Path, dst: Path) -> None:
    b = json.loads(src.read_text(encoding="utf-8"))
    b["chips"] = [c for c in b.get("chips", []) if not str(c.get("chip", "")).startswith("warmup")]
    dst.write_text(json.dumps(b, indent=1), encoding="utf-8")


def main(out: Path) -> None:
    dist = DECAP / "web" / "dist"
    if not (dist / "index.html").exists():
        sys.exit("Build the site first: cd web && npm run build")
    if out.exists():
        shutil.rmtree(out)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", "warmup*")

    # the site
    shutil.copytree(dist, out / "public", ignore=skip)
    data = out / "public" / "data"
    if (data / "bench.json").exists():
        no_warmup_bench(dist / "data" / "bench.json", data / "bench.json")
    idx = data / "designs" / "index.json"
    if idx.exists():   # design.json = the default real chip (a local warm-up build may have overwritten it)
        default = json.loads(idx.read_text(encoding="utf-8"))["default"]
        shutil.copy(data / "designs" / f"{default}.json", data / "design.json")

    # the API
    code = out / "decap"
    for d in ("core", "api"):
        shutil.copytree(DECAP / d, code / d, ignore=skip)
    shutil.copytree(DECAP / "agent" / "decap_agent", code / "agent" / "decap_agent", ignore=skip)
    for d in ("tt08", "pdk"):
        shutil.copytree(DECAP / "data" / d, code / "data" / d, ignore=skip)
    shutil.copytree(DECAP / "out" / "chips", code / "out" / "chips", ignore=skip)
    no_warmup_bench(DECAP / "out" / "bench.json", code / "out" / "bench.json")
    (code / "bench").mkdir()
    shutil.copy(DECAP / "bench" / "split.json", code / "bench" / "split.json")
    (out / "api").mkdir()
    shutil.copy(HERE / "index.py", out / "api" / "index.py")
    shutil.copy(HERE / "requirements.txt", out / "requirements.txt")
    shutil.copy(HERE / "vercel.json", out / "vercel.json")
    shutil.copy(HERE / "build_cache.py", out / "build_cache.py")
    print(f"staged -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
