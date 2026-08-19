"""Tiny, safe evaluator for a JSONLogic subset (NO pip dependency).

Deliberately kept narrow: only an allow list of operators, no eval, no access outside the
data object passed in (= WorkflowInstance.context). `{"var": "a.b"}` reads over a dot path.
The recursion depth is capped so that a maliciously deep expression does not blow the
stack.

Used by the workflow engine for `decision` guards. Raises a JsonLogicError on unknown
operators (the validation catches them in advance).
"""
from __future__ import annotations

# Allowed operators; EVERYTHING else is rejected.
ALLOWED_OPS = {
    "var", "==", "!=", ">", "<", ">=", "<=", "and", "or", "!", "in", "+", "-", "*",
}

MAX_DEPTH = 25


class JsonLogicError(Exception):
    """Ungültiger/unerlaubter JSONLogic-Ausdruck."""


def _truthy(v) -> bool:
    """JSONLogic-Wahrheitswert: [], "", 0, None → falsch."""
    if v is None:
        return False
    if isinstance(v, (list, dict, str)):
        return len(v) > 0
    return bool(v)


def _num(v):
    """Best-effort number (for comparisons and arithmetic). None when not possible."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if ("." in v or "e" in v.lower()) else int(v)
        except ValueError:
            return None
    return None


def _loose_eq(a, b) -> bool:
    if a is b:
        return True
    if type(a) == type(b):  # noqa: E721
        return a == b
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na == nb
    return a == b


def _cmp(a, b):
    """Deliver comparable pairs (na, nb); raises on incompatible types."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na, nb
    if isinstance(a, str) and isinstance(b, str):
        return a, b
    raise JsonLogicError(f"Not comparable: {a!r} / {b!r}")


def _dig(data, path: str):
    if path in ("", None):
        return data
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def evaluate(rule, data: dict, _depth: int = 0):
    """Wertet einen JSONLogic-Ausdruck gegen `data` aus."""
    if _depth > MAX_DEPTH:
        raise JsonLogicError("Recursion depth exceeded")
    # Literale
    if rule is None or isinstance(rule, (int, float, str, bool)):
        return rule
    if isinstance(rule, list):
        return [evaluate(x, data, _depth + 1) for x in rule]
    if not isinstance(rule, dict):
        raise JsonLogicError(f"Invalid expression: {rule!r}")
    if len(rule) != 1:
        raise JsonLogicError("An operator object needs exactly one key")

    op, args = next(iter(rule.items()))
    if op not in ALLOWED_OPS:
        raise JsonLogicError(f"Operator '{op}' is not allowed")

    if op == "var":
        if isinstance(args, list):
            key = args[0] if args else ""
            default = args[1] if len(args) > 1 else None
        else:
            key, default = args, None
        val = _dig(data, evaluate(key, data, _depth + 1) if isinstance(key, dict) else key)
        return default if val is None else val

    values = args if isinstance(args, list) else [args]
    ev = [evaluate(a, data, _depth + 1) for a in values]

    if op == "==":
        return _loose_eq(ev[0], ev[1])
    if op == "!=":
        return not _loose_eq(ev[0], ev[1])
    if op in (">", "<", ">=", "<="):
        a, b = _cmp(ev[0], ev[1])
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    if op == "and":
        result = True
        for v in ev:
            if not _truthy(v):
                return v
            result = v
        return result
    if op == "or":
        for v in ev:
            if _truthy(v):
                return v
        return ev[-1] if ev else False
    if op == "!":
        return not _truthy(ev[0])
    if op == "in":
        needle, haystack = ev[0], ev[1]
        if isinstance(haystack, (str, list, dict)):
            return needle in haystack
        return False
    if op in ("+", "-", "*"):
        nums = [_num(v) for v in ev]
        if any(n is None for n in nums):
            raise JsonLogicError(f"Arithmetik braucht Zahlen: {ev!r}")
        if op == "+":
            total = 0
            for n in nums:
                total += n
            return total
        if op == "*":
            total = 1
            for n in nums:
                total *= n
            return total
        # "-" : unary or binary
        if len(nums) == 1:
            return -nums[0]
        return nums[0] - nums[1]
    raise JsonLogicError(f"Operator '{op}' is not implemented")  # pragma: no cover


def collect_operators(rule, acc: set | None = None) -> set:
    """Collects all operator keys of an expression (for the validation)."""
    acc = set() if acc is None else acc
    if isinstance(rule, dict):
        for k, v in rule.items():
            acc.add(k)
            collect_operators(v, acc)
    elif isinstance(rule, list):
        for x in rule:
            collect_operators(x, acc)
    return acc


def safe_eval(rule, data: dict) -> bool:
    """Guard evaluation with a truth value; raises JsonLogicError on an invalid expression."""
    return _truthy(evaluate(rule, data))
