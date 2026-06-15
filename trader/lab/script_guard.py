"""AST allowlist for user-supplied strategy code before it is exec'd.

This is defense-in-depth, not a full sandbox. Strategy scripts run via exec() in a
backtest subprocess and in the live scheduler. Without OS-level isolation a script
could `import os` and read ~/.shectory_trade.env (FINAM_SECRET_TOKEN), open sockets,
or escape via dunder gadgets. This validator rejects the obvious paths: dangerous
imports, dangerous builtins, and double-underscore attribute access used to walk
back to the interpreter. Run it once before exec; on failure the script is refused.

It is NOT a guarantee against a determined attacker. The remaining mitigation
(separate low-priv OS user, no network, no read access to the secrets file) is
infra and lives in the systemd unit / deploy, not here.
"""

import ast

# Modules a legitimate strategy may import. trader.lab.* (indicators, runtime,
# strategies, commission) is the strategy API; the rest are pure-compute stdlib.
_ALLOWED_IMPORT_ROOTS = {
    "math", "statistics", "datetime", "typing", "dataclasses",
    "collections", "random", "decimal", "itertools", "functools",
    "trader",  # only trader.lab.* is reachable; see _check_import
}

# Builtins that enable escape or I/O. Blocked as Name loads and as call targets.
_BANNED_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "memoryview", "breakpoint", "exit", "quit",
}


class ScriptValidationError(ValueError):
    """Raised when a strategy script uses a forbidden construct."""


def _check_import(node: ast.AST) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                raise ScriptValidationError(f"import of '{alias.name}' is not allowed")
            if root == "trader" and not alias.name.startswith("trader.lab"):
                raise ScriptValidationError(f"import of '{alias.name}' is not allowed")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        root = module.split(".")[0]
        if root not in _ALLOWED_IMPORT_ROOTS:
            raise ScriptValidationError(f"import from '{module}' is not allowed")
        if root == "trader" and not module.startswith("trader.lab"):
            raise ScriptValidationError(f"import from '{module}' is not allowed")


def validate_script(code: str) -> None:
    """Parse and reject dangerous constructs. Raises ScriptValidationError on violation."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ScriptValidationError(f"syntax error: {exc}") from exc

    for node in ast.walk(tree):
        _check_import(node)

        # Block dunder attribute access (e.g. obj.__globals__, ().__class__.__bases__).
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            raise ScriptValidationError(f"access to dunder attribute '{node.attr}' is not allowed")

        # Block dangerous builtins referenced by name.
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            raise ScriptValidationError(f"use of '{node.id}' is not allowed")
