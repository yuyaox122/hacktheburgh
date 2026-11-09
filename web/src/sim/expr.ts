// Liberty boolean function strings -> AST -> compiled evaluator, plus a plain-English rendering.
// Grammar (Liberty precedence, highest first): postfix ' and prefix !, ^, & * (or juxtaposition), | +.
// SKY130 HD uses only ! & | ^ and parentheses, e.g. "(A1&A2) | (!B1_N)".

export type Expr =
  | { op: "var"; name: string }
  | { op: "const"; v: 0 | 1 }
  | { op: "not"; a: Expr }
  | { op: "and" | "or" | "xor"; args: Expr[] };

const TOKEN = /\s*([A-Za-z_][A-Za-z0-9_]*|1'b[01]|[01]|[!'&*|+^()])/y;

function tokenize(s: string): string[] {
  const out: string[] = [];
  TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while (TOKEN.lastIndex < s.length && (m = TOKEN.exec(s))) out.push(m[1]);
  if (s.slice(TOKEN.lastIndex).trim()) throw new Error(`Cannot parse function "${s}" near "${s.slice(TOKEN.lastIndex)}"`);
  return out;
}

export function parse(s: string): Expr {
  const t = tokenize(s);
  let i = 0;
  const peek = () => t[i];
  const startsPrimary = (x: string | undefined) => x !== undefined && (x === "(" || x === "!" || /^[A-Za-z_01]/.test(x));
  function primary(): Expr {
    const x = t[i++];
    if (x === undefined) throw new Error(`Unexpected end of "${s}"`);
    let e: Expr;
    if (x === "!") e = { op: "not", a: primary() };
    else if (x === "(") {
      e = orE();
      if (t[i++] !== ")") throw new Error(`Missing ) in "${s}"`);
    } else if (x === "0" || x === "1'b0") e = { op: "const", v: 0 };
    else if (x === "1" || x === "1'b1") e = { op: "const", v: 1 };
    else if (/^[A-Za-z_]/.test(x)) e = { op: "var", name: x };
    else throw new Error(`Unexpected "${x}" in "${s}"`);
    while (peek() === "'") { i++; e = { op: "not", a: e }; }
    return e;
  }
  function xorE(): Expr {
    const args = [primary()];
    while (peek() === "^") { i++; args.push(primary()); }
    return args.length === 1 ? args[0] : { op: "xor", args };
  }
  function andE(): Expr {
    const args = [xorE()];
    for (;;) {
      if (peek() === "&" || peek() === "*") { i++; args.push(xorE()); }
      else if (startsPrimary(peek())) args.push(xorE()); // juxtaposition = AND
      else break;
    }
    return args.length === 1 ? args[0] : { op: "and", args };
  }
  function orE(): Expr {
    const args = [andE()];
    while (peek() === "|" || peek() === "+") { i++; args.push(andE()); }
    return args.length === 1 ? args[0] : { op: "or", args };
  }
  const e = orE();
  if (i !== t.length) throw new Error(`Trailing tokens in "${s}"`);
  return e;
}

export function vars(e: Expr, out = new Set<string>()): Set<string> {
  if (e.op === "var") out.add(e.name);
  else if (e.op === "not") vars(e.a, out);
  else if (e.op !== "const") e.args.forEach((a) => vars(a, out));
  return out;
}

/**
 * JS source for an expression, with `ref(name)` giving each variable's JS. Bit mode (the simulator) works on 0/1;
 * word mode (the verify engine) on 32 lanes at once, so a constant 1 is all ones and NOT is a bitwise complement.
 */
export function toJs(e: Expr, ref: (name: string) => string, word = false): string {
  const go = (x: Expr) => toJs(x, ref, word);
  switch (e.op) {
    case "var": return ref(e.name);
    case "const": return word ? (e.v ? "-1" : "0") : String(e.v);
    case "not": return word ? `(~${go(e.a)})` : `(1^${go(e.a)})`;
    case "and": return `(${e.args.map(go).join("&")})`;
    case "or": return `(${e.args.map(go).join("|")})`;
    case "xor": return `(${e.args.map(go).join("^")})`;
  }
}

export function evalExpr(e: Expr, env: Record<string, number>): number {
  switch (e.op) {
    case "var": return env[e.name] ? 1 : 0;
    case "const": return e.v;
    case "not": return evalExpr(e.a, env) ^ 1;
    case "and": return e.args.every((a) => evalExpr(a, env)) ? 1 : 0;
    case "or": return e.args.some((a) => evalExpr(a, env)) ? 1 : 0;
    case "xor": return e.args.reduce((s, a) => s ^ evalExpr(a, env), 0);
  }
}

/** Verilog expression (used by the golden check's generated cell models). */
export function toVerilog(e: Expr): string {
  switch (e.op) {
    case "var": return e.name;
    case "const": return `1'b${e.v}`;
    case "not": return `~${toVerilog(e.a)}`;
    case "and": return `(${e.args.map(toVerilog).join(" & ")})`;
    case "or": return `(${e.args.map(toVerilog).join(" | ")})`;
    case "xor": return `(${e.args.map(toVerilog).join(" ^ ")})`;
  }
}

/** "(A1 AND A2) OR NOT B1_N" style rendering for the inspector. */
export function toWords(e: Expr, top = true): string {
  switch (e.op) {
    case "var": return e.name;
    case "const": return e.v ? "always 1" : "always 0";
    case "not": return `NOT ${toWords(e.a, false)}`;
    default: {
      const s = e.args.map((a) => toWords(a, false)).join(` ${e.op.toUpperCase()} `);
      return top ? s : `(${s})`;
    }
  }
}
