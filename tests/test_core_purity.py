"""Core purity, enforced independently of the lint config.

CLAUDE.md rule 1: ``core/**`` imports nothing from ``io/``, ``viewer/`` or
``cli``, performs no I/O, never reads the wall clock, and never touches
unseeded randomness. Randomness arrives as an explicit ``np.random.Generator``.

``src/juggling_analyser/core/.ruff.toml`` encodes the same rule as lint. This
module walks the AST itself so that the rule holds even if that config drifts,
a ruff rule is renamed, or someone adds a blanket ``# noqa``. Both halves are
load-bearing (DESIGN.md §2) — neither may be weakened to make a phase pass.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "src" / "juggling_analyser" / "core"

CORE_MODULES = sorted(CORE.glob("*.py"))

#: Standard-library and third-party roots that would make ``core`` impure. The
#: message is what a failure prints, so it says *where the thing belongs*.
BANNED_MODULES: dict[str, str] = {
    "time": "core/ never reads the wall clock",
    "datetime": "core/ never reads the wall clock",
    "random": "randomness arrives as an explicit np.random.Generator argument",
    "secrets": "randomness arrives as an explicit np.random.Generator argument",
    "os": "core/ performs no I/O",
    "sys": "core/ performs no I/O",
    "io": "core/ performs no I/O",
    "pathlib": "core/ performs no I/O",
    "shutil": "core/ performs no I/O",
    "tempfile": "core/ performs no I/O",
    "json": "serialisation lives in io/session.py",
    "csv": "serialisation lives in io/",
    "pickle": "serialisation lives in io/",
    "sqlite3": "core/ performs no I/O",
    "socket": "core/ performs no I/O",
    "subprocess": "core/ performs no I/O",
    "urllib": "core/ performs no I/O",
    "http": "core/ performs no I/O",
    "requests": "core/ performs no I/O",
    "httpx": "core/ performs no I/O",
    "logging": "return diagnostics as data instead of logging",
    "argparse": "argument parsing lives in cli.py",
    "webbrowser": "core/ performs no I/O",
    "threading": "core/ is single-threaded and deterministic",
    "multiprocessing": "core/ is single-threaded and deterministic",
    "asyncio": "core/ is synchronous and deterministic",
    "matplotlib": "core/ does no plotting; the viewer renders",
    "plotly": "core/ does no plotting; the viewer renders",
    "olefile": "file parsing lives in io/qtm.py",
    "lzo": "file parsing lives in io/qtm.py",
    "flask": "core/ serves nothing; viewer/server.py does",
    "fastapi": "core/ serves nothing; viewer/server.py does",
}

#: Sibling packages of ``core`` inside this project. Dependency direction is
#: strictly ``cli / viewer / io -> core``, never the reverse. Reachable both
#: relatively (``from ..io import``) and absolutely
#: (``from juggling_analyser.io import``); both are banned.
BANNED_SIBLINGS = ("io", "viewer", "cli", "__main__")
PACKAGE = "juggling_analyser"

#: Fully-qualified callables that are banned even when reached by attribute
#: access through an alias (``np.random.seed``, ``time.perf_counter``, ...).
BANNED_ATTRIBUTES = (
    "time.time",
    "time.perf_counter",
    "time.monotonic",
    "time.sleep",
    "time.time_ns",
    "datetime.datetime.now",
    "datetime.datetime.today",
    "datetime.datetime.utcnow",
    "numpy.random.seed",
    "numpy.random.default_rng",
    "numpy.random.rand",
    "numpy.random.randn",
    "numpy.random.random",
    "numpy.random.normal",
    "numpy.random.uniform",
    "numpy.random.randint",
    "numpy.random.choice",
    "numpy.random.shuffle",
    "numpy.random.permutation",
    "numpy.random.standard_normal",
)

#: The only names reachable under ``numpy.random`` from core: the *type*, for
#: annotations. Anything else is the global RNG or a factory for it.
ALLOWED_NUMPY_RANDOM = frozenset({"Generator"})

BANNED_BUILTINS = ("open", "input", "eval", "exec", "compile", "__import__")


def _dotted(node: ast.expr) -> str | None:
    """Reconstruct ``a.b.c`` from an attribute/name chain, else ``None``."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


