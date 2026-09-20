"""掩码表达式（对齐 Axiom ``mask/elements/`` 的词汇表）。

Axiom 的掩码是一门小语言（antlr 解析 + 23 个 element）。这里实现其中最
常用、也最适合脚本化的一部分：

    布尔    ``&``/``and``、``|``/``or``、``!``/``not``、括号
    坐标    ``x``/``y``/``z`` 参与算术与比较：``y<64``、``(y-4)%8==0``、``x>z``
    空间    ``solid`` / ``air`` / ``surface``（朝空气的面）/ ``sky``（上方全空）/
            ``edge``（有空气邻居）/ ``inside``（选区内部）
    邻域    ``above(B)`` / ``below(B)`` / ``near(B)`` / ``neighbor(B)`` /
            ``adjacent(B)``（六邻域任一）—— B 可为 ``air``、``stone``、``oak*``
    方块    ``block(B)`` 或直接写裸方块名（同上，支持 ``*`` 通配）
    随机    ``random(0.3)``（需要 seed，确定性）

坐标是**世界坐标**（加上 work box 的 origin），所以同一表达式在任意子区域
求值结果一致。
"""
from __future__ import annotations

import re

import numpy as np

__all__ = ["MaskContext", "compile_mask", "MaskError"]

_KEYWORDS = {"and", "or", "not", "true", "false"}
_SPACE = {"solid", "air", "surface", "sky", "edge", "inside"}
_UNARY = {"above", "below", "near", "neighbor", "adjacent", "block", "random"}
_COMPARE = {"<", "<=", ">", ">=", "==", "!="}
_OPS = {"+", "-", "*", "/", "%"}

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d+)?)
    | (?P<ident>[A-Za-z_#][A-Za-z0-9_:.*]*)
    | (?P<op><=|>=|==|!=|&&|\|\||[()!,&|<>=+\-*/%])
    )""", re.X)


class MaskError(ValueError):
    """掩码语法/取值错误（带位置，方便前端给出人话提示）。"""


# ------------------------------------------------------------------ lexer
def _lex(text: str) -> list[tuple[str, str]]:
    out, i, n = [], 0, len(text)
    while i < n:
        m = _TOKEN.match(text, i)
        if not m:
            if text[i].isspace():
                i += 1
                continue
            raise MaskError(f"掩码第 {i} 个字符无法识别：{text[i:i + 8]!r}")
        i = m.end()
        raw = m.group("num") or m.group("ident") or m.group("op")
        kind = "num" if m.group("num") else ("ident" if m.group("ident")
                                              else "op")
        out.append((kind, raw))
    return out


# ------------------------------------------------------------------ parser
class _Parser:
    """递归下降：or → and → not → primary，数字子语言单独一套。"""

    def __init__(self, text: str):
        self.toks = _lex(text)
        self.i = 0
        self.text = text

    # ---- token helpers
    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def accept(self, value):
        k, v = self.peek()
        if v == value:
            self.i += 1
            return True
        return False

    def expect(self, value):
        if not self.accept(value):
            raise MaskError(f"掩码缺 {value!r}：{self.text!r}")

    # ---- bool layer
    def parse(self):
        node = self.parse_or()
        if self.i != len(self.toks):
            raise MaskError(f"掩码尾部多余内容：{self.toks[self.i][1]!r}")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek()[1] in ("|", "||") or self.peek() == ("ident", "or"):
            self.take()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.peek()[1] in ("&", "&&") or self.peek() == ("ident", "and"):
            self.take()
            node = ("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.accept("!") or self.peek() == ("ident", "not"):
            if self.peek() == ("ident", "not"):
                self.take()
            return ("not", self.parse_not())
        return self.parse_primary()

    def _group_is_numeric(self, i: int) -> bool:
        """看 ``toks[i] == '('`` 这一组括号里是不是纯数字表达式。

        用来消解 ``(y-1)%2==0`` 这种「括号里是算术、外面才是比较」的歧义。
        """
        depth, j = 0, i
        while j < len(self.toks):
            t = self.toks[j][1]
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1:
                k, v = self.toks[j]
                if k == "op" and v in _COMPARE | {"&", "|", "&&", "||", "!",
                                                   ","}:
                    return False
                if k == "ident" and v.lower() not in ("x", "y", "z"):
                    return False
            j += 1
        return True

    def parse_primary(self):
        k, v = self.peek()
        nxt = self.toks[self.i + 1][1] if self.i + 1 < len(self.toks) else None
        if v == "(":
            if self._group_is_numeric(self.i):
                return self.parse_compare()
            self.take()
            node = self.parse_or()
            self.expect(")")
            return node
        if k == "num":
            return self.parse_compare()
        if k == "ident":
            low = v.lower()
            if low in ("true", "false"):
                self.take()
                return ("const", low == "true")
            if low in ("x", "y", "z"):
                if nxt in _COMPARE or nxt in _OPS:
                    return self.parse_compare()
                self.take()
                return ("truthy", ("axis", low))
            empty = (nxt == "(" and
                     self.toks[self.i + 2][1] == ")" if
                     self.i + 2 < len(self.toks) else False)
            if low in _SPACE and (nxt != "(" or empty):
                self.take()
                if empty:
                    self.take()
                    self.take()
                return ("space", low)
            if nxt == "(":
                self.take()
                self.expect("(")
                args = [self.parse_arg()]
                while self.accept(","):
                    args.append(self.parse_arg())
                self.expect(")")
                if low not in _UNARY:
                    raise MaskError(f"未知掩码函数 {v!r}：{self.text!r}")
                return ("call", low, args)
            # 裸方块名（支持 `oak*` 通配）
            self.take()
            return ("call", "block", [v])
        raise MaskError(f"掩码意外记号 {v!r}：{self.text!r}")

    def parse_arg(self):
        """函数实参：裸标识符 / 数字 / 字符串样标识符。"""
        k, v = self.peek()
        if k not in ("ident", "num"):
            raise MaskError(f"掩码实参不合法 {v!r}")
        self.take()
        return v

    def parse_compare(self):
        left = self.parse_num()
        k, v = self.peek()
        if v in _COMPARE:
            self.take()
            right = self.parse_num()
            return ("cmp", v, left, right)
        return ("truthy", left)

    # ---- numeric layer
    def parse_num(self):
        node = self.parse_term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_unary()
        while self.peek()[1] in ("*", "/", "%"):
            op = self.take()[1]
            node = ("bin", op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.accept("-"):
            return ("neg", self.parse_unary())
        return self.parse_atom()

    def parse_atom(self):
        k, v = self.peek()
        if v == "(":
            self.take()
            node = self.parse_num()
            self.expect(")")
            return node
        if k == "num":
            self.take()
            return ("num", float(v))
        if k == "ident":
            self.take()
            low = v.lower()
            if low in ("x", "y", "z"):
                return ("axis", low)
            raise MaskError(f"掩码数值表达式中不认识的标识符 {v!r}")
        raise MaskError(f"掩码数值表达式不完整：{self.text!r}")


# ------------------------------------------------------------------ context
class MaskContext:
    """掩码求值环境：体素 + 世界原点 + 调色板（+ 可选选区/随机源）。"""

    def __init__(self, vol: np.ndarray, origin=(0, 0, 0), names=None,
                 sel: np.ndarray | None = None, rng=None):
        self.vol = vol
        self.origin = tuple(int(v) for v in origin)
        self.names = [str(n) for n in (names or [])]
        self.sel = sel
        self.rng = rng or np.random.default_rng(0)
        self._ids = [n.split("[", 1)[0] for n in self.names]
        self._cache: dict = {}
        self.solid = vol != 0

    # ---- coordinates (world) ----------------------------------------
    def coord(self, axis: str) -> np.ndarray:
        key = ("coord", axis)
        if key not in self._cache:
            sy, sz, sx = self.vol.shape
            i = {"x": 2, "y": 0, "z": 1}[axis]
            n = (sy, sz, sx)[i]
            idx = (np.arange(n) + self.origin[i]).astype(np.float64)
            shape = [1, 1, 1]
            shape[i] = n
            self._cache[key] = idx.reshape(shape) + np.zeros(self.vol.shape)
        return self._cache[key]

    # ---- block name matching ----------------------------------------
    def name_mask(self, ident: str) -> np.ndarray:
        key = ("name", ident)
        if key in self._cache:
            return self._cache[key]
        want = ident.lower()
        if want.startswith("#"):
            raise MaskError("掩码暂不支持方块标签（#tag），请用通配符如 `oak_*`")
        wild = want.replace("minecraft:", "")
        if "*" in wild:
            rx = re.compile("^" + re.escape(wild).replace(r"\*", ".*") + "$")
            hit = [i for i, n in enumerate(self._ids)
                   if rx.match(n.replace("minecraft:", ""))]
        else:
            hit = [i for i, n in enumerate(self._ids)
                   if n.replace("minecraft:", "") == wild]
        m = (np.isin(self.vol, np.array(hit, dtype=np.uint16)) if hit
             else np.zeros(self.vol.shape, dtype=bool))
        self._cache[key] = m
        return m

    def shift(self, m: np.ndarray, axis: int, delta: int, fill=False):
        """把掩码沿 axis 平移 delta（不环绕，边界填 ``fill``）。"""
        out = np.full_like(m, fill)
        sy, sz, sx = m.shape
        if delta == 0:
            return m.copy()
        if axis == 0:
            if delta > 0:
                out[delta:, :, :] = m[:-delta, :, :]
            else:
                out[:delta, :, :] = m[-delta:, :, :]
        elif axis == 1:
            if delta > 0:
                out[:, delta:, :] = m[:, :-delta, :]
            else:
                out[:, :delta, :] = m[:, -delta:, :]
        else:
            if delta > 0:
                out[:, :, delta:] = m[:, :, :-delta]
            else:
                out[:, :, :delta] = m[:, :, -delta:]
        return out

    def any_neighbor(self, m: np.ndarray) -> np.ndarray:
        out = np.zeros_like(m)
        for ax in (0, 1, 2):
            out |= self.shift(m, ax, 1) | self.shift(m, ax, -1)
        return out


# ------------------------------------------------------------------ eval
def _numeric(node, ctx: MaskContext):
    k = node[0]
    if k == "num":
        return node[1]
    if k == "axis":
        return ctx.coord(node[1])
    if k == "neg":
        return -_numeric(node[1], ctx)
    op, a, b = node[1], _numeric(node[2], ctx), _numeric(node[3], ctx)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return np.divide(a, b, out=np.zeros_like(a, dtype=np.float64),
                         where=(b != 0))
    return np.mod(a, b)


def _eval(node, ctx: MaskContext) -> np.ndarray:
    k = node[0]
    if k == "const":
        return np.full(ctx.vol.shape, bool(node[1]))
    if k == "and":
        return _eval(node[1], ctx) & _eval(node[2], ctx)
    if k == "or":
        return _eval(node[1], ctx) | _eval(node[2], ctx)
    if k == "not":
        return ~_eval(node[1], ctx)
    if k == "cmp":
        a, b = _numeric(node[2], ctx), _numeric(node[3], ctx)
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
                "==": a == b, "!=": a != b}[node[1]]
    if k == "truthy":
        return _numeric(node[1], ctx) != 0
    if k == "space":
        return _space(node[1], ctx)
    if k == "call":
        return _call(node[1], node[2], ctx)
    raise MaskError(f"内部错误：未知掩码节点 {k}")


def _space(name, ctx: MaskContext) -> np.ndarray:
    solid = ctx.solid
    if name == "solid":
        return solid
    if name == "air":
        return ~solid
    if name == "inside":
        return (ctx.sel if ctx.sel is not None
                else np.zeros(ctx.vol.shape, dtype=bool))
    if name == "surface":
        return solid & ctx.any_neighbor(~solid)
    if name == "edge":
        return solid & ctx.any_neighbor(~solid)
    if name == "sky":
        m = np.ones(ctx.vol.shape, dtype=bool)
        for dy in range(1, ctx.vol.shape[0]):
            m &= ~ctx.shift(solid, 0, -dy)     # 上方 dy 层不能有实体
        return solid & m
    raise MaskError(f"未知空间谓词 {name}")


def _call(name, args, ctx: MaskContext) -> np.ndarray:
    if name == "random":
        p = float(args[0])
        return ctx.rng.random(ctx.vol.shape) < p
    ident = str(args[0])
    if ident in ("air", "minecraft:air"):
        target = ~ctx.solid
    else:
        target = ctx.name_mask(ident)
    if name == "block":
        return target
    if name == "adjacent":
        return ctx.any_neighbor(target)
    if name == "above":
        return ctx.shift(target, 0, 1)         # 世界上方（y+1）
    if name == "below":
        return ctx.shift(target, 0, -1)
    if name == "near":
        return ctx.any_neighbor(target)
    if name == "neighbor":
        # 与 adjacent 同义，但只算水平四邻（对齐 Axiom 的 neighbor 语义）
        return (ctx.shift(target, 2, 1) | ctx.shift(target, 2, -1) |
                ctx.shift(target, 1, 1) | ctx.shift(target, 1, -1))
    raise MaskError(f"未知掩码函数 {name}")


def compile_mask(expr: str | None, ctx: MaskContext) -> np.ndarray:
    """把表达式编译成与 ``ctx.vol`` 同形状的布尔数组。

    空表达式 → 全 ``True``（不筛选）。多个表达式用 ``;`` 分隔时取交集。
    """
    if expr is None or not str(expr).strip():
        return np.ones(ctx.vol.shape, dtype=bool)
    out = None
    for part in str(expr).split(";"):
        part = part.strip()
        if not part:
            continue
        m = _eval(_Parser(part).parse(), ctx)
        out = m if out is None else (out & m)
    if out is None:
        return np.ones(ctx.vol.shape, dtype=bool)
    return np.ascontiguousarray(out, dtype=bool)
