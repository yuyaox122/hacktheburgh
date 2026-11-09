"""Parse a flat SKY130 gate-level netlist (Tiny Tapeout format) into ports, cells and assign aliases. No Yosys needed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = re.compile(r"^sky130_(?:fd|ef)_sc_hd__")
PHYSICAL = re.compile(r"^(fill|decap|tapvpwrvgnd|tap|diode|fakediode)_")
POWER = {"VPWR", "VGND", "VPB", "VNB", "VDD", "VSS"}


@dataclass
class Cell:
    name: str                      # instance name, identical to GDS property 61
    type: str                      # e.g. "a21oi_1"
    pins: dict[str, str]           # pin -> net name (constants as given by parse(const=...))

    @property
    def physical(self) -> bool:
        return bool(PHYSICAL.match(self.type))


@dataclass
class Netlist:
    top: str
    ports: dict[str, dict]         # name -> {"dir": "in"|"out", "width": n}
    cells: list[Cell]
    aliases: dict[str, str] = field(default_factory=dict)   # assign lhs -> rhs

    @property
    def logic(self) -> list[Cell]:
        return [c for c in self.cells if not c.physical]

    def bits(self, direction: str) -> list[str]:
        """Port bits of one direction ("in" / "out"), e.g. ui_in[0] .. ui_in[7]."""
        out = []
        for n, p in self.ports.items():
            if p["dir"] == direction:
                out += [f"{n}[{k}]" for k in range(p["width"])] if p["width"] > 1 else [n]
        return out


def parse(path: str | Path, power_ports: bool = False, const: tuple[str, str] = ("0", "1")) -> Netlist:
    """power_ports: keep VPWR/VGND port declarations as ports (the redstone half has always counted them as inputs).
    const: names for the constant nets 1'b0 / 1'b1."""
    text = Path(path).read_text(errors="replace")
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    def net(s: str) -> str:
        s = s.strip()
        if s.startswith("\\"):
            s = s[1:].strip()
        m = re.fullmatch(r"1'b([01])", s)
        return const[int(m.group(1))] if m else s

    m = re.search(r"\bmodule\s+(\S+)", text)
    top = m.group(1) if m else "top"
    ports: dict[str, dict] = {}
    for d, rng, names in re.findall(r"\b(input|output)\b\s*(\[\s*\d+\s*:\s*\d+\s*\])?\s*([^;]+);", text):
        w = 1
        if rng:
            a, b = map(int, re.findall(r"\d+", rng))
            w = abs(a - b) + 1
        for n in names.split(","):
            n = n.strip()
            if n and (power_ports or n not in POWER):
                ports[n] = {"dir": "in" if d == "input" else "out", "width": w}
    aliases = {net(a): net(b) for a, b in re.findall(r"\bassign\s+([^=]+?)\s*=\s*([^;]+);", text)}
    cells = []
    for typ, name, body in re.findall(r"\b(sky130_\w+)\s+(\\?\S+)\s*\((.*?)\);", text, flags=re.S):
        pins = {}
        for pin, n in re.findall(r"\.(\w+)\s*\(\s*([^()]*?)\s*\)", body):
            if pin in POWER or not n:
                continue
            pins[pin] = net(n)
        cells.append(Cell(net(name), PREFIX.sub("", typ), pins))
    return Netlist(top, ports, cells, aliases)