class PurityVisitor(ast.NodeVisitor):
    """Collect purity violations in one core module."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.violations: list[str] = []
        #: local alias -> fully-qualified module (``np`` -> ``numpy``)
        self.aliases: dict[str, str] = {}

    def _fail(self, node: ast.AST, what: str, why: str) -> None:
        line = getattr(node, "lineno", 0)
        self.violations.append(f"{self.module}:{line}: {what} — {why}")

    # -- imports ---------------------------------------------------------- #

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            self.aliases[alias.asname or root] = alias.name
            if root in BANNED_MODULES:
                self._fail(node, f"import {alias.name}", BANNED_MODULES[root])
            self._check_absolute_sibling(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            module = node.module or ""
            root = module.split(".")[0]
            if root in BANNED_MODULES:
                self._fail(node, f"from {module} import ...", BANNED_MODULES[root])
            self._check_absolute_sibling(node, module)
            for alias in node.names:
                self.aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
        elif node.level == 1:
            # sibling module inside core — fine
            for alias in node.names:
                self.aliases[alias.asname or alias.name] = alias.name
        else:
            # ``..`` or deeper leaves core; the only things out there are io/,
            # viewer/, cli and the package root.
            target = node.module or ", ".join(a.name for a in node.names)
            self._fail(
                node,
                f"from {'.' * node.level}{node.module or ''} import ...",
                f"core/ must not reach outside its package (saw {target!r})",
            )
        self.generic_visit(node)

    def _check_absolute_sibling(self, node: ast.AST, module: str) -> None:
        """Catch ``from juggling_analyser.io import ...`` and friends."""
        parts = module.split(".")
        if parts[0] != PACKAGE:
            return
        if len(parts) == 1:
            self._fail(
                node,
                f"from {module} import ...",
                "importing the package root from core/ risks a cycle back through io/",
            )
        elif parts[1] in BANNED_SIBLINGS:
            self._fail(
                node,
                f"from {module} import ...",
                "dependency direction is cli / viewer / io -> core, never the reverse",
            )

    # -- usage ------------------------------------------------------------ #

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = _dotted(node)
        if dotted is not None:
            head, _, rest = dotted.partition(".")
            root = self.aliases.get(head, head)
            resolved = f"{root}.{rest}" if rest else root
            if resolved in BANNED_ATTRIBUTES:
                self._fail(node, resolved, "not permitted in core/")
            if resolved.startswith("numpy.random."):
                leaf = resolved.split(".")[2]
                if leaf not in ALLOWED_NUMPY_RANDOM:
                    self._fail(
                        node,
                        resolved,
                        "the global numpy RNG is banned; take a np.random.Generator argument",
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BANNED_BUILTINS:
            self._fail(node, f"{node.func.id}()", "core/ performs no I/O and does not exec code")
        self.generic_visit(node)


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.name)
def test_core_module_is_pure(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = PurityVisitor(f"core/{path.name}")
    visitor.visit(tree)
    assert not visitor.violations, "core purity violated:\n  " + "\n  ".join(visitor.violations)


def test_core_modules_exist() -> None:
    """Guard against the parametrised test silently covering nothing."""
    assert CORE_MODULES, "no core modules found — the purity test would pass vacuously"


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.name)
def test_core_module_imports_cleanly(path: Path) -> None:
    """Importing core must have no side effects beyond defining names."""
    suffix = "" if path.stem == "__init__" else f".{path.stem}"
    importlib.import_module(f"juggling_analyser.core{suffix}")


def test_purity_visitor_catches_a_violation() -> None:
    """The detector itself is tested — an inert checker is worse than none."""
    source = (
        "import time\n"
        "import numpy as np\n"
        "from ..io import qtm\n"
        "from juggling_analyser.viewer import server\n"
        "def f():\n"
        "    np.random.seed(0)\n"
        "    return time.perf_counter(), open('x'), qtm, server\n"
    )
    visitor = PurityVisitor("fake.py")
    visitor.visit(ast.parse(source))
    joined = "\n".join(visitor.violations)
    assert "import time" in joined
    assert "numpy.random.seed" in joined
    assert "time.perf_counter" in joined
    assert "open()" in joined
    assert "outside its package" in joined
    assert "never the reverse" in joined


def test_purity_visitor_allows_legitimate_core_code() -> None:
    source = (
        "import numpy as np\n"
        "from .trajectory import Session\n"
        "def f(rng: np.random.Generator, s: Session) -> np.ndarray:\n"
        "    return rng.normal(size=3) + np.asarray(s.f_s)\n"
    )
    visitor = PurityVisitor("fake.py")
    visitor.visit(ast.parse(source))
    assert not visitor.violations, visitor.violations
