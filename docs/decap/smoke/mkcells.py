# Build Yosys-readable SKY130 HD cell models from per-cell Liberty JSON + a netlist.
import json, re, sys, os
pdk, netlist, out = sys.argv[1], sys.argv[2], sys.argv[3]
used = sorted(set(re.findall(r"sky130_fd_sc_hd__([a-z0-9_]+)\s", open(netlist).read())))
PG = ["VGND", "VNB", "VPB", "VPWR"]
mods = []
for c in used:
    f = os.path.join(pdk, c + ".json")
    if not os.path.exists(f):  # fill/tap/decap/diode: physical only
        mods.append(f"module sky130_fd_sc_hd__{c}(input VGND, input VNB, input VPB, input VPWR, input DIODE);\nendmodule\n")
        continue
    d = json.load(open(f))
    pins = {k[4:]: v for k, v in d.items() if k.startswith("pin,")}
    ins = [p for p, v in pins.items() if v.get("direction") == "input"]
    outs = {p: v.get("function") for p, v in pins.items() if v.get("direction") == "output"}
    ff = [v for k, v in d.items() if k.startswith("ff,")]
    ports = [f"input {p}" for p in ins + PG] + [f"output {p}" for p in outs]
    body = []
    if ff:
        g = ff[0]; iq = [k for k in d if k.startswith("ff,")][0].split(",")[1]
        clk, nxt, clr = g["clocked_on"], g["next_state"], g.get("clear")
        rst = clr.lstrip("!")
        body.append(f"  reg {iq};")
        body.append(f"  always @(posedge {clk} or negedge {rst}) if (!{rst}) {iq} <= 1'b0; else {iq} <= {nxt};")
        body += [f"  assign {p} = {fn};" for p, fn in outs.items()]
    else:
        body += [f"  assign {p} = {fn.replace('!', '~')};" for p, fn in outs.items()]
    mods.append(f"module sky130_fd_sc_hd__{c}({', '.join(ports)});\n" + "\n".join(body) + "\nendmodule\n")
open(out, "w").write("\n".join(mods))
print(len(used), "cell types ->", out)
