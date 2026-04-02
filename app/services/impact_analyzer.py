"""Static impact analysis: AST parse, call graph, and risk from changed symbols."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Tuple


RiskLevel = Literal["low", "medium", "high"]


@dataclass
class ParsedModule:
    """Per-file parse result."""

    path: Path
    functions: List[str] = field(default_factory=list)
    # function_name -> names called directly (unqualified)
    calls: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class DependencyGraph:
    """Cross-file view: who defines what, who calls whom."""

    # qualified_id "file.py:func" -> defining file path (normalized)
    function_to_file: Dict[str, Path] = field(default_factory=dict)
    # file path -> top-level function names defined there
    file_to_functions: Dict[Path, List[str]] = field(default_factory=dict)
    # callee global name -> qualified callers (file.py:fn)
    callee_to_callers: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class ImpactResult:
    changed_functions: List[str]
    impacted_functions: List[str]
    impacted_files: List[str]
    risk_level: RiskLevel


def _norm_path(p: Path) -> Path:
    return p.resolve()


def _qual(file_path: Path, fn: str) -> str:
    return f"{file_path.name}:{fn}"


class _CallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: List[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        self.generic_visit(node)


def _parse_module_source(path: Path, source: str) -> ParsedModule:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return ParsedModule(path=_norm_path(path), functions=[], calls={})
    functions: List[str] = []
    calls: Dict[str, List[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
            v = _CallVisitor()
            for stmt in node.body:
                v.visit(stmt)
            calls[node.name] = sorted(set(v.calls))
    return ParsedModule(path=_norm_path(path), functions=sorted(functions), calls=calls)


def parse_file(path: Path) -> ParsedModule:
    """Parse a Python file; return defined functions and intra-module call edges."""
    text = path.read_text(encoding="utf-8")
    return _parse_module_source(path, text)


def parse_source(path: Path, source: str) -> ParsedModule:
    """Parse source string as if it lived at `path` (for tests)."""
    return _parse_module_source(path, source)


def build_graph(file_paths: List[Path]) -> DependencyGraph:
    """
    Build a graph from multiple files. Global names for callees are resolved to a single
    definition if exactly one top-level function with that name exists in the project.
    """
    modules = [parse_file(p) for p in file_paths]
    name_to_file: Dict[str, Path] = {}
    for m in modules:
        for fn in m.functions:
            # last definition wins if duplicate names (same as naive linker)
            name_to_file[fn] = _norm_path(m.path)

    g = DependencyGraph(function_to_file={}, file_to_functions={}, callee_to_callers={})

    for m in modules:
        p = _norm_path(m.path)
        g.file_to_functions[p] = list(m.functions)
        for fn in m.functions:
            q = _qual(p, fn)
            g.function_to_file[q] = p
            for callee in m.calls.get(fn, []):
                if callee in name_to_file:
                    if callee not in g.callee_to_callers:
                        g.callee_to_callers[callee] = set()
                    g.callee_to_callers[callee].add(q)
    return g


def analyze_impact(
    graph: DependencyGraph,
    changed_functions: List[str],
    *,
    project_root: Optional[Path] = None,
) -> ImpactResult:
    """
    `changed_functions`: top-level function names that were modified (short names).

    Impacted functions are direct callers (any file) of any changed function, by global name.
    """
    impacted_qualified: Set[str] = set()
    for name in changed_functions:
        impacted_qualified |= graph.callee_to_callers.get(name, set())

    # map qualified -> short name for display
    impacted_short = sorted({_q.split(":", 1)[1] for _q in impacted_qualified})

    impacted_files_set: Set[Path] = set()
    for q in impacted_qualified:
        fp = graph.function_to_file.get(q)
        if fp is not None:
            impacted_files_set.add(fp)

    # impacted files as basenames, sorted
    impacted_files = sorted({p.name for p in impacted_files_set})

    n = len(impacted_short)
    if n == 0:
        risk: RiskLevel = "low"
    elif n >= 3:
        risk = "high"
    else:
        risk = "medium"

    return ImpactResult(
        changed_functions=sorted(changed_functions),
        impacted_functions=impacted_short,
        impacted_files=impacted_files,
        risk_level=risk,
    )


def analyze_files_impact(
    file_paths: List[Path],
    changed_functions: List[str],
) -> ImpactResult:
    """Convenience: build graph from files then analyze."""
    g = build_graph(file_paths)
    return analyze_impact(g, changed_functions)
