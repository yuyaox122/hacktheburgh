"""A small parser and evaluator for Witness claims: Verilog expressions over a chip's signals.

Used by the stub checker (so the agent loop runs offline) and by the formaliser to reject a formal
line before it reaches the real checker. The real verdicts come from Yosys in the core
(core/claims.py, Yuyao's); this module never decides what is shown as proven on a real chip.

Grammar (lowest precedence first):
    claim  := impl ('when' impl)?            'A when B' means B -> A
    impl   := cond ('->' cond)*
    cond   := lor ('?' cond ':' cond)?
    lor := land ('||' land)*   land := bor ('&&' bor)*   bor := bxor ('|' bxor)*
    bxor := band ('^' band)*   band := eq ('&' eq)*       eq := rel (('=='|'!=') rel)*
    rel := shift (('<'|'<='|'>'|'>=') shift)*            shift := add (('<<'|'>>') add)*
    add := mul (('+'|'-') mul)*                          mul := unary ('*' unary)*
    unary := ('!'|'~'|'-'|'&'|'|'|'^') unary | primary
    primary := NAME select? | NUMBER | '(' claim ')' | '{' claim (',' claim)* '}' | 'next' '(' NAME select? ')'

Widths follow Verilog's unsigned rules: unsized numbers are 32 bits, + - * & | ^ ~ ?: are sized by
context, comparisons size both sides to the wider one. So `next(count) == count + 1` does NOT wrap
at 255 (the 1 is 32 bits) while `next(count) == count + 8'd1` does, exactly as Yosys would see it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d*'[sS]?[bBoOdDhH][0-9a-fA-F_]+|\d[\d_]*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<op>->|<=|>=|==|!=|&&|\|\||<<|>>|[~!&|^+\-*<>?:()\[\]{},])
""", re.X)


class ClaimSyntaxError(ValueError):
    pass


@dataclass
class Node:
    op: str                      # 'num', 'sig', 'next', or an operator
    args: list = field(default_factory=list)
    value: int = 0               # for 'num'
    width: int = 32              # for 'num'
    name: str = ""               # for 'sig' / 'next'
    hi: int | None = None        # bit select [hi] or part select [hi:lo]
    lo: int | None = None


def _number(text: str) -> tuple[int, int]:
    text = text.replace("_", "")
    if "'" not in text:
        return int(text), 32
    size, rest = text.split("'", 1)
    rest = rest.lstrip("sS")
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[rest[0].lower()]
    value = int(rest[1:], base)
    width = int(size) if size else 32
    if width < 1:
        raise ClaimSyntaxError(f"bad width in {text!r}")
    return value & ((1 << width) - 1), width


