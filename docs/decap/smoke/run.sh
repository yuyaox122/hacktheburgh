#!/usr/bin/env bash
# Decap proof smoke test on one real Tiny Tapeout 08 chip (tt_um_Electom_cla_4bits).
# Needs: gh (logged in), yosys (brew install yosys), python3.
# Fetches data into ./data (gitignored), then runs five checks. Each must give its expected verdict.
set -euo pipefail
cd "$(dirname "$0")"
P=tt_um_Electom_cla_4bits
SRC_REPO=Electom/tt08_CSA_4bits
SRC_SHA=213ea903e947a2fbc5415d67e893da5d43385ffe   # from the chip's commit_id.json
PDK=google/skywater-pdk-libs-sky130_fd_sc_hd
mkdir -p data/pdk data/src
raw() { gh api "repos/$1/contents/$2${3:+?ref=$3}" -H 'Accept: application/vnd.github.raw'; }

[ -s data/$P.v ] || raw TinyTapeout/tinytapeout-08 projects/$P/$P.v > data/$P.v
[ -s data/src/project.v ] || raw $SRC_REPO src/project.v $SRC_SHA > data/src/project.v
for c in $(grep -oE 'sky130_fd_sc_hd__[a-z0-9_]+ ' data/$P.v | sed 's/sky130_fd_sc_hd__//' | sort -u); do
  case $c in fill_*|decap_*|tapvpwrvgnd_*|diode_*) continue ;; esac
  [ -s data/pdk/$c.json ] || raw $PDK "cells/${c%_*}/sky130_fd_sc_hd__${c}__tt_025C_1v80.lib.json" > data/pdk/$c.json
done
python3 -I mkcells.py data/pdk data/$P.v data/cells.v

# Control A: swap the first xnor2 for an xor2.
awk '/sky130_fd_sc_hd__xnor2_1/ && !d {sub(/xnor2_1/,"xor2_1"); d=1} {print}' data/$P.v > data/net_swap.v

load_gate() { # $1 netlist, $2 wrapper, $3 "strip" to hide internal names
  echo "read_verilog data/cells.v $1 $2"; echo "hierarchy -top gate_top"; echo "flatten"; echo "rename gate_top gate"
  [ "${3:-}" = strip ] && { echo "rename -hide w:* c:*"; echo "opt_clean -purge"; }
  echo "design -stash gate"
}
equiv_script() { # gold file, gold module, netlist, wrapper
  echo "read_verilog $1"; echo "rename $2 gold"; echo "design -stash gold"; load_gate "$3" "$4"
  cat <<'EOF'
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
proc; opt_clean; async2sync
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple -seq 5
equiv_induct -seq 5
equiv_status -assert
EOF
}
miter_script() { # gold file, gold module, netlist, wrapper, strip?, sat command
  echo "read_verilog $1"; echo "rename $2 gold"; echo "design -stash gold"; load_gate "$3" "$4" "$5"
  cat <<EOF
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
proc; async2sync; opt_clean
miter -equiv -flatten -make_outputs gold gate miter
hierarchy -top miter
$6
EOF
}
# NB: never add -ignore_gold_x to miter; it made the planted trigger pass silently.

check() { # name, expected (pass|fail), script text
  printf '%s\n' "$3" > "data/$1.ys"
  if yosys -q -l "data/$1.log" "data/$1.ys" >/dev/null 2>&1; then got=pass; else got=fail; fi
  [ "$got" = "$2" ] && ok=OK || ok=WRONG
  printf '%-48s expected %-4s got %-4s %s\n' "$1" "$2" "$got" "$ok"
  [ "$ok" = OK ]
}

INDUCT="sat -verify -tempinduct -prove trigger 0 -set-init-zero -seq 1 miter"
CEX="sat -seq 3 -prove trigger 0 -show-inputs -set-init-zero -set in_rst_n 1 miter"
fails=0
check source_vs_netlist           pass "$(equiv_script data/src/project.v $P data/$P.v gate_wrap.v)" || fails=1
check source_vs_swapped_gate      fail "$(equiv_script data/src/project.v $P data/net_swap.v gate_wrap.v)" || fails=1
check source_vs_planted_trigger   fail "$(equiv_script data/src/project.v $P data/$P.v gate_trojan.v)" || fails=1
check blind_decompile_vs_netlist  pass "$(miter_script decompiled.v decap_out data/$P.v gate_wrap.v strip "$INDUCT")" || fails=1
check blind_decompile_vs_planted  fail "$(miter_script decompiled.v decap_out data/$P.v gate_trojan.v strip "$INDUCT")" || fails=1
miter_script data/src/project.v $P data/$P.v gate_trojan.v "" "$CEX" > data/counterexample.ys
yosys -q -l data/counterexample.log data/counterexample.ys >/dev/null 2>&1 || true
echo "planted-trigger counterexample (ui_in per step):"
grep -E '^\s+[0-9]+\s+\\in_ui_in' data/counterexample.log || echo "  none found"
exit $fails
