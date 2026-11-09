// Plain-English descriptions and colour categories for SKY130 HD cells.

import type { Lib, LibCell } from "../api/types";
import { parse, toWords } from "../sim/expr";

export type Category = "flop" | "mux" | "arith" | "gate" | "complex" | "buffer" | "clock" | "const" | "phys";
export const CATEGORY_LABEL: Record<Category, string> = {
  flop: "flip-flop (memory)", mux: "multiplexer (switch)", arith: "XOR / adder", gate: "basic gate",
  complex: "compound gate", buffer: "buffer / inverter", clock: "clock buffer", const: "constant", phys: "physical only",
};
export const CATEGORY_VAR: Record<Category, string> = {
  flop: "--reg", mux: "--mux", arith: "--adder", gate: "--gate", complex: "--detect", buffer: "--const", clock: "--clock", const: "--const", phys: "--faint",
};

export const family = (type: string) => type.replace(/_\d+$/, "");

export function category(type: string, L?: LibCell): Category {
  const f = family(type);
  if (L?.phys || /^(fill|decap|tapvpwrvgnd|diode)/.test(f)) return "phys";
  if (L?.ff || /^(df|sdf|edf|dl)/.test(f)) return "flop";
  if (/^clk/.test(f)) return "clock";
  if (/^conb/.test(f)) return "const";
  if (/^mux/.test(f)) return "mux";
  if (/^(x[n]?or|ha|fa|maj)/.test(f)) return "arith";
  if (/^(buf|inv|dly|probe)/.test(f)) return "buffer";
  if (/^(and|nand|or|nor)\d/.test(f)) return "gate";
  return "complex";
}

const N = (k: number) => ["", "one", "two", "three", "four", "five"][k] ?? String(k);

/** Short, plain-English sentence for what a cell does. */
export function describe(type: string, L?: LibCell): string {
  const f = family(type);
  let m: RegExpExecArray | null;
  if (L?.phys || /^(fill|decap|tapvpwrvgnd|diode)/.test(f)) {
    if (f.startsWith("decap")) return "Decoupling capacitor: a tiny charge reservoir that steadies the power supply. No logic.";
    if (f.startsWith("tap")) return "Well tap: ties the silicon wells to power and ground so transistors behave. No logic.";
    if (f.startsWith("diode")) return "Antenna diode: protects gates from charge built up during manufacturing. No logic.";
    return "Filler: empty space that keeps the rows of cells continuous. No logic.";
  }
  if (/^dfrtp/.test(f)) return "Flip-flop: remembers one bit. On each rising clock edge it copies D to Q. Pulling RESET_B low forces Q to 0 straight away.";
  if (/^dfstp/.test(f)) return "Flip-flop: remembers one bit. On each rising clock edge it copies D to Q. Pulling SET_B low forces Q to 1 straight away.";
  if (/^dfxtp/.test(f)) return "Flip-flop: remembers one bit. On each rising clock edge it copies D to Q.";
  if (/^dfrbp/.test(f)) return "Flip-flop with both outputs: on each rising clock edge Q copies D (Q_N is its opposite). RESET_B low forces Q to 0.";
  if (L?.ff) return "Flip-flop: remembers one bit, updated on a clock edge.";
  if (/^clkbuf/.test(f)) return "Clock buffer: a strong amplifier that copies the clock signal so it reaches many flip-flops at the same moment. Added by the layout tools, not by the designer.";
  if (/^(buf|dlygate|dlymetal)/.test(f)) return "Buffer: copies its input to its output, with more drive strength so the signal can travel further.";
  if (/^clkinv|^inv/.test(f)) return "Inverter (NOT gate): the output is the opposite of the input.";
  if (/^conb/.test(f)) return "Constant: HI is always 1 and LO is always 0. Used to tie unused outputs.";
  if ((m = /^mux2i/.exec(f))) return "Inverting 2-way switch: outputs NOT A0 when S is 0, NOT A1 when S is 1.";
  if ((m = /^mux(\d)/.exec(f))) return `${N(+m[1])}-way switch (multiplexer): the select input${+m[1] > 2 ? "s choose" : " S chooses"} which data input is passed to the output.`;
  if ((m = /^and(\d)(b+)?$/.exec(f))) return `AND gate: outputs 1 only when all ${N(+m[1])} inputs are 1${m[2] ? ` (input${m[2].length > 1 ? "s" : ""} ending in _N ${m[2].length > 1 ? "are" : "is"} inverted first)` : ""}.`;
  if ((m = /^nand(\d)(b+)?$/.exec(f))) return `NAND gate: outputs 0 only when all ${N(+m[1])} inputs are 1, otherwise 1${m[2] ? " (the _N input is inverted first)" : ""}.`;
  if ((m = /^or(\d)(b+)?$/.exec(f))) return `OR gate: outputs 1 when any of its ${N(+m[1])} inputs is 1${m[2] ? " (the _N input is inverted first)" : ""}.`;
  if ((m = /^nor(\d)(b+)?$/.exec(f))) return `NOR gate: outputs 1 only when all ${N(+m[1])} inputs are 0${m[2] ? " (the _N input is inverted first)" : ""}.`;
  if (/^xor2/.test(f)) return "XOR gate: outputs 1 when exactly one of A and B is 1. It is the 'sum' part of adding two bits.";
  if (/^xnor2/.test(f)) return "XNOR gate: outputs 1 when A and B are equal. Comparators are built from these.";
  if (/^xor3/.test(f)) return "3-input XOR: outputs 1 when an odd number of inputs are 1 (the sum bit of a full adder).";
  if (/^xnor3/.test(f)) return "3-input XNOR: outputs 1 when an even number of inputs are 1.";
  if (/^ha/.test(f)) return "Half adder: adds two bits. SUM is A XOR B, COUT (carry) is A AND B.";
  if (/^fa/.test(f)) return "Full adder: adds three bits (A, B, carry in) into SUM and a carry out.";
  if (/^maj3/.test(f)) return "Majority gate: outputs 1 when at least two of its three inputs are 1 (the carry of an adder).";
  if ((m = /^([ao])(\d+)(b?)(o|oi|a|ai)$/.exec(f))) {
    const kind = m[1] === "a" ? "AND-OR" : "OR-AND";
    const fn = L && Object.values(L.function)[0];
    return `Compound gate (${kind}${/i$/.test(f) ? ", inverted" : ""}): ${fn ? toWordsSafe(fn) : f}. One cell does the work of several gates.`;
  }
  const fn = L && Object.values(L.function)[0];
  return fn ? `Logic cell: ${toWordsSafe(fn)}.` : "Standard cell.";
}

export function toWordsSafe(fn: string): string {
  try { return toWords(parse(fn)); } catch { return fn; }
}

export function fullName(type: string) { return `sky130_fd_sc_hd__${type}`; }

export function outputsOf(type: string, lib: Lib): string[] {
  const L = lib[type];
  if (L) return Object.entries(L.pins).filter(([, d]) => d === "out").map(([p]) => p);
  return [];
}

/** Output pin guess when lib.json lacks a type. */
export function isOutputPin(type: string, pin: string, lib: Lib): boolean {
  const L = lib[type];
  if (L && L.pins[pin]) return L.pins[pin] === "out";
  return /^(X|Y|Q|Q_N|HI|LO|SUM|COUT|CO)$/.test(pin);
}
