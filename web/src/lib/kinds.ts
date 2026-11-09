// Words and colours for the blocks the decompiler recognises (ChipResult.blocks[].kind), shared by the Decompile and
// Compare pages and the agent's labels. Colours are token names from styles/tokens.css.

const KIND_WORD: Record<string, string> = {
  adder: "adder", subtractor: "subtractor", counter: "counter", register: "register", shift: "shift register", comparator: "comparator",
  mux: "multiplexer", reduce: "reduction", const: "constant", copy: "wire copy", logic: "leftover logic",
  table: "lookup table", bitwise: "bitwise block", multiplier: "multiplier",
};
const KIND_VAR: Record<string, string> = {
  adder: "--adder", subtractor: "--adder", counter: "--reg", register: "--reg", shift: "--reg",
  comparator: "--detect", mux: "--mux", reduce: "--gate", const: "--const", copy: "--const", logic: "--faint",
};

export const kindWord = (k: string) => KIND_WORD[k] ?? k;
/** A block kind's colour as a CSS value, e.g. "var(--adder)". */
export const kindColour = (k: string, fallback = "--faint") => `var(${KIND_VAR[k] ?? fallback})`;
/** "shift register" -> "Shift register". */
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
