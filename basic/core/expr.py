"""Small boolean-expression toolkit for writing readable Verilog.

AST nodes (tuples):
    ("v", "name")          a 1-bit signal
    ("c", 0 | 1)           a constant
    ("not", e)
    ("and", [e, …]), ("or", [e, …]), ("xor", [e, …])
    ("mux", s, a1, a0)     s ? a1 : a0
    ("raw", "text", prec)  pre-rendered text (word-level ops, comparisons); prec as in PREC
"""
from __future__ import annotations

import re
from functools import lru_cache

from .lib import FRIENDLY

PREC = {"mux": 1, "or": 2, "xor": 3, "and": 4, "not": 6, "v": 9, "c": 9}
SYM = {"and": " & ", "or": " | ", "xor": " ^ "}

ONE, ZERO = ("c", 1), ("c", 0)


def V(n):
    return ("v", n)


def NOT(e):
    return ("not", e)


# ---------- parse the FRIENDLY cell strings ("~((A1 & A2) | B1)", "S ? A1 : A0")
@lru_cache(maxsize=None)
def parse_friendly(s: str):
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[~&|^()?:]", s)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def p_mux():
        c = p_bin("or")
        if peek() == "?":
            take(); a = p_mux(); take(); b = p_mux()
            return ("mux", c, a, b)
        return c

    order = ["or", "xor", "and"]
    sym = {"or": "|", "xor": "^", "and": "&"}

    def p_bin(op):
        nxt = order.index(op) + 1
        sub = (lambda: p_bin(order[nxt])) if nxt < len(order) else p_un
        xs = [sub()]
        while peek() == sym[op]:
            take(); xs.append(sub())
        return xs[0] if len(xs) == 1 else (op, tuple(xs))

    def p_un():
        t = take()
        if t == "~":
            return ("not", p_un())
        if t == "(":
            e = p_mux(); take(); return e
        return ("v", t)

    return p_mux()


def subst(e, m: dict):
    k = e[0]
    if k == "v":
        return m.get(e[1], e)
    if k in ("c", "raw"):
        return e
    if k == "not":
        return ("not", subst(e[1], m))
    if k == "mux":
        return ("mux", subst(e[1], m), subst(e[2], m), subst(e[3], m))
    return (k, tuple(subst(x, m) for x in e[1]))


def from_cell(cell, pin: str, ins: dict):
    if cell.base in FRIENDLY and len(cell.outputs) == 1:
        return simplify(subst(parse_friendly(FRIENDLY[cell.base]), ins))
    return simplify(subst(_lib_to_ast(cell.outputs[pin]), ins))


def _lib_to_ast(e):
    k = e[0]
    if k == "var":
        return ("v", e[1])
    if k == "const":
        return ("c", e[1])
    if k == "not":
        return ("not", _lib_to_ast(e[1]))
    if k == "mux":
        return ("mux", _lib_to_ast(e[1]), _lib_to_ast(e[2]), _lib_to_ast(e[3]))
    return (k, tuple(_lib_to_ast(x) for x in e[1]))


# ---------- simplification
def simplify(e):
    k = e[0]
    if k in ("v", "c", "raw"):
        return e
    if k == "not":
        a = simplify(e[1])
        if a[0] == "c":
            return ("c", 1 - a[1])
        if a[0] == "not":
            return a[1]
        return ("not", a)
    if k == "mux":
        s, a, b = simplify(e[1]), simplify(e[2]), simplify(e[3])
        if s[0] == "c":
            return a if s[1] else b
        if a == b:
            return a
        if a == ONE and b == ZERO:
            return s
        if a == ZERO and b == ONE:
            return simplify(("not", s))
        if b == ZERO:
            return simplify(("and", [s, a]))
        if a == ONE:
            return simplify(("or", [s, b]))
        if a == ZERO:
            return simplify(("and", [("not", s), b]))
        if b == ONE:
            return simplify(("or", [("not", s), a]))
        if s[0] == "not":
            return ("mux", s[1], b, a)
        return ("mux", s, a, b)
    xs = []
    for x in e[1]:
        x = simplify(x)
        if x[0] == k:
            xs.extend(x[1])
        else:
            xs.append(x)
    if k == "and":
        if ZERO in xs:
            return ZERO
        xs = [x for x in xs if x != ONE]
        xs = list(dict.fromkeys(xs))
        if any(("not", x) in xs for x in xs):
            return ZERO
    elif k == "or":
        if ONE in xs:
            return ONE
        xs = [x for x in xs if x != ZERO]
        xs = list(dict.fromkeys(xs))
        if any(("not", x) in xs for x in xs):
            return ONE
    else:  # xor
        inv = 0
        ys = []
        for x in xs:
            if x[0] == "c":
                inv ^= x[1]
            elif x[0] == "not":
                inv ^= 1
                ys.append(x[1])
            else:
                ys.append(x)
        # cancel pairs
        cnt: dict = {}
        for y in ys:
            cnt[y] = cnt.get(y, 0) ^ 1
        ys = [y for y in dict.fromkeys(ys) if cnt[y]]
        if not ys:
            return ("c", inv)
        r = ys[0] if len(ys) == 1 else ("xor", tuple(ys))
        return ("not", r) if inv else r
    if not xs:
        return ONE if k == "and" else ZERO
    return xs[0] if len(xs) == 1 else (k, tuple(xs))


