"""Datapacks for 1.21.11 (pack format 94) and a few command builders both halves use."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

PACK_FORMAT = {"min_format": [94, 0], "max_format": [94, 1]}


def write_meta(pack: Path, description: str, indent: int | None = None) -> None:
    """pack.mcmeta for the datapack folder `pack` (created if needed)."""
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "pack.mcmeta").write_text(json.dumps({"pack": {"description": description, **PACK_FORMAT}}, indent=indent))


def write_functions(fdir: Path, fns: dict[str, list[str]]) -> None:
    """One <name>.mcfunction per entry, one command per line."""
    for name, lines in fns.items():
        (fdir / f"{name}.mcfunction").write_text("\n".join(lines) + "\n")


def install(pack: Path, world: Path) -> Path:
    """Copy the datapack folder `pack` into <world>/datapacks/, replacing an older copy."""
    dst = world / "datapacks" / pack.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(pack, dst)
    return dst


def sign(lines: list[str], rotation: int = 0) -> str:
    """Block state of a standing oak sign with up to four lines of text."""
    msgs = ",".join('"' + s.replace("\\", "/").replace('"', "'") + '"' for s in (lines + ["", "", "", ""])[:4])
    return f"oak_sign[rotation={rotation}]{{front_text:{{messages:[{msgs}]}}}}"


def text_display(x, y, z, text: str, color: str, scale, bold: bool = False, tag: str = "decap") -> str:
    """summon a floating, always-facing label."""
    return (f"summon text_display {x} {y} {z} {{Tags:[\"{tag}\"],billboard:\"center\",background:0,view_range:2.5f,"
            f"transformation:{{left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f],translation:[0f,0f,0f],"
            f"scale:[{scale}f,{scale}f,{scale}f]}},text:{json.dumps({'text': text, 'color': color, 'bold': bold})}}}")