def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise ClaimSyntaxError(f"unexpected {src[pos:pos + 8]!r} at position {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group()))
    return out


BINARY = [  # precedence climbing, lowest first (after 'when', '->', '?:')
    ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="], ["<", "<=", ">", ">="], ["<<", ">>"], ["+", "-"], ["*"],
]


class Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i][1] if self.i < len(self.toks) else None

    def take(self, want: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise ClaimSyntaxError(f"claim ends early; expected {want or 'more'}")
        tok = self.toks[self.i]
        if want is not None and tok[1] != want:
            raise ClaimSyntaxError(f"expected {want!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def parse(self) -> Node:
        if not self.toks:
            raise ClaimSyntaxError("empty claim")
        node = self.claim()
        # a top-level comma is a loose '&&' (models often write a, b, c for a && b && c)
        while self.peek() == ",":
            self.take()
            node = Node("&&", [node, self.claim()])
        if self.i != len(self.toks):
            raise ClaimSyntaxError(f"unexpected {self.peek()!r} after the claim")
        return node

    def claim(self) -> Node:
        node = self.impl()
        if self.peek() == "when":
            self.take()
            node = Node("->", [self.impl(), node])
        return node

    def impl(self) -> Node:
        node = self.cond()
        while self.peek() == "->":
            self.take()
            node = Node("->", [node, self.cond()])
        return node

    def cond(self) -> Node:
        node = self.binary(0)
        if self.peek() == "?":
            self.take()
            a = self.cond()
            self.take(":")
            node = Node("?:", [node, a, self.cond()])
        return node

    def binary(self, level: int) -> Node:
        if level == len(BINARY):
            return self.unary()
        node = self.binary(level + 1)
        while self.peek() in BINARY[level]:
            op = self.take()[1]
            node = Node(op, [node, self.binary(level + 1)])
        return node

    def unary(self) -> Node:
        t = self.peek()
        if t in ("!", "~", "-"):
            self.take()
            return Node("u" + t, [self.unary()])
        if t in ("&", "|", "^"):
            self.take()
            return Node("r" + t, [self.unary()])
        return self.primary()

    def select(self, node: Node) -> Node:
        if self.peek() == "[":
            self.take()
            hi = int(self.take()[1])
            lo = hi
            if self.peek() == ":":
                self.take()
                lo = int(self.take()[1])
            self.take("]")
            if lo > hi:
                raise ClaimSyntaxError(f"part select [{hi}:{lo}] must be [high:low]")
            node.hi, node.lo = hi, lo
        return node

    def primary(self) -> Node:
        kind, text = self.take()
        if kind == "num":
            v, w = _number(text)
            return Node("num", value=v, width=w)
        if text == "(":
            node = self.claim()
            self.take(")")
            return self.select(node)   # allow (a + b)[7:1]
        if text == "{":
            parts = [self.claim()]
            while self.peek() == ",":
                self.take()
                parts.append(self.claim())
            self.take("}")
            return Node("{}", parts)
        if kind == "name" and text == "next":
            self.take("(")
            k, name = self.take()
            if k != "name":
                raise ClaimSyntaxError("next(...) takes a register or flip-flop name")
            node = self.select(Node("next", name=name))
            self.take(")")
            if self.peek() == "[":   # next(x)[i] / next(x)[hi:lo]: one bit of the register's next value
                inner_lo = node.lo or 0
                had = node.hi is not None
                node.hi = node.lo = None
                self.select(node)
                if had:              # next(x[7:4])[1] = next(x[5])
                    node.hi, node.lo = node.hi + inner_lo, node.lo + inner_lo
            return node
        if kind == "name" and text != "when":
            return self.select(Node("sig", name=text))
        raise ClaimSyntaxError(f"unexpected {text!r}")


_PROSE = re.compile(r"\b(i\.e\.|in the|samples|where|truncated|otherwise|bits of|the)\b", re.I)
_ASSIGN = re.compile(r"(?<![=!<>])=(?!=)")   # a standalone '=' (prose like 'out = in'), not == != <= >=


def _strip_trailing_prose(src: str) -> str:
    """Models append English asides like `… (i.e. out = in + 1)` or `… (bit 7:1 of the sum)`. Drop a trailing
    balanced (...) group only when its content is prose (an `=` assignment or an English word), never an expression."""
    s = src.rstrip()
    while s.endswith(")"):
        depth, start = 0, None
        for i in range(len(s) - 1, -1, -1):
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start is None:
            break
        inside = s[start + 1:-1]
        if _ASSIGN.search(inside) or _PROSE.search(inside):
            s = s[:start].rstrip()
        else:
            break
    return s


_TEMP_DEF = re.compile(r"\bwire\s+(?:\[[^\]]*\]\s*)?(t\d+)\s*=\s*(.+?);", re.S)
_TEMP_REF = re.compile(r"\bt\d+\b")


def temp_defs(verilog: str) -> dict[str, str]:
    """The decompiler's helper wires: {'t5': '((shreg7[1] | state0[3]) & t4) | ...'}."""
    return {m.group(1): " ".join(m.group(2).split()) for m in _TEMP_DEF.finditer(verilog or "")}


def temps_used(texts: list[str], defs: dict[str, str]) -> list[str]:
    """Every helper wire the given lines rely on, directly or through other helpers, in definition order."""
    seen: set[str] = set()
    todo = [t for x in texts for t in _TEMP_REF.findall(x) if t in defs]
    while todo:
        t = todo.pop()
        if t not in seen:
            seen.add(t)
            todo += [u for u in _TEMP_REF.findall(defs[t]) if u in defs]
    return [t for t in defs if t in seen]


def expand_temps(claim: str, defs: dict[str, str], depth: int = 32) -> str:
    """Replace helper-wire names in a claim by their (parenthesised) definitions until none are left."""
    for _ in range(depth):
        new = _TEMP_REF.sub(lambda m: f"({defs[m.group(0)]})" if m.group(0) in defs else m.group(0), claim)
        if new == claim:
            return new
        claim = new
    return claim


def _fix_mux(n: Node) -> tuple[Node, bool]:
    if n.op == "&&":
        a, fa = _fix_mux(n.args[0])
        b, fb = _fix_mux(n.args[1])
        return Node("&&", [a, b]), fa or fb
    if n.op == "?:" and n.args[0].op == "&&":   # `p && next(d) == c ? a : b`: the ?: swallowed the whole conjunction
        left, right = n.args[0].args
        r, fr = _fix_mux(Node("?:", [right, n.args[1], n.args[2]]))
        if fr:
            left, _ = _fix_mux(left)
            return Node("&&", [left, r]), True
    if n.op == "?:" and n.args[0].op == "==":
        lhs, cond = n.args[0].args
        if lhs.op == "next" or (lhs.op == "sig" and lhs.name in ("uo_out", "uio_out", "uio_oe")):
            return Node("==", [lhs, Node("?:", [cond, n.args[1], n.args[2]])]), True
    return n, False


def fix_mux_precedence(src: str) -> str:
    """`next(r) == c ? a : b` means `(next(r) == c) ? a : b` in Verilog (?: binds loosest), which is never what the
    writer meant: they meant `next(r) == (c ? a : b)`. Add those parentheses, per conjunct. Anything else, or a claim
    that doesn't parse, comes back unchanged."""
    try:
        node, fixed = _fix_mux(parse(src))
    except ClaimSyntaxError:
        return src
    return unparse(node) if fixed else src


def parse(src: str) -> Node:
    return Parser(_strip_trailing_prose(src)).parse()


def names(node: Node) -> set[tuple[str, str]]:
    """{('sig'|'next', name)} referenced by the claim."""
    out = set()
    if node.op in ("sig", "next"):
        out.add((node.op, node.name))
    for a in node.args:
        out |= names(a)
    return out


def literals(node: Node) -> int:
    """Leaf signal references (the credit rule compares this with the decompiled cone)."""
    if node.op in ("sig", "next"):
        return 1
    return sum(literals(a) for a in node.args)


def _mask(w: int) -> int:
    return (1 << w) - 1


class Evaluator:
    """Evaluate a parsed claim against signal values. `widths` maps a name to its bit width."""

    def __init__(self, widths: dict[str, int]):
        self.widths = widths

    def check_names(self, node: Node) -> None:
        for kind, name in names(node):
            if name not in self.widths:
                raise ClaimSyntaxError(f"unknown signal {name!r}; use the names get_region lists")
        self._check_selects(node)

    def _check_selects(self, node: Node) -> None:
        if node.op in ("sig", "next") and node.hi is not None and node.hi >= self.widths[node.name]:
            raise ClaimSyntaxError(f"{node.name}[{node.hi}] is out of range; {node.name} has {self.widths[node.name]} bits")
        for a in node.args:
            self._check_selects(a)

    def sw(self, n: Node) -> int:
        """Self-determined width."""
        if n.hi is not None:
            return n.hi - n.lo + 1
        if n.op == "num":
            return n.width
        if n.op in ("sig", "next"):
            return self.widths[n.name]
        if n.op in ("u~", "u-"):
            return self.sw(n.args[0])
        if n.op in ("+", "-", "*", "&", "|", "^"):
            return max(self.sw(n.args[0]), self.sw(n.args[1]))
        if n.op in ("<<", ">>"):
            return self.sw(n.args[0])
        if n.op == "?:":
            return max(self.sw(n.args[1]), self.sw(n.args[2]))
        if n.op == "{}":
            return sum(self.sw(a) for a in n.args)
        return 1  # comparisons, logical ops, reductions, implication

    def ev(self, n: Node, env: dict, w: int | None = None) -> int:
        if n.hi is not None and n.op not in ("sig", "next"):
            # a bit/part-select on a sub-expression, e.g. (a + b)[7:1]: evaluate it whole, then slice
            inner = Node(n.op, n.args, n.value, n.width, n.name)
            return (self.ev(inner, env) >> n.lo) & _mask(n.hi - n.lo + 1)
        w = self.sw(n) if w is None else w
        op, a = n.op, n.args
        if op == "num":
            return n.value & _mask(w)
        if op in ("sig", "next"):
            v = env[(op, n.name)]
            if n.hi is not None:
                v = (v >> n.lo) & _mask(n.hi - n.lo + 1)
            return v
        if op == "u~":
            return ~self.ev(a[0], env, w) & _mask(w)
        if op == "u-":
            return -self.ev(a[0], env, w) & _mask(w)
        if op == "u!":
            return int(self.ev(a[0], env) == 0)
        if op[0] == "r":
            x, xw = self.ev(a[0], env), self.sw(a[0])
            if op == "r&":
                return int(x == _mask(xw))
            if op == "r|":
                return int(x != 0)
            return bin(x).count("1") & 1
        if op in ("+", "-", "*", "&", "|", "^"):
            x, y = self.ev(a[0], env, w), self.ev(a[1], env, w)
            r = {"+": x + y, "-": x - y, "*": x * y, "&": x & y, "|": x | y, "^": x ^ y}[op]
            return r & _mask(w)
        if op in ("<<", ">>"):
            x, s = self.ev(a[0], env, w), self.ev(a[1], env)
            return ((x << s) if op == "<<" else (x >> s)) & _mask(w)
        if op in ("==", "!=", "<", "<=", ">", ">="):
            cw = max(self.sw(a[0]), self.sw(a[1]))
            x, y = self.ev(a[0], env, cw), self.ev(a[1], env, cw)
            return int({"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op])
        if op == "&&":
            return int(bool(self.ev(a[0], env)) and bool(self.ev(a[1], env)))
        if op == "||":
            return int(bool(self.ev(a[0], env)) or bool(self.ev(a[1], env)))
        if op == "->":
            return int((not self.ev(a[0], env)) or bool(self.ev(a[1], env)))
        if op == "?:":
            return self.ev(a[1], env, w) if self.ev(a[0], env) else self.ev(a[2], env, w)
        if op == "{}":
            v = 0
            for part in a:
                pw = self.sw(part)
                v = (v << pw) | self.ev(part, env, pw)
            return v & _mask(w)
        raise ClaimSyntaxError(f"unsupported operator {op!r}")

    def holds(self, n: Node, env: dict) -> bool:
        return bool(self.ev(n, env))


def unparse(n: Node) -> str:
    """Canonical text of a parsed claim (fully parenthesised where it matters)."""
    sel = "" if n.hi is None else (f"[{n.hi}]" if n.hi == n.lo else f"[{n.hi}:{n.lo}]")
    if n.op == "num":
        return f"{n.width}'d{n.value}" if n.width != 32 else str(n.value)
    if n.op in ("sig", "next"):
        return f"next({n.name}{sel})" if n.op == "next" else f"{n.name}{sel}"
    inner = ""
    if n.op[0] in "ur" and len(n.args) == 1:
        inner = n.op[1] + _wrap(n.args[0])
    elif n.op == "?:":
        inner = f"{_wrap(n.args[0])} ? {_wrap(n.args[1])} : {_wrap(n.args[2])}"
    elif n.op == "{}":
        inner = "{" + ", ".join(unparse(a) for a in n.args) + "}"
    else:
        inner = f"{_wrap(n.args[0])} {n.op} {_wrap(n.args[1])}"
    if sel and n.op not in ("num", "sig", "next"):
        inner = f"({inner})"
    return inner + sel if sel else inner


def _wrap(n: Node) -> str:
    s = unparse(n)
    return s if n.op in ("num", "sig", "next", "{}") else f"({s})"


def ref_bits(node: Node) -> list[tuple[str, str, int | None, int | None]]:
    """(kind, name, hi, lo) for every signal reference, keeping its bit select."""
    out = [(node.op, node.name, node.hi, node.lo)] if node.op in ("sig", "next") else []
    for a in node.args:
        out += ref_bits(a)
    return out


def output_bits(node: Node, outputs: set[tuple[str, str]], widths: dict[str, int]) -> list[tuple[str, str, int]]:
    """The bits of the region's outputs that the claim mentions, as (kind, name, bit)."""
    bits = set()
    for kind, name, hi, lo in ref_bits(node):
        if (kind, name) in outputs:
            rng = range(widths[name]) if hi is None else range(lo, hi + 1)
            bits |= {(kind, name, b) for b in rng}
    return sorted(bits)


def is_complete(ev: Evaluator, node: Node, envs: list[dict], out_bits: list[tuple[str, str, int]],
                max_bits: int = 12) -> bool | None:
    """Does the claim pin down the output bits it mentions, for every sampled input?

    A complete claim (e.g. `next(count) == (en ? count + 8'd1 : count)`) leaves exactly one value for those bits;
    a partial one (`next(count) == count + 8'd1 when en`, `bit0 -> next(bit0)`) is true but leaves some open.
    None: too many output bits to enumerate here (the Yosys checker can ask SAT for two different outputs instead)."""
    if not out_bits:
        return False
    if len(out_bits) > max_bits:
        return None
    for env in envs:
        sat = 0
        for assign in range(1 << len(out_bits)):
            e = dict(env)
            for i, (kind, name, b) in enumerate(out_bits):
                v = e[(kind, name)]
                e[(kind, name)] = (v & ~(1 << b)) | (((assign >> i) & 1) << b)
            if ev.holds(node, e):
                sat += 1
                if sat > 1:
                    return False
    return True