def size(e) -> int:
    k = e[0]
    if k in ("v", "c"):
        return 1
    if k == "raw":
        return max(1, len(e[1]) // 6)
    if k == "not":
        return size(e[1])
    if k == "mux":
        return 1 + size(e[1]) + size(e[2]) + size(e[3])
    return sum(size(x) for x in e[1]) + len(e[1]) - 1


def render(e, parent: int = 0) -> str:
    k = e[0]
    if k == "v":
        return e[1]
    if k == "c":
        return f"1'b{e[1]}"
    if k == "raw":
        t, p = e[1], e[2]
        return f"({t})" if p < parent else t
    if k == "not":
        a = e[1]
        if a[0] == "raw" and a[2] >= 9:
            return "~" + a[1]
        if a[0] in ("v",):
            return "~" + render(a, 9)
        return "~(" + render(a, 0) + ")"
    if k == "mux":
        t = f"{render(e[1], 2)} ? {render(e[2], 1)} : {render(e[3], 1)}"
        return f"({t})" if parent > 1 else t
    p = PREC[k]
    # always bracket a different binary operator inside this one: "(a & b) | c" reads better than "a & b | c"
    t = SYM[k].join(render(x, 10 if x[0] in SYM else p + 1) for x in e[1])
    return f"({t})" if p < parent else t


def support(e, out=None) -> set:
    out = set() if out is None else out
    k = e[0]
    if k == "v":
        out.add(e[1])
    elif k == "not":
        support(e[1], out)
    elif k == "mux":
        for x in e[1:]:
            support(x, out)
    elif k in ("and", "or", "xor"):
        for x in e[1]:
            support(x, out)
    return out


# ---------- two-level minimisation for small functions (Quine-McCluskey, ≤ 6 inputs)
def minimise(names: list[str], tt: int):
    """tt bit j = f(pattern j), pattern j assigns names[i] = (j >> i) & 1. Returns the smaller of SOP / POS-as-NOT."""
    n = len(names)
    full = (1 << (1 << n)) - 1
    tt &= full
    if tt == 0:
        return ZERO
    if tt == full:
        return ONE
    # parity / single-literal shortcuts
    for i in range(n):
        m = _var_tt(i, n)
        if tt == m:
            return V(names[i])
        if tt == full & ~m:
            return NOT(V(names[i]))
    par = 0
    for i in range(n):
        par ^= _var_tt(i, n)
    if tt == par:
        return ("xor", tuple(V(x) for x in names))
    if tt == full & ~par:
        return NOT(("xor", tuple(V(x) for x in names)))
    a = _sop(names, tt)
    b = NOT(_sop(names, full & ~tt))
    return a if size(a) <= size(b) else simplify(b)


@lru_cache(maxsize=None)
def _var_tt(i: int, n: int) -> int:
    t = 0
    for j in range(1 << n):
        if (j >> i) & 1:
            t |= 1 << j
    return t


def _sop(names, tt):
    n = len(names)
    mins = [j for j in range(1 << n) if (tt >> j) & 1]
    # implicants as (value, care-mask)
    groups = {(m, (1 << n) - 1) for m in mins}
    primes = set()
    while groups:
        nxt = set()
        used = set()
        gl = list(groups)
        for i, (v1, c1) in enumerate(gl):
            for v2, c2 in gl[i + 1:]:
                if c1 != c2:
                    continue
                d = (v1 ^ v2) & c1
                if d and not (d & (d - 1)):
                    nxt.add((v1 & ~d, c1 & ~d))
                    used.add((v1, c1)); used.add((v2, c2))
        primes |= groups - used
        groups = nxt
    # greedy cover
    def covers(p, m):
        return (m & p[1]) == (p[0] & p[1])
    left = set(mins)
    chosen = []
    plist = sorted(primes, key=lambda p: -bin(~p[1] & ((1 << n) - 1)).count("1"))
    while left:
        best = max(plist, key=lambda p: (sum(1 for m in left if covers(p, m)), -bin(p[1]).count("1")))
        chosen.append(best)
        left = {m for m in left if not covers(best, m)}
    terms = []
    for v, c in chosen:
        lits = [V(names[i]) if (v >> i) & 1 else NOT(V(names[i])) for i in range(n) if (c >> i) & 1]
        terms.append(lits[0] if len(lits) == 1 else ("and", tuple(lits)))
    return simplify(terms[0] if len(terms) == 1 else ("or", tuple(terms)))
