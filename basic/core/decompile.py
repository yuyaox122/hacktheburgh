"""Decompile a stripped gate-level netlist into short, readable Verilog.

Structure first (SPEC §6.7): flip-flops are kept 1:1 and grouped into registers, register controls
(sync reset, enable) are peeled off by exact Shannon expansion on real nets, and what is left is
matched against word-level templates (adders, subtractors, counters, comparators, shifts, copies,
lookup tables) by bit-parallel simulation. Anything unmatched is written as small boolean logic.
Nothing here is trusted: `prove.py` checks the result against the netlist with Yosys.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import expr as X
from .netlist import Netlist
from .sim import Sim

IDENT = {"buf", "clkbuf", "dlygate4sd1", "dlygate4sd2", "dlygate4sd3", "dlymetal6s2s", "dlymetal6s4s", "dlymetal6s6s",
         "clkdlybuf4s15", "clkdlybuf4s18", "clkdlybuf4s25", "clkdlybuf4s50", "probe_p", "probec_p", "lpflow_bleeder"}
INVS = {"inv", "clkinv", "clkinvlp"}
RECOGNISED = {"const", "copy", "adder", "subtractor", "multiplier", "counter", "shift", "register", "comparator",
              "mux", "reduce", "table", "bitwise"}
TABLE_MAX_IN = 4


# ---------- bit-sliced word arithmetic (each element is a bit-parallel int, LSB first)
def w_add(a, b, W, m, cin=0):
    a = (a + [0] * W)[:W]; b = (b + [0] * W)[:W]
    out, c = [], cin
    for x, y in zip(a, b):
        out.append(x ^ y ^ c)
        c = (x & y) | (x & c) | (y & c)
    return out


def w_sub(a, b, W, m):
    a = (a + [0] * W)[:W]; b = (b + [0] * W)[:W]
    return w_add(a, [~y & m for y in b], W, m, cin=m)


def w_mul(a, b, W, m):
    acc = [0] * W
    for i, bi in enumerate(b):
        if i >= W:
            break
        pp = [0] * i + [x & bi for x in a]
        acc = w_add(acc, pp[:W], W, m)
    return acc


def w_lt(a, b, m):  # unsigned a < b: borrow out of a - b
    W = max(len(a), len(b)) + 1
    d = w_sub(a, b, W, m)
    return d[W - 1]


def w_eq(a, b, m):
    W = max(len(a), len(b))
    a = (a + [0] * W)[:W]; b = (b + [0] * W)[:W]
    r = m
    for x, y in zip(a, b):
        r &= ~(x ^ y) & m
    return r


@dataclass
class Word:
    name: str
    bits: list[int]

    def slice_text(self, lo, hi):
        if lo == 0 and hi == len(self.bits) - 1:
            return self.name
        if lo == hi:
            return f"{self.name}[{lo}]"
        return f"{self.name}[{hi}:{lo}]"


@dataclass
class Cand:
    text: str          # Verilog expression for the whole result
    bits: list[int]    # bit-parallel value of each result bit
    kind: str
    cost: int
    width: int = 0
    prec: int = 2      # how tightly the text binds (for parenthesising)


@dataclass
class Piece:
    lo: int            # first sink bit (inside the word being built)
    hi: int
    text: str          # Verilog expression giving exactly bits lo..hi
    kind: str
    exprs: list | None = None   # per-bit ASTs for logic pieces


@dataclass
class Block:
    id: str
    kind: str
    text: str
    sinks: list[int]
    recognised: bool
    lines: list[int] = field(default_factory=lambda: [0, 0])
    width: int = 1
    cells: set = field(default_factory=set)


class Decompiler:
    def __init__(self, nl: Netlist, n_patterns: int = 2048, seed: int = 7, structural_only: set | None = None,
                 names: dict | None = None, plain: bool = False):
        self.nl = nl
        self.s = Sim(nl)
        self.N = n_patterns
        self.env, self.m = self.s.random_env(n_patterns, seed, biased=True)
        self.src = set(self.s.sources)
        self._cache: dict = {}
        self.base = self.run({})
        self.name: dict[int, str] = {}       # source bit -> Verilog text
        self.words: list[Word] = []
        for pn, p in nl.ports.items():
            if p["dir"] != "in":
                continue
            for k, b in enumerate(p["bits"]):
                if isinstance(b, int):
                    self.name[b] = f"{pn}[{k}]" if len(p["bits"]) > 1 else pn
            if len(p["bits"]) > 1:
                self.words.append(Word(pn, p["bits"]))
        for k, b in enumerate(self.s.cuts):
            self.name[b] = f"loop{k}"
        self.proof_names: dict[int, str] = {b: f"loop{k}" for k, b in enumerate(self.s.cuts)}
        self.structural_only = structural_only or set()   # sink bits to write as plain logic (after a failed proof)
        self.user_names = names or {}
        self.plain = plain           # pure gate-by-gate transliteration (last-resort attempt)
        self.fanout = self._fanout()
        self.temps: dict[int, str] = {}
        self.temp_lines: list[str] = []
        self.decls: list[str] = []
        self.blocks: list[Block] = []
        self.counter = {}
        self.rn = lambda t: t       # placeholder register names -> final names (set by _emit)
        self._cond: dict = {}
        self.conds_used: dict = {}

    # ----- simulation helpers
    def run(self, force: dict):
        key = tuple(sorted(force.items()))
        if key not in self._cache:
            f = {b: (self.m if v else 0) for b, v in force.items()}
            self._cache[key] = self.s.run(self.env, self.m, f)
            if len(self._cache) > 400:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    def sig(self, b, force=None):
        if isinstance(b, str):
            return self.m if b == "1" else 0
        v = self.run(force or {})
        return v.get(b, 0)

    def fresh(self, stem):
        k = self.counter.get(stem, 0)
        self.counter[stem] = k + 1
        return f"{stem}{k}"

    def _fanout(self):
        f: dict[int, int] = {}
        for i in self.nl.insts:
            for p, b in i.conns.items():
                if p in i.cell.inputs and isinstance(b, int):
                    f[b] = f.get(b, 0) + 1
        for _, _, b in self.nl.out_bits():
            if isinstance(b, int):
                f[b] = f.get(b, 0) + 1
        return f

    # ----- clock / reset tracing through buffers and inverters (exact)
    def trace(self, b):
        inv = False
        seen = 0
        while isinstance(b, int) and b not in self.src and seen < 64:
            d = self.s.drv.get(b)
            if not d:
                break
            i, _ = d
            if i.cell.base in IDENT:
                b = i.conns.get("A")
            elif i.cell.base in INVS:
                b = i.conns.get("A"); inv = not inv
            else:
                break
            seen += 1
        return b, inv

    def signal_text(self, b, stem):
        """A clock/reset net as text; derived ones become named wires (matched in the proof)."""
        if isinstance(b, str):
            return f"1'b{b}"
        if b in self.name:
            return self.name[b]
        n = self.fresh(stem)
        txt = self.raw_text(b)
        note = "  // derived clock: written gate for gate (delay-line pulse generators are constant in zero-delay logic)" \
            if stem == "gclk" else ""
        self.temp_lines.append(f"wire {n} = {txt};{note}")
        self.proof_names[b] = n
        self.name[b] = n
        return n

    def raw_text(self, b, depth=0):
        """Gate-for-gate text of a net, no simplification (for clock and reset nets)."""
        if isinstance(b, str):
            return f"1'b{b}"
        if b in self.name or depth > 200:
            return self.name.get(b, "1'b0")
        d = self.s.drv.get(b)
        if d is None:
            return "1'b0"
        inst, pin = d
        if inst.cell.base in IDENT:
            return self.raw_text(inst.conns.get("A"), depth + 1)
        ins = {p: ("v", self.raw_text(inst.conns.get(p, "0"), depth + 1)) for p in inst.cell.inputs}
        from .lib import FRIENDLY
        tmpl = X.parse_friendly(FRIENDLY[inst.cell.base]) if inst.cell.base in FRIENDLY else X._lib_to_ast(inst.cell.outputs[pin])
        return "(" + X.render(X.subst(tmpl, {k: ("raw", v, 9) for k, (_, v) in ins.items()})) + ")"

    # ----- structural expression of a net in terms of sources (with forced nets as constants)
    def struct(self, b, force: dict, depth=0):
        if isinstance(b, str):
            return X.ONE if b == "1" else X.ZERO
        if b in force:
            return X.ONE if force[b] else X.ZERO
        if b in self.name:
            return X.V(self.name[b])
        if b in self.temps and not self._touches(b, force):
            return X.V(self.temps[b])
        if depth > 0 or not force:
            c = self.condition(b, force)
            if c is not None:
                return c
        d = self.s.drv.get(b)
        if d is None:
            return X.ZERO
        inst, pin = d
        ins = {p: self.struct(inst.conns.get(p, "0"), force, depth + 1) for p in inst.cell.inputs}
        e = X.from_cell(inst.cell, pin, ins)
        if not force and self.fanout.get(b, 0) >= 2 and X.size(e) >= 4 and depth > 0:
            n = self.fresh("t")
            self.temps[b] = n
            self.proof_names[b] = n
            self.temp_lines.append(f"wire {n} = {X.render(e)};")
            return X.V(n)
        return e

    # ----- rare conditions: nets that are 1 (or 0) for exactly one value of a few operand words
    def rarity(self, b) -> float:
        """Fraction of uniform random patterns (first quarter of the env) where b is 1."""
        q = self.N // 4
        v = self.base.get(b, 0) & ((1 << q) - 1)
        return bin(v).count("1") / q

    def condition(self, b, force):
        """If b is `w1 == K1 && w2 == K2` (or its negation) over ≤ 16 support bits, return that as raw text."""
        if isinstance(b, str) or b in self.src or self.plain:
            return None
        key = (b, tuple(sorted(force.items())) if force else ())
        if key in self._cond:
            return self._cond[key]
        self._cond[key] = None
        if not force:
            p = self.rarity(b)
            if 1 / 16 < p < 15 / 16:
                return None
        sup = self.support([b], force)
        if not 3 <= len(sup) <= 16 or not all(x in self.name for x in sup):
            return None
        tt, order = self.truth(b, force, sup)
        n = len(order)
        full = (1 << (1 << n)) - 1
        ones = bin(tt).count("1")
        if ones == 1:
            j = tt.bit_length() - 1; neg = False
        elif ones == (1 << n) - 1:
            j = (full & ~tt).bit_length() - 1; neg = True
        else:
            e = self._sum_condition(order, tt)
            if e is not None:
                self._cond[key] = e
                self.conds_used[b] = {"text": e[1], "ones": ones, "space": 1 << n, "neg": False, "bits": n}
            return e
        terms = self._eq_terms(order, j)
        if neg:
            def flip(t):
                if " == " in t:
                    return t.replace(" == ", " != ")
                return t[1:] if t.startswith("!") else "!" + t
            t = " || ".join(flip(t) for t in terms)
            e = ("raw", t, 1 if len(terms) == 1 else 0)
        else:
            e = ("raw", " && ".join(terms), 1 if len(terms) == 1 else 0)
        self._cond[key] = e
        self.conds_used[b] = {"text": e[1], "ones": 1, "space": 1 << n, "neg": neg, "bits": n}
        return e

    def _sum_condition(self, order, tt):
        """tt over two whole operand words (either bit order): is it `A + B == K` (or A - B, B - A)?"""
        groups: dict = {}
        for k, x in enumerate(order):
            w, i = self._bitkey(x)
            groups.setdefault(w, []).append((i, k))
        if len(groups) != 2:
            return None
        (wa, ia), (wb, ib) = list(groups.items())
        ia.sort(); ib.sort()
        if [i for i, _ in ia] != list(range(len(ia))) or [i for i, _ in ib] != list(range(len(ib))):
            return None
        n = len(order)
        sat = [j for j in range(1 << n) if (tt >> j) & 1]
        for ra in (False, True):
            for rb in (False, True):
                pa = [k for _, k in (reversed(ia) if ra else ia)]
                pb = [k for _, k in (reversed(ib) if rb else ib)]
                val = lambda j, pos: sum(((j >> k) & 1) << t for t, k in enumerate(pos))
                for op, f in (("+", lambda x, y: x + y), ("-", lambda x, y: x - y)):
                    ks = {f(val(j, pa), val(j, pb)) for j in sat}
                    if len(ks) != 1:
                        continue
                    K = ks.pop()
                    cnt = sum(1 for x in range(1 << len(pa)) for y in range(1 << len(pb)) if f(x, y) == K)
                    if cnt == len(sat):
                        ta = self._word_text(wa, len(pa), ra)
                        tb = self._word_text(wb, len(pb), rb)
                        W = max(len(pa), len(pb)) + 1
                        kt = f"{W}'d{K}" if K >= 0 else f"-{W}'d{-K}"
                        return ("raw", f"{ta} {op} {tb} == {kt}", 1)
        return None

    def _word_text(self, w, n, rev):
        wd = next((ww for ww in self.words if ww.name == w), None)
        t = w if wd is not None and len(wd.bits) == n else f"{w}[{n - 1}:0]"
        return "{<<{" + t + "}}" if rev else t

    def truth(self, b, force, sup):
        order = sorted(sup, key=lambda x: self._bitkey(x))
        env, m = Sim.exhaustive_env(order)
        full = {x: 0 for x in self.s.sources}
        full.update(env)
        f = {k: (m if v else 0) for k, v in force.items()}
        return self.s.run(full, m, f).get(b, 0), order

    def _bitkey(self, x):
        n = self.name.get(x, "")
        mm = __import__("re").match(r"(\w+)\[(\d+)\]$", n)
        return (mm.group(1), int(mm.group(2))) if mm else (n, 0)

    def _eq_terms(self, order, j):
        """Pattern index j over `order` bits -> ['ui_in == 8'd137', 'r0[3:0] == 4'd9', 'en']."""
        groups: dict = {}
        for k, x in enumerate(order):
            w, i = self._bitkey(x)
            groups.setdefault(w, []).append((i, (j >> k) & 1, x))
        terms = []
        for w, items in groups.items():
            items.sort()
            idx = [i for i, _, _ in items]
            # split into contiguous runs
            runs, cur = [], [items[0]]
            for it in items[1:]:
                if it[0] == cur[-1][0] + 1:
                    cur.append(it)
                else:
                    runs.append(cur); cur = [it]
            runs.append(cur)
            for run in runs:
                lo, hi = run[0][0], run[-1][0]
                val = sum(v << (i - lo) for i, v, _ in run)
                nm = self.name[run[0][2]]
                if "[" not in nm:
                    terms.append(nm if val else f"!{nm}")
                    continue
                wd = next((ww for ww in self.words if ww.name == w), None)
                full_w = wd is not None and lo == 0 and hi == len(wd.bits) - 1
                txt = w if full_w else (f"{w}[{lo}]" if lo == hi else f"{w}[{hi}:{lo}]")
                n = hi - lo + 1
                if n == 1:
                    terms.append(txt if val else f"!{txt}")
                else:
                    terms.append(f"{txt} == {n}'d{val}")
        return terms

    def _touches(self, b, force):
        """Does any forced net lie in the cone of b?"""
        if not force:
            return False
        stack, seen = [b], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in force:
                return True
            if x not in self.src:
                stack.extend(self.s.fanin.get(x, []))
        return False

    def support(self, bits, force):
        """Source bits the given nets depend on, with forced nets cut."""
        sup, seen = set(), set()
        stack = [b for b in bits if isinstance(b, int)]
        while stack:
            x = stack.pop()
            if x in seen or x in force:
                continue
            seen.add(x)
            if x in self.src:
                sup.add(x)
                continue
            stack.extend(self.s.fanin.get(x, []))
        return sup

    def logic_expr(self, b, force):
        """Smallest of: minimised truth table (≤ 5 inputs) and the structural expression."""
        st = X.simplify(self.struct(b, force))
        sup = sorted(self.support([b], force), key=lambda x: self.name.get(x, ""))
        if 0 < len(sup) <= 5:
            env, m = Sim.exhaustive_env(sup)
            # evaluate under force with only the support varying
            full = {s: 0 for s in self.s.sources}
            full.update(env)
            f = {k: (m if v else 0) for k, v in force.items()}
            tt = self.s.run(full, m, f).get(b, 0) if not isinstance(b, str) else (m if b == "1" else 0)
            mn = X.minimise([self.name[s] for s in sup], tt)
            if X.size(mn) < X.size(st):
                return mn
        if not sup and not isinstance(b, str):
            v = self.sig(b, force)
            if v == 0:
                return X.ZERO
            if v == self.m:
                return X.ONE
        return st

    # ----- candidate operands and word-level templates
    def operand_slices(self, sup: set, extra_words: list[Word]):
        out = []
        for w in self.words + extra_words:
            idx = [k for k, b in enumerate(w.bits) if b in sup]
            if not idx:
                continue
            runs, start = [], idx[0]
            for a, b in zip(idx, idx[1:] + [None]):
                if b != a + 1:
                    runs.append((start, a)); start = b
            for lo, hi in runs:
                out.append((w, lo, hi))
                n = hi - lo + 1
                if n >= 4 and n % 2 == 0:
                    out.append((w, lo, lo + n // 2 - 1)); out.append((w, lo + n // 2, hi))
        # dedupe
        seen, res = set(), []
        for w, lo, hi in out:
            k = (w.name, lo, hi)
            if k not in seen:
                seen.add(k); res.append((w, lo, hi))
        return res[:12]

    def candidates(self, sup: set, width: int, extra_words: list[Word], force):
        v = self.run(force)
        m = self.m
        sl = self.operand_slices(sup, extra_words)
        val = lambda w, lo, hi: [v.get(b, 0) if b not in force else (m if force[b] else 0) for b in w.bits[lo:hi + 1]]
        cs: list[Cand] = []
        ops = [(w, lo, hi, val(w, lo, hi), w.slice_text(lo, hi)) for w, lo, hi in sl]
        multi = {bb for w, lo, hi in sl if hi > lo for bb in w.bits[lo:hi + 1]}
        single = sorted((b for b in sup if b not in multi), key=lambda b: self.name.get(b, ""))
        for w, lo, hi, a, t in ops:
            n = hi - lo + 1
            cs.append(Cand(t, a, "copy", 1, n, 9))
            if n >= 2:
                W = max(n + 1, width)
                cs.append(Cand(f"{t} + 1'b1", w_add(a, [], W, m, cin=m), "adder", 3, W))
                cs.append(Cand(f"{t} - 1'b1", w_sub(a, [m], W, m), "subtractor", 3, W))
                cs.append(Cand(f"&{t}", [_and_all(a, m)], "reduce", 2, 1, 6))
                cs.append(Cand(f"|{t}", [_or_all(a)], "reduce", 2, 1, 6))
                cs.append(Cand(f"^{t}", [_xor_all(a)], "reduce", 2, 1, 6))
        for i, (w1, l1, h1, a, t1) in enumerate(ops):
            for j, (w2, l2, h2, b, t2) in enumerate(ops):
                if i == j:
                    continue
                n1, n2 = h1 - l1 + 1, h2 - l2 + 1
                if n1 < 2 and n2 < 2:
                    continue
                if (w1.name, l1) == (w2.name, l2):
                    continue
                overl = w1.name == w2.name and not (h1 < l2 or h2 < l1)
                if overl:
                    continue
                W = max(n1, n2) + 1
                if i < j:
                    cs.append(Cand(f"{t1} + {t2}", w_add(a, b, max(W, width), m), "adder", 4, max(W, width)))
                    cs.append(Cand(f"{t1} & {t2}", [x & y for x, y in zip(_pad(a, W), _pad(b, W))][:max(n1, n2)], "bitwise", 2, max(n1, n2), 4))
                    cs.append(Cand(f"{t1} | {t2}", [x | y for x, y in zip(_pad(a, W), _pad(b, W))][:max(n1, n2)], "bitwise", 2, max(n1, n2), 2))
                    cs.append(Cand(f"{t1} ^ {t2}", [x ^ y for x, y in zip(_pad(a, W), _pad(b, W))][:max(n1, n2)], "bitwise", 2, max(n1, n2), 3))
                    cs.append(Cand(f"{t1} == {t2}", [w_eq(a, b, m)], "comparator", 3, 1, 1))
                    if n1 + n2 <= 16:
                        Wm = max(n1 + n2, width)
                        cs.append(Cand(f"{t1} * {t2}", w_mul(a, b, Wm, m), "multiplier", 6, Wm))
                    for c in single[:4]:
                        cs.append(Cand(f"{t1} + {t2} + {self.name[c]}", w_add(a, b, max(W, width), m, cin=self.sig(c, force) if c not in force else (m if force[c] else 0)), "adder", 5, max(W, width)))
                cs.append(Cand(f"{t1} - {t2}", w_sub(a, b, max(W, width), m), "subtractor", 4, max(W, width)))
                cs.append(Cand(f"{t1} < {t2}", [w_lt(a, b, m)], "comparator", 3, 1, 1))
        for c in single[:4]:
            for w, lo, hi, a, t in ops:
                if hi > lo:
                    cv = self.sig(c, force) if c not in force else (m if force[c] else 0)
                    W = max(hi - lo + 2, width)
                    cs.append(Cand(f"{t} + {self.name[c]}", w_add(a, [], W, m, cin=cv), "adder", 4, W))
        return cs

    def const_compare(self, b, sup, force):
        """1-bit sink that depends on one operand slice only: == K, != K, >= K, < K."""
        if not 2 <= len(sup) <= 14 or isinstance(b, str):
            return None
        sl = [s for s in self.operand_slices(sup, self._extra) if set(s[0].bits[s[1]:s[2] + 1]) == sup]
        if not sl:
            return None
        w, lo, hi = sl[0]
        bits = w.bits[lo:hi + 1]
        env, m = Sim.exhaustive_env(bits)
        full = {s: 0 for s in self.s.sources}
        full.update(env)
        f = {k: (m if v else 0) for k, v in force.items()}
        tt = self.s.run(full, m, f).get(b, 0)
        n = len(bits)
        ones = [j for j in range(1 << n) if (tt >> j) & 1]
        t = w.slice_text(lo, hi)
        if len(ones) == 1:
            return ("raw", f"{t} == {n}'d{ones[0]}", 1)
        if len(ones) == (1 << n) - 1:
            z = next(j for j in range(1 << n) if not (tt >> j) & 1)
            return ("raw", f"{t} != {n}'d{z}", 1)
        if ones and ones == list(range(ones[0], 1 << n)):
            return ("raw", f"{t} >= {n}'d{ones[0]}", 1)
        if ones and ones == list(range(0, len(ones))):
            return ("raw", f"{t} < {n}'d{len(ones)}", 1)
        return None

    # ----- build a word (list of sink nets) as pieces
    def build(self, bits: list, force: dict, extra_words: list[Word], allow_table=True) -> list[Piece]:
        self._extra = extra_words
        m = self.m
        sigs = [self.sig(b, force) for b in bits]
        sup = self.support(bits, force)
        n = len(bits)
        covered = [None] * n
        # constants first
        for k, s in enumerate(sigs):
            if isinstance(bits[k], str) or (not self.support([bits[k]], force) and s in (0, m)):
                covered[k] = ("const", 1 if s == m else 0)
        if any(bits[k] in self.structural_only for k in range(n) if isinstance(bits[k], int)):
            cs = []
        else:
            cs = self.candidates(sup, n, extra_words, force) if sup else []
        sm = self._self_map(bits, force, extra_words, sup)
        if sm is not None:
            return [sm]
        index: dict[int, list] = {}
        for ci, c in enumerate(cs):
            for i, s in enumerate(c.bits):
                index.setdefault(s, []).append((ci, i))
        pieces: list[Piece] = []
        k = 0
        while k < n:
            if covered[k] is not None and covered[k][0] == "const":
                j = k
                while j + 1 < n and covered[j + 1] is not None and covered[j + 1][0] == "const":
                    j += 1
                val = sum(covered[t][1] << (t - k) for t in range(k, j + 1))
                w = j - k + 1
                pieces.append(Piece(k, j, f"{w}'b{val:0{w}b}" if w <= 8 else f"{w}'h{val:x}", "const"))
                k = j + 1
                continue
            if bits[k] in self.structural_only:
                pieces.append(self._logic_piece(bits, k, force)); k += 1
                continue
            best = None
            for ci, i in index.get(sigs[k], []):
                c = cs[ci]
                L = 0
                while k + L < n and i + L < len(c.bits) and c.bits[i + L] == sigs[k + L] and covered[k + L] is None \
                        and bits[k + L] not in self.structural_only:
                    L += 1
                score = (L, i == 0, -c.cost)
                if best is None or score > best[0]:
                    best = (score, ci, i, L)
            if best and (best[3] >= 2 or cs[best[1]].kind in ("copy", "comparator", "reduce")):
                _, ci, i, L = best
                c = cs[ci]
                if k == 0 and L == n and i == 0 and c.kind != "copy":
                    # the whole word: write the operation inline (Verilog truncates to the left-hand width)
                    pieces.append(Piece(0, n - 1, c.text, c.kind))
                else:
                    pieces.append(self._cand_piece(c, k, k + L - 1, i))
                k += L
                continue
            # 1-bit comparator against a constant
            if isinstance(bits[k], int):
                cc = self.const_compare(bits[k], self.support([bits[k]], force), force)
                if cc:
                    pieces.append(Piece(k, k, X.render(cc), "comparator")); k += 1
                    continue
            pieces.append(self._logic_piece(bits, k, force)); k += 1
        # lookup table for a run of ≥3 logic bits sharing a small support
        if allow_table:
            pieces = self._tables(bits, pieces, force)
        return _merge_logic(pieces)

    def _self_map(self, bits, force, words, sup):
        """A register's next value as a function of its own value only: wrap counters (x == K ? L : x ± 1)."""
        if len(bits) < 2 or any(isinstance(b, str) or b in self.structural_only for b in bits):
            return None
        w = next((ww for ww in words if len(ww.bits) == len(bits) and set(ww.bits) == sup and len(sup) <= 12), None)
        if w is None or len(w.bits) != len(bits):
            return None
        n = len(bits)
        env, m = Sim.exhaustive_env(w.bits)
        full = {x: 0 for x in self.s.sources}
        full.update(env)
        f = {k: (m if v else 0) for k, v in force.items()}
        vals = self.s.run(full, m, f)
        nxt = [sum(((vals.get(b, 0) >> j) & 1) << t for t, b in enumerate(bits)) for j in range(1 << n)]
        M = (1 << n) - 1
        for step, op in ((1, "+"), (-1, "-")):
            diff = [x for x in range(1 << n) if nxt[x] != (x + step) & M]
            if not diff:
                return None   # plain x ± 1: the template matcher handles it
            if len(diff) == 1:
                K, L = diff[0], nxt[diff[0]]
                t = f"({w.name} == {n}'d{K}) ? {n}'d{L} : {w.name} {op} 1'b1"
                return Piece(0, n - 1, t, "counter")
            # wraps early: every x > K jumps somewhere constant (synthesis keeps the out-of-range behaviour exact)
        return None

    def _cand_piece(self, c: Cand, lo, hi, i):
        L = hi - lo + 1
        if c.kind == "copy":
            # slice of a source word: rewrite the text as a direct slice
            return Piece(lo, hi, _slice_expr(c.text, i, i + L - 1, c.width), "copy")
        if i == 0 and L == c.width:
            return Piece(lo, hi, c.text if c.prec >= 9 else f"({c.text})", c.kind)
        # take a slice of a named intermediate result
        stem = {"adder": "sum", "subtractor": "diff", "multiplier": "prod", "comparator": "cmp", "bitwise": "w", "reduce": "red"}.get(c.kind, "w")
        nm = self.fresh(stem)
        self.temp_lines.append(f"wire [{c.width - 1}:0] {nm} = {c.text};" if c.width > 1 else f"wire {nm} = {c.text};")
        t = nm if (i == 0 and L == c.width) else (f"{nm}[{i}]" if L == 1 else f"{nm}[{i + L - 1}:{i}]")
        return Piece(lo, hi, t, c.kind)

    def _logic_piece(self, bits, k, force):
        e = self.logic_expr(bits[k], force)
        kind = "comparator" if e[0] == "raw" or (e[0] == "not" and e[1][0] == "raw") else "logic"
        return Piece(k, k, X.render(e), kind, [e])

    def _tables(self, bits, pieces, force):
        out, i = [], 0
        while i < len(pieces):
            j = i
            while j < len(pieces) and pieces[j].kind == "logic":
                j += 1
            run = pieces[i:j]
            if len(run) >= 3:
                rb = [bits[p.lo] for p in run]
                sup = sorted(self.support(rb, force), key=lambda x: self.name.get(x, ""))
                if 2 <= len(sup) <= TABLE_MAX_IN and not any(b in self.structural_only for b in rb):
                    out.append(self._table_piece(rb, run[0].lo, run[-1].lo, sup, force))
                    i = j
                    continue
            if run:
                out.extend(run); i = j
                continue
            out.append(pieces[i]); i += 1
        return out

    def _table_piece(self, rb, lo, hi, sup, force):
        env, m = Sim.exhaustive_env(sup)
        full = {s: 0 for s in self.s.sources}
        full.update(env)
        f = {k: (m if v else 0) for k, v in force.items()}
        v = self.s.run(full, m, f)
        w = len(rb)
        sel = "{" + ", ".join(self.name[s] for s in reversed(sup)) + "}" if len(sup) > 1 else self.name[sup[0]]
        nm = self.fresh("tbl")
        lines = [f"reg [{w - 1}:0] {nm};", f"always @* case ({sel})"]
        for j in range(1 << len(sup)):
            val = sum(((v.get(b, 0) >> j) & 1) << t for t, b in enumerate(rb))
            lab = "default" if j == (1 << len(sup)) - 1 else f"{len(sup)}'d{j}"
            lines.append(f"    {lab}: {nm} = {w}'b{val:0{w}b};")
        lines.append("endcase")
        self.temp_lines.extend(lines)
        return Piece(lo, hi, nm, "table")


