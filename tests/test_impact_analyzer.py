"""Unit tests for the impact analyzer (AST, graph, impact, risk)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.impact_analyzer import (
    analyze_files_impact,
    build_graph,
    parse_file,
    parse_source,
)


@pytest.fixture
def tmp_path_norm(tmp_path: Path) -> Path:
    return tmp_path.resolve()


def _write(p: Path, name: str, content: str) -> Path:
    fp = p / name
    fp.write_text(content, encoding="utf-8")
    return fp.resolve()


# --- 1. AST parsing ---


def test_ast_parsing_single_file_functions_and_calls(tmp_path_norm: Path) -> None:
    code = """
def a():
    b()

def b():
    pass
"""
    f = _write(tmp_path_norm, "sample.py", code.strip())
    m = parse_file(f)
    assert sorted(m.functions) == ["a", "b"]
    assert m.calls == {"a": ["b"], "b": []}


def test_parse_source_matches_file_parse(tmp_path_norm: Path) -> None:
    code = "def x():\n    y()\ndef y():\n    pass\n"
    virtual = tmp_path_norm / "virt.py"
    m = parse_source(virtual, code)
    assert m.functions == ["x", "y"]
    assert m.calls["x"] == ["y"]


# --- 2. Dependency graph (two files) ---


def test_dependency_graph_two_files(tmp_path_norm: Path) -> None:
    _write(
        tmp_path_norm,
        "file1.py",
        """
def a():
    b()
""".strip(),
    )
    _write(
        tmp_path_norm,
        "file2.py",
        """
def b():
    pass
""".strip(),
    )
    f1 = (tmp_path_norm / "file1.py").resolve()
    f2 = (tmp_path_norm / "file2.py").resolve()
    g = build_graph([f1, f2])

    assert g.file_to_functions[f1] == ["a"]
    assert g.file_to_functions[f2] == ["b"]
    assert g.function_to_file["file1.py:a"] == f1
    assert g.function_to_file["file2.py:b"] == f2
    assert g.callee_to_callers["b"] == {"file1.py:a"}


# --- 3. Impact: single caller → medium ---


def test_impact_single_caller_medium_risk(tmp_path_norm: Path) -> None:
    _write(
        tmp_path_norm,
        "file1.py",
        """
def a():
    b()
""".strip(),
    )
    _write(
        tmp_path_norm,
        "file2.py",
        """
def b():
    pass
""".strip(),
    )
    f1 = (tmp_path_norm / "file1.py").resolve()
    f2 = (tmp_path_norm / "file2.py").resolve()
    res = analyze_files_impact([f1, f2], changed_functions=["b"])
    assert sorted(res.changed_functions) == ["b"]
    assert sorted(res.impacted_functions) == ["a"]
    assert sorted(res.impacted_files) == ["file1.py"]
    assert res.risk_level == "medium"


# --- 4. Isolated function → low, no impacted ---


def test_impact_isolated_function_low_risk(tmp_path_norm: Path) -> None:
    _write(
        tmp_path_norm,
        "iso.py",
        """
def lonely():
    return 42
""".strip(),
    )
    fp = (tmp_path_norm / "iso.py").resolve()
    res = analyze_files_impact([fp], changed_functions=["lonely"])
    assert res.impacted_functions == []
    assert res.impacted_files == []
    assert res.risk_level == "low"


# --- 5. Many callers → high ---


def test_impact_high_risk_many_callers(tmp_path_norm: Path) -> None:
    _write(
        tmp_path_norm,
        "users.py",
        """
def a():
    b()

def c():
    b()

def d():
    b()
""".strip(),
    )
    _write(
        tmp_path_norm,
        "core.py",
        """
def b():
    pass
""".strip(),
    )
    files = [
        (tmp_path_norm / "users.py").resolve(),
        (tmp_path_norm / "core.py").resolve(),
    ]
    res = analyze_files_impact(files, changed_functions=["b"])
    assert sorted(res.impacted_functions) == ["a", "c", "d"]
    assert res.risk_level == "high"
    assert sorted(res.impacted_files) == ["users.py"]


# --- Optional: invalid syntax does not crash ---


def test_invalid_python_returns_empty_parse(tmp_path_norm: Path) -> None:
    bad = _write(tmp_path_norm, "bad.py", "def oops(\n")
    m = parse_file(bad)
    assert m.functions == []
    assert m.calls == {}


def test_build_graph_with_invalid_file_skips_or_empty(tmp_path_norm: Path) -> None:
    good = _write(tmp_path_norm, "ok.py", "def f():\n    pass\n")
    bad = _write(tmp_path_norm, "bad.py", "!!!")
    g = build_graph([good, bad])
    assert "ok.py" in {p.name for p in g.file_to_functions}
