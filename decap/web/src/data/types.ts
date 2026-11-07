// Shapes shared with the Python side. See decap/CONTRACTS.md (§1 chip result, §2 bench, §3 design + lib, §4 API).

export interface LibFF {
  clocked_on: string;
  next_state: string;
  clear?: string;
  preset?: string;
  clear_preset_var1?: string;
  clear_preset_var2?: string;
  state?: string[]; // Liberty state variable names, e.g. ["IQ", "IQ_N"]
}
export interface LibCell {
  pins: Record<string, "in" | "out" | "inout">;
  function: Record<string, string>;
  area: number | null;
  ff: LibFF | null;
  phys?: boolean;
}
export type Lib = Record<string, LibCell>;

export interface DesignCell {
  id: string;
  type: string;
  x: number;
  y: number;
  w: number;
  orient: string;
  pins: Record<string, string>;
  module: string | null;
  inst?: string;
  src: number | null;
}
export interface Design {
  name: string;
  pdk: string;
  die: { w: number; h: number };
  core?: { x0: number; y0: number; x1: number; y1: number } | null;
  rowHeight: number;
  site: number;
  rows?: { y: number; orient: string }[];
  ports: { name: string; dir: "in" | "out" | "inout"; x: number; y: number }[];
  cells: DesignCell[];
  phys: { type: string; x: number; y: number; w: number; orient?: string }[];
  routes: { net: string; layer: string; pts: number[][] }[];
  power?: { net: string; layer: string; width: number; pts: number[][] }[];
  source: {
    files: Record<string, string>;
    modules: Record<string, { file: string; line: number }>;
    instances?: Record<string, string>;
    top?: string | null;
    mapping?: string;
  };
  credit?: string;
  counts?: Record<string, number>;
}

// ---- §1 chip result ----
export type Verdict = "proven" | "tested" | "failed";
export interface Block {
  id: string;
  kind: string;
  text: string;
  lines: [number, number];
  width?: number;
  cells: string[];
  recognised: boolean;
}
export interface Flag {
  id: string;
  kind: string;
  line: number;
  signal: string;
  condition: string;
  rate: string;
  cells: string[];
  cone: string[];
}
export interface NetlistCell {
  id: string;
  type: string;
  pins: Record<string, string>;
}
export interface ChipNetlist {
  ports: Record<string, { dir: "in" | "out" | "inout"; width: number }>;
  cells: NetlistCell[];
}
export interface ChipResult {
  chip: string;
  title?: string;
  author?: string;
  language?: string;
  split: "dev" | "test" | "warmup" | "user";
  variant: "clean" | "planted";
  stats: { logic_cells: number; flops: number; cell_types: number; area_um2: number };
  decompiled: { verilog: string; lines: number };
  blocks: Block[];
  coverage: { explained_cells: number; logic_cells: number; pct: number };
  verdict: Verdict;
  proof: {
    method: string;
    points?: number;
    proven?: number;
    seconds?: number;
    control_caught?: boolean;
    tested_cycles?: number;
    detail?: string;
  };
  author_source?: {
    repo: string;
    commit: string;
    url: string;
    files: Record<string, string>;
    lines: number;
    check: "proven" | "bounded" | "failed" | "unavailable";
    check_detail?: string;
  } | null;
  netlist: ChipNetlist;
  cell_lines: Record<string, number>;
  flags: Flag[];
  plant?: { n: number; target: string; inserted_cells: string[] } | null;
}

// ---- §2 bench ----
export interface BenchSummary {
  n: number;
  proven: number;
  tested: number;
  failed: number;
  coverage_pct: number;
}
export interface BenchChip {
  chip: string;
  title?: string;
  split: "dev" | "test" | "warmup" | "user";
  verdict: Verdict;
  logic_cells: number;
  flops: number;
  lines_decompiled: number;
  lines_author: number | null;
  coverage_pct: number;
  seconds: number;
  author_check?: string;
  trojan?: { n: number; caught: boolean; flag_line: number | null; clean_flags: number } | null;
}
export interface Bench {
  generated: string;
  split: { frozen: string; dev: string[]; test: string[] };
  summary: {
    test: BenchSummary;
    dev: BenchSummary;
    trojan?: { planted: number; caught: number; clean: number; clean_flagged: number };
  };
  chips: BenchChip[];
}

// ---- §4 API ----
export interface FixResult {
  removed_cells: string[];
  verdict: Verdict;
  detail: string;
  seconds: number;
  verilog: string;
}
export interface Neighbour {
  chip: string;
  title: string;
  score: number;
  why: string;
  logic_cells: number;
  area_um2: number;
  blocks: string[] | Record<string, number> | string;
  excerpt: string;
}
export interface CompareResult {
  fingerprint: Record<string, unknown>;
  neighbours: Neighbour[];
}
export interface ChatReply {
  reply: string;
  highlights?: { cells?: string[]; lines?: number[] };
}