def _pad(a, W):
    return (a + [0] * W)[:W]


def _and_all(a, m):
    r = m
    for x in a:
        r &= x
    return r


def _or_all(a):
    r = 0
    for x in a:
        r |= x
    return r


def _xor_all(a):
    r = 0
    for x in a:
        r ^= x
    return r


def _slice_expr(text, lo, hi, width):
    """text is `name`, `name[k]` or `name[h:l]`; return bits lo..hi of it."""
    if "[" not in text:
        if lo == 0 and hi == width - 1:
            return text
        return f"{text}[{lo}]" if lo == hi else f"{text}[{hi}:{lo}]"
    base, rng = text[:-1].split("[")
    l0 = int(rng.split(":")[-1])
    a, b = l0 + lo, l0 + hi
    return f"{base}[{a}]" if a == b else f"{base}[{b}:{a}]"


def _merge_logic(pieces):
    return pieces


# =====================================================================================================
# Registers, statements and the module
# =====================================================================================================
@dataclass
class Reg:
    name: str
    flops: list                 # Inst, LSB first
    key: tuple                  # (kind, clk, clk_inv, rst, rst_inv)
    q: list = field(default_factory=list)
    d: list = field(default_factory=list)


def _decompile(self: Decompiler) -> dict:
    nl, s, m = self.nl, self.s, self.m
    # ---------- 1. flop classes (clock, reset kind and net)
    classes: dict[tuple, list] = {}
    for f in s.flops:
        ff = f.cell.ff
        clk, cinv = self.trace(f.conns.get("CLK"))
        kind = "clear" if ff["clear"] else "preset" if ff["preset"] else "none"
        rpin = {"clear": "RESET_B", "preset": "SET_B"}.get(kind)
        r, rinv = self.trace(f.conns.get(rpin)) if rpin else (None, False)
        if isinstance(r, str) and (r == "1") != rinv:     # reset pin tied inactive
            kind, r, rinv = "none", None, False
        classes.setdefault((kind, clk, cinv, r, rinv), []).append(f)

    # ---------- 2. control scan: for every candidate net and value, which flops hold or go constant
    allq = {f.conns["Q"] for f in s.flops}
    dbits = {f.id: f.conns.get("D") for f in s.flops}
    cand_nets = set()
    for f in s.flops:
        cells, sup = s.cone([dbits[f.id]])
        cand_nets |= {f2 for c in cells for f2 in [_out_of(s, c)] if f2 is not None}
        cand_nets |= sup
    cand_nets -= allq
    cand_nets -= set(self.structural_only)
    self._ctl_cache = {}

    def ctl_scan(flops, force, allow_q=False):
        """keys (net, v, kind, consts) that apply to every flop in `flops` under `force`.
        allow_q: other registers' bits may act as controls (not while grouping, where carries would split counters)."""
        res = {}
        nets = set()
        for f in flops:
            cells, sup = s.cone([dbits[f.id]])
            nets |= {_out_of(s, c) for c in cells} | sup
        pool = cand_nets | (allq - {f.conns["Q"] for f in flops}) if allow_q else cand_nets
        nets = (nets & pool) - set(force)
        for c in sorted(nets, key=lambda x: (x not in self.src, x)):
            for v in (0, 1):
                ff = dict(force); ff[c] = v
                vals = self.run(ff)
                kinds, consts = set(), []
                for f in flops:
                    d = self.sig(dbits[f.id], ff)
                    q = vals.get(f.conns["Q"], 0)
                    if d == q:
                        kinds.add("hold")
                    elif d in (0, m) and not self.support([dbits[f.id]], ff):
                        kinds.add("const"); consts.append(1 if d == m else 0)
                    else:
                        kinds.add("x")
                        break
                if kinds in ({"hold"}, {"const"}):
                    # the other branch must still do something (otherwise the net is irrelevant)
                    fo = dict(force); fo[c] = 1 - v
                    if all(self.sig(dbits[f.id], fo) == self.sig(dbits[f.id], ff) for f in flops):
                        continue
                    k = kinds.pop()
                    vo = self.run(fo)
                    if k == "const" and all(not self.support([dbits[f.id]], fo) for f in flops):
                        continue      # D is just a function of this net: data, not control
                    if k == "hold" and all(self.sig(dbits[f.id], fo) == vo.get(f.conns["Q"], 0) for f in flops):
                        continue
                    res[(c, v, k, tuple(consts) if k == "const" else ())] = len(s.cone([c])[0]) if c not in self.src else 0
        return res

    regs: list[Reg] = []
    for key, flops in classes.items():
        for grp in self._group(flops, ctl_scan):
            regs.append(Reg("", grp, key))
    # ---------- 3. order bits and name registers
    # order bits; split off low parts that are self-contained wrap counters (minutes | hours sharing one enable)
    split_regs = []
    queue = list(regs)
    while queue:
        r = queue.pop(0)
        force = self._peel_force(r.flops, ctl_scan)
        r.flops = self._order(r.flops, force)
        k = self._wrap_prefix(r.flops, force) if len(r.flops) > 2 else None
        if k:
            split_regs.append(Reg("", r.flops[:k], r.key))
            queue.insert(0, Reg("", r.flops[k:], r.key))
        else:
            split_regs.append(r)
    regs = split_regs
    for k, r in enumerate(regs):
        r.name = f"rg{k}"
        r.q = [f.conns["Q"] for f in r.flops]
        r.d = [dbits[f.id] for f in r.flops]
        for i, b in enumerate(r.q):
            self.name[b] = f"{r.name}[{i}]" if len(r.q) > 1 else r.name
            self.proof_names[b] = self.name[b]
    regwords = [Word(r.name, r.q) for r in regs if len(r.q) > 1]
    self.words = self.words + regwords

    # ---------- 4. statements
    stmts = []   # (block, [lines])
    for r in regs:
        stmts.append(self._reg_stmt(r, ctl_scan, regwords))
    for pn, p in nl.ports.items():
        if p["dir"] != "out":
            continue
        stmts.append(self._port_stmt(pn, p["bits"], regwords))
    # loop cut points (ring oscillators): each is one equation in terms of itself
    for b in s.cuts:
        inst, pin = s.drv[b]
        ins = {pp: self.struct(inst.conns.get(pp, "0"), {}) for pp in inst.cell.inputs}
        e = X.from_cell(inst.cell, pin, ins)
        blk = Block("", "logic", f"assign {self.name[b]} = …", [b], False)
        blk._ok_bits = [(b, False)]
        stmts.append((blk, [f"assign {self.name[b]} = {X.render(e)};  // combinational loop"]))
    return self._emit(regs, stmts)


