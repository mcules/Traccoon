"""Winziger, sicherer JSONLogic-Subset-Evaluator (KEINE pip-Abhängigkeit).

Bewusst eng gehalten: nur eine Allowlist an Operatoren, kein eval, kein Zugriff
außerhalb des übergebenen Datenobjekts (= WorkflowInstance.context). `{"var": "a.b"}`
liest per Dot-Pfad. Die Rekursionstiefe ist gedeckelt, damit ein bösartig tiefer
Ausdruck nicht den Stack sprengt.

Verwendet von der Workflow-Engine für `decision`-Guards. Gibt bei unbekannten
Operatoren eine JsonLogicError (die Validierung fängt sie vorab ab).
"""
from __future__ import annotations

# Erlaubte Operatoren — ALLES andere wird abgelehnt.
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
    """Best-effort-Zahl (für Vergleiche/Arithmetik). None wenn nicht möglich."""
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
    """Vergleichbare Paare liefern (na, nb); wirft bei inkompatiblen Typen."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na, nb
    if isinstance(a, str) and isinstance(b, str):
        return a, b
    raise JsonLogicError(f"Nicht vergleichbar: {a!r} / {b!r}")


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
        raise JsonLogicError("Rekursionstiefe überschritten")
    # Literale
    if rule is None or isinstance(rule, (int, float, str, bool)):
        return rule
    if isinstance(rule, list):
        return [evaluate(x, data, _depth + 1) for x in rule]
    if not isinstance(rule, dict):
        raise JsonLogicError(f"Ungültiger Ausdruck: {rule!r}")
    if len(rule) != 1:
        raise JsonLogicError("Operator-Objekt braucht genau einen Schlüssel")

    op, args = next(iter(rule.items()))
    if op not in ALLOWED_OPS:
        raise JsonLogicError(f"Operator '{op}' nicht erlaubt")

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
        # "-" : unär oder binär
        if len(nums) == 1:
            return -nums[0]
        return nums[0] - nums[1]
    raise JsonLogicError(f"Operator '{op}' nicht implementiert")  # pragma: no cover


def collect_operators(rule, acc: set | None = None) -> set:
    """Sammelt alle Operator-Schlüssel eines Ausdrucks (für die Validierung)."""
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
    """Guard-Auswertung mit Wahrheitswert; wirft JsonLogicError bei ungültigem Ausdruck."""
    return _truthy(evaluate(rule, data))