def _out_of(s: Sim, cell_id: str):
    inst = s._by_id[cell_id] if hasattr(s, "_by_id") else None
    if inst is None:
        s._by_id = {i.id: i for i in s.nl.insts}
        inst = s._by_id[cell_id]
    for p in inst.cell.outputs:
        b = inst.conns.get(p)
        if isinstance(b, int):
            return b
    return None


def _group(self: Decompiler, flops, ctl_scan):
    """Group same-clock flip-flops into registers.

    1. Bits the proof rejected earlier stand alone (written as plain logic).
    2. Each remaining flop gets its primary control: a sync-clear or enable net it shares with the most
       other flops. Flops with the same primary control form a register.
    3. Flops with no control join by direct dependency (counter carries, shift chains) or by copying
       the same input word.
    4. Groups split into dependency components when they hold unrelated bits.
    """
    s = self.s
    alone = [f for f in flops if f.conns.get("D") in self.structural_only]
    flops = [f for f in flops if f not in alone]
    out = [[f] for f in alone]
    if not flops:
        return out
    parent = {f.id: f.id for f in flops}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    byq = {f.conns["Q"]: f for f in flops}
    qset = set(byq)
    sup = {f.id: self.support([f.conns.get("D")], {}) for f in flops}
    keys = {f.id: [k[:3] for k in ctl_scan([f], {})] for f in flops} if len(flops) <= 96 else {f.id: [] for f in flops}
    pop: dict = {}
    for f in flops:
        for k in keys[f.id]:
            pop[k] = pop.get(k, 0) + 1
    primary = {}
    for f in flops:
        ks = [k for k in keys[f.id] if pop[k] >= 2]
        if ks:
            primary[f.id] = max(ks, key=lambda k: (k[2] == "const", pop[k], k[0] in self.src, -k[0]))
    byp: dict = {}
    for fid, k in primary.items():
        byp.setdefault(k, []).append(fid)
    for k, fs in byp.items():
        for x in fs[1:]:
            union(fs[0], x)
    free = [f for f in flops if f.id not in primary]
    for f in free:
        d = f.conns.get("D")
        for b in sup[f.id]:
            g = byq.get(b)
            if g is None or g.id == f.id or g.id in primary:
                continue
            if self.sig(d) == self.sig(b) or (f.conns["Q"] in sup[f.id] and (sup[g.id] & qset) <= sup[f.id]):
                union(f.id, g.id)
    portword = {b: w.name for w in self.words for b in w.bits}
    pw = {}
    for f in free:
        sp = sup[f.id]
        if sp and not (sp & set(self.s.q_bits)):
            key = tuple(sorted({portword.get(b, self.name.get(b, "")) for b in sp}))
            if key in pw:
                union(pw[key], f.id)
            else:
                pw[key] = f.id
    groups: dict = {}
    for f in flops:
        groups.setdefault(find(f.id), []).append(f)
    for g in groups.values():
        if len(g) <= 2:
            out.append(g)
            continue
        # dependency components (Q of one in D of another, or the same input word)
        ids = {f.id for f in g}
        par = {f.id: f.id for f in g}

        def fd(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        words_of = {f.id: {portword.get(b) for b in sup[f.id] if b in portword} - {None} for f in g}
        for f in g:
            for b in sup[f.id]:
                h = byq.get(b)
                if h is not None and h.id in ids and h.id != f.id:
                    par[fd(f.id)] = fd(h.id)
        for i, f in enumerate(g):
            for h in g[i + 1:]:
                if words_of[f.id] & words_of[h.id]:
                    par[fd(f.id)] = fd(h.id)
        comp: dict = {}
        for f in g:
            comp.setdefault(fd(f.id), []).append(f)
        for c in comp.values():
            for i in range(0, len(c), 32):
                out.append(c[i:i + 32])
    return out


def _peel_force(self: Decompiler, flops, ctl_scan) -> dict:
    """The force (control nets fixed to their 'active' branch) that the register statement will use."""
    if any(f.conns.get("D") in self.structural_only for f in flops) or len(flops) < 2:
        return {}
    force: dict = {}
    for _ in range(4):
        res = ctl_scan(flops, force, allow_q=True)
        if not any(k[2] == "const" for k in res):
            se = self._source_enable(flops, force)
            if se:
                outside, j = se
                force = dict(force)
                for t, x in enumerate(outside):
                    force[x] = (j >> t) & 1
                break
        if not res:
            break
        (net, v, k, consts), _ = min(res.items(), key=lambda kv: (kv[0][2] != "const", kv[1], kv[0][0] not in self.src))
        force = dict(force); force[net] = 1 - v
    return force


def _source_enable(self: Decompiler, flops, force):
    """If the register changes only for ONE value of the outside bits it reads (e.g. `!alarm && sec == 59`),
    return (order, j): those bits and the index of that value. The data is then read with them fixed."""
    key = (tuple(f.id for f in flops), tuple(sorted(force.items())), tuple(self.name.get(f.conns["Q"], "") for f in flops))
    cache = self.__dict__.setdefault("_se_cache", {})
    if key not in cache:
        cache[key] = _source_enable_raw(self, flops, force)
    return cache[key]


def _source_enable_raw(self: Decompiler, flops, force):
    own = [f.conns["Q"] for f in flops]
    ds = [f.conns.get("D") for f in flops]
    sup = self.support(ds, force)
    outside = sorted(sup - set(own), key=lambda x: self._bitkey(x))
    if not 1 <= len(outside) <= 14 or len(outside) + len(own) > 18:
        return None
    order = outside + own
    env, m = Sim.exhaustive_env(order)
    full = {x: 0 for x in self.s.sources}
    full.update(env)
    vals = self.s.run(full, m, {b: (m if v else 0) for b, v in force.items()})
    change = 0
    for f, d in zip(flops, ds):
        change |= Sim.val(vals, d, m) ^ vals.get(f.conns["Q"], 0)
    n_out = len(outside)
    active = set()
    for j in range(1 << len(order)):
        if (change >> j) & 1:
            active.add(j & ((1 << n_out) - 1))
            if len(active) > 1:
                return None
    if len(active) != 1:
        return None
    return outside, active.pop()


def _wrap_prefix(self: Decompiler, flops, force):
    """Smallest k (2..n-1) such that the low k bits only depend on themselves and count with a wrap."""
    n = len(flops)
    qs = [f.conns["Q"] for f in flops]
    ds = [f.conns.get("D") for f in flops]
    for k in range(2, min(n, 12)):
        low = set(qs[:k])
        sup = self.support(ds[:k], force)
        if not sup or not sup <= low:
            continue
        env, m = Sim.exhaustive_env(qs[:k])
        full = {x: 0 for x in self.s.sources}
        full.update(env)
        vals = self.s.run(full, m, {b: (m if v else 0) for b, v in force.items()})
        nxt = [sum(((vals.get(b, 0) >> j) & 1) << t for t, b in enumerate(ds[:k])) for j in range(1 << k)]
        M = (1 << k) - 1
        if sum(1 for x in range(1 << k) if nxt[x] != (x + 1) & M) == 1:
            return k
    return None


def _order(self: Decompiler, flops, force=None):
    force = force or {}
    if len(flops) == 1:
        return flops
    byq = {f.conns["Q"]: f for f in flops}
    qs = set(byq)
    # shift chain: D(f) == Q(g)
    nxt = {}
    vf = self.run(force)
    for f in flops:
        for g in flops:
            if g is not f and self.sig(f.conns.get("D"), force) == vf.get(g.conns["Q"], 0):
                nxt[g.id] = f
    if len(nxt) >= len(flops) - 1:
        heads = [f for f in flops if f.id not in {x.id for x in nxt.values()}]
        if len(heads) == 1:
            chain, cur = [], heads[0]
            while cur is not None and len(chain) < len(flops):
                chain.append(cur)
                cur = nxt.get(cur.id)
            if len(chain) == len(flops):
                return chain

    # registers that drive output-port bits directly take the port's bit order
    outpos = {}
    for pn, p in self.nl.ports.items():
        if p["dir"] == "out":
            for k, b in enumerate(p["bits"]):
                if isinstance(b, int):
                    t, inv = self.trace(b)
                    if t in qs and t not in outpos:
                        outpos[t] = (pn, k)
    if len(outpos) == len(flops) and len({v[0] for v in outpos.values()}) == 1:
        return sorted(flops, key=lambda f: outpos[f.conns["Q"]][1])

    def key(f):
        sup = self.support([f.conns.get("D")], {})
        own = len(sup & qs)
        pidx = min([k for w in self.words for k, b in enumerate(w.bits) if b in sup] or [99])
        return (own, pidx, int(f.id[1:]))
    # counters: the lowest bit flips on almost every edge, the next on about half, and so on
    q4 = (1 << (self.N // 4)) - 1
    rate = {}
    for f in flops:
        d = self.sig(f.conns.get("D"), force) & q4
        qv = vf.get(f.conns["Q"], 0) & q4
        rate[f.id] = bin(d ^ qv).count("1") / (self.N // 4)
    rs = sorted(rate.values(), reverse=True)
    if rs[0] > 0.6 and all(b < 0.8 * a for a, b in zip(rs[:3], rs[1:3])):
        return sorted(flops, key=lambda f: (-round(rate[f.id], 3), key(f)))

    return sorted(flops, key=key)


def _cond_text(self: Decompiler, net, v, force, regwords):
    """Text that is true when `net == v` (under the outer force context)."""
    if net in self.name:
        t = self.name[net]
        return t if v else f"!{t}"
    pcs = self.build([net], force, regwords, allow_table=False)
    t = pcs[0].text
    if v:
        return t
    if pcs[0].kind == "comparator" and " == " in t:
        return t.replace(" == ", " != ")
    if pcs[0].kind == "comparator" and " != " in t:
        return t.replace(" != ", " == ")
    return f"!({t})" if not t.replace("_", "").replace("[", "").replace("]", "").isalnum() else f"!{t}"


def _reg_stmt(self: Decompiler, r: Reg, ctl_scan, regwords):
    kind, clk, cinv, rst, rinv = r.key
    w = len(r.q)
    ctext = self.signal_text(clk, "gclk")
    head = f"always @({'negedge' if cinv else 'posedge'} {ctext}"
    lines = []
    rval = [1 if f.cell.ff["preset"] else 0 for f in r.flops]
    struct_mode = any(d in self.structural_only for d in r.d)
    if kind != "none":
        rtext = self.signal_text(rst, "grst")
        # RESET_B active low; an inverter in front makes the root active high
        head += f" or {'posedge' if rinv else 'negedge'} {rtext})"
        lines.append(f"if ({rtext if rinv else '!' + rtext}) {r.name} <= {_const(rval)};")
    else:
        head += ")"
    force: dict = {}
    guard: list[str] = []
    layers = []
    while not struct_mode and len(layers) < 4:
        res = ctl_scan(r.flops, force, allow_q=True)
        if not any(k[2] == "const" for k in res):
            se = self._source_enable(r.flops, force)
            if se:
                outside, j = se
                guard.append(" && ".join(self._eq_terms(outside, j)))
                layers.append(("hold", guard[-1]))
                force = dict(force)
                for t, x in enumerate(outside):
                    force[x] = (j >> t) & 1
                break
        if not res:
            break
        # prefer const layers (sync reset) over hold (enable), then simpler nets
        (net, v, k, consts), _ = min(res.items(), key=lambda kv: (kv[0][2] != "const", kv[1], kv[0][0] not in self.src))
        cond = _cond_text(self, net, v, force, regwords)
        if k == "const":
            g = " && ".join(guard + [cond]) if guard else cond
            lines.append(f"{'else ' if lines else ''}if ({g}) {r.name} <= {_const(list(consts))};")
            layers.append(("const", cond))
        else:
            notc = _cond_text(self, net, 1 - v, force, regwords)
            guard.append(notc if len(notc) < 40 else f"({notc})")
            layers.append(("hold", notc))
        force = dict(force); force[net] = 1 - v
    pieces = self.build(r.d, force, regwords)
    data = _assign_text(r.name, w, pieces, "<=")
    if guard:
        g = " && ".join(guard)
        pre = f"{'else ' if lines else ''}if ({g})"
    else:
        pre = "else" if lines else ""
    if len(data) == 1:
        lines.append(f"{pre} {data[0]}".strip())
    else:
        lines.append(f"{pre} begin".strip())
        lines += ["    " + x for x in data]
        lines.append("end")
    kinds = {p.kind for p in pieces}
    own = any(p.kind in ("adder", "subtractor", "counter") and _mentions(p.text, r.name) for p in pieces)
    shift = any(p.kind == "copy" and _mentions(p.text, r.name) for p in pieces)
    if own:
        bk = "counter"
    elif shift:
        bk = "shift"
    elif kinds <= {"copy", "const"}:
        bk = "register"
    elif "logic" in kinds:
        bk = "logic"
    else:
        bk = sorted(kinds - {"const", "copy"})[0] if kinds - {"const", "copy"} else "register"
    ok_bits = [(r.d[p.lo + i], p.kind != "logic") for p in pieces for i in range(p.hi - p.lo + 1)]
    summary = data[0] if len(data) == 1 else f"{r.name} <= …"
    blk = Block("", bk, summary, r.d, all(o for _, o in ok_bits), width=w)
    blk._ok_bits = ok_bits
    blk._reg = r
    blk._layers = layers
    body = [head] + ["    " + x for x in lines]
    return blk, body


def _port_stmt(self: Decompiler, pn, bits, regwords):
    pieces = self.build(bits, {}, regwords)
    data = _assign_text(pn, len(bits), pieces, "=")
    kinds = {p.kind for p in pieces}
    rk = kinds - {"const", "copy"}
    bk = "logic" if "logic" in kinds else (sorted(rk)[0] if rk else ("const" if kinds == {"const"} else "copy"))
    ok_bits = [(bits[p.lo + i], p.kind != "logic") for p in pieces for i in range(p.hi - p.lo + 1)]
    blk = Block("", bk, data[0] if len(data) == 1 else f"assign {pn} = …", [b for b in bits if isinstance(b, int)],
                all(o for _, o in ok_bits), width=len(bits))
    blk._ok_bits = ok_bits
    return blk, ["assign " + x for x in data]


def _const(vals):
    w = len(vals)
    v = sum(x << i for i, x in enumerate(vals))
    if w == 1:
        return f"1'b{v}"
    if v == 0:
        return f"{w}'d0"
    if w > 1 and v == (1 << w) - 1:
        return f"{{{w}{{1'b1}}}}"
    return f"{w}'d{v}" if w > 4 else f"{w}'b{v:0{w}b}"


def _mentions(text, name):
    import re
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


def _assign_text(lhs, w, pieces, op):
    """One assignment if the pieces concatenate cleanly, else one per piece."""
    def lhs_slice(p):
        if w == 1:
            return lhs
        return f"{lhs}[{p.lo}]" if p.lo == p.hi else f"{lhs}[{p.hi}:{p.lo}]"
    if len(pieces) == 1:
        p = pieces[0]
        return [f"{lhs_slice(p) if (p.lo, p.hi) != (0, w - 1) else lhs} {op} {p.text};"]
    total = sum(len(p.text) for p in pieces)
    if total <= 72 and all(p.kind != "logic" or len(p.text) < 16 for p in pieces):
        cat = ", ".join(p.text for p in reversed(pieces))
        return [f"{lhs} {op} {{{cat}}};"]
    return [f"{lhs_slice(p)} {op} {p.text};" for p in pieces]


def _emit(self: Decompiler, regs, stmts) -> dict:
    nl, s = self.nl, self.s
    names = {r.name: r for r in regs}
    # ---------- friendly register names by what they do
    rename = {}
    for blk, _ in stmts:
        r = getattr(blk, "_reg", None)
        if r is not None:
            stem = {"counter": "count", "shift": "shreg", "register": "data", "table": "state", "logic": "state",
                    "adder": "acc", "subtractor": "acc", "multiplier": "acc", "comparator": "flag", "mux": "sel",
                    "bitwise": "data", "reduce": "flag"}.get(blk.kind, "r")
            if len(r.q) == 1 and stem in ("data", "state"):
                stem = "bit"
            rename[r.name] = self.user_names.get(r.name) or self.fresh(stem)
    import re
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(rename, key=len, reverse=True))) + r")\b") if rename else None
    rn = (lambda t: pat.sub(lambda mm: rename[mm.group(1)], t)) if pat else (lambda t: t)
    self.rn = rn

    # ---------- drop unused temps
    temps = list(self.temp_lines)
    body_text = "\n".join(l for _, ls in stmts for l in ls)
    changed = True
    while changed:
        changed = False
        alltext = body_text + "\n" + "\n".join(temps)
        keep = []
        for t in temps:
            mm = re.match(r"(?:wire|reg)\s*(?:\[[^\]]*\])?\s*(\w+)", t)
            if mm and mm.group(1).startswith(("t", "sum", "diff", "prod", "cmp", "w", "red", "tbl", "gclk", "grst")) and \
                    t.startswith(("wire", "reg")) and len(re.findall(r"\b" + mm.group(1) + r"\b", alltext)) <= 1 + (t.startswith("reg") and 2 * 0):
                # only its own declaration mentions it
                if t.startswith("reg"):
                    keep.append(t); continue
                changed = True
                continue
            keep.append(t)
        temps = keep
    # tables: a reg plus an always block; drop the whole block if the reg is unused
    used_names = set(re.findall(r"\b\w+\b", body_text + "\n" + "\n".join(t for t in temps if not t.startswith(("reg", "always", "    ", "endcase")))))
    out_t, skip = [], False
    for t in temps:
        mm = re.match(r"reg \[[^\]]*\] (tbl\d+);", t)
        if mm:
            skip = mm.group(1) not in used_names
        if not skip:
            out_t.append(t)
        if t == "endcase":
            skip = False
    temps = out_t
    live = set(re.findall(r"\b\w+\b", body_text + "\n" + "\n".join(temps)))
    self.proof_names = {b: n for b, n in self.proof_names.items() if n.split("[")[0] in live}

    # ---------- the module text
    lines = []
    ins = [(n, p) for n, p in nl.ports.items() if p["dir"] == "in"]
    outs = [(n, p) for n, p in nl.ports.items() if p["dir"] == "out"]
    plist = []
    for n, p in ins + outs:
        wdt = len(p["bits"])
        rng = f"[{wdt - 1}:0] " if wdt > 1 else "      "
        plist.append(f"    {'input ' if p['dir'] == 'in' else 'output'} wire {rng}{n}")
    lines.append(f"module {nl.top} (")
    lines += [x + ("," if k < len(plist) - 1 else "") for k, x in enumerate(plist)]
    lines.append(");")
    for r in regs:
        w = len(r.q)
        lines.append(f"    reg {'[' + str(w - 1) + ':0] ' if w > 1 else ''}{rn(r.name)};")
    for k, b in enumerate(s.cuts):
        lines.append(f"    wire loop{k};")
    if temps:
        lines.append("")
        for t in temps:
            lines.append("    " + rn(t))
    blocks = []
    for k, (blk, ls) in enumerate(stmts):
        lines.append("")
        start = len(lines) + 1
        lines += ["    " + rn(x) for x in ls]
        blk.lines = [start, len(lines)]
        blk.id = f"b{k}"
        blk.text = rn(blk.text)
        blocks.append(blk)
    lines.append("endmodule")
    verilog = "\n".join(lines) + "\n"
    self.proof_names = {b: rn(n) for b, n in self.proof_names.items()}

    # ---------- coverage and cell -> line
    explained = set()
    cell_lines: dict[str, int] = {}
    for blk in blocks:
        for b, ok in getattr(blk, "_ok_bits", []):
            if not isinstance(b, int):
                continue
            cells, _ = s.cone([b])
            if ok:
                explained |= cells
            for c in cells:
                if ok or c not in cell_lines:
                    cell_lines.setdefault(c, blk.lines[0])
        blk.cells = set()
        for b in blk.sinks:
            if isinstance(b, int):
                blk.cells |= s.cone([b])[0]
        r = getattr(blk, "_reg", None)
        if r is not None:
            ok_all = all(ok for _, ok in blk._ok_bits)
            for f in r.flops:
                blk.cells.add(f.id)
                cell_lines[f.id] = blk.lines[0]
                if ok_all:
                    explained.add(f.id)
            for f in r.flops:
                for pin in ("CLK", "RESET_B", "SET_B"):
                    b = f.conns.get(pin)
                    while isinstance(b, int) and b not in self.src and b in s.drv and s.drv[b][0].cell.base in IDENT | INVS:
                        c = s.drv[b][0]
                        explained.add(c.id); blk.cells.add(c.id); cell_lines.setdefault(c.id, blk.lines[0])
                        b = c.conns.get("A")
    for i in nl.insts:
        cell_lines.setdefault(i.id, 0)
    n_logic = len(nl.insts)
    out_blocks = []
    for blk in blocks:
        out_blocks.append({"id": blk.id, "kind": blk.kind, "text": blk.text, "lines": blk.lines, "width": blk.width,
                           "cells": sorted(blk.cells, key=lambda c: int(c[1:])), "recognised": bool(blk.recognised and blk.kind in RECOGNISED)})
    return {
        "verilog": verilog,
        "lines": verilog.count("\n"),
        "blocks": out_blocks,
        "coverage": {"explained_cells": len(explained), "logic_cells": n_logic,
                     "pct": round(100.0 * len(explained) / n_logic, 1) if n_logic else 100.0},
        "cell_lines": cell_lines,
        "proof_names": self.proof_names,
        "registers": {rn(r.name): [f.id for f in r.flops] for r in regs},
    }


Decompiler.decompile = _decompile
Decompiler._group = _group
Decompiler._order = _order
Decompiler._peel_force = _peel_force
Decompiler._wrap_prefix = _wrap_prefix
Decompiler._source_enable = _source_enable
Decompiler._reg_stmt = _reg_stmt
Decompiler._port_stmt = _port_stmt
Decompiler._emit = _emit


def decompile(nl: Netlist, **kw) -> dict:
    return Decompiler(nl, **kw).decompile()


def _out_bit(self, cid):
    return _out_of(self.s, cid)


Decompiler._out_bit = _out_bit
