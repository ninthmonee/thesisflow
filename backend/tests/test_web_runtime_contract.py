"""Code/prompt/schema and runtime changes fence old resumable research."""

from pathlib import Path

from tradingagents.web.infrastructure.runtime_contract import build_graph_contract


def test_contract_changes_with_prompt_state_graph_and_dependencies(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    for name in ("prompt.py", "state.py", "graph.py"):
        (root / name).write_text("original = True\n")
    original = build_graph_contract(root, {"langgraph": "1.0"}, "3.13.3")
    assert original == build_graph_contract(root, {"langgraph": "1.0"}, "3.13.3")
    for name in ("prompt.py", "state.py", "graph.py"):
        (root / name).write_text("changed = True\n")
        assert build_graph_contract(root, {"langgraph": "1.0"}, "3.13.3") != original
        (root / name).write_text("original = True\n")
    assert build_graph_contract(root, {"langgraph": "2.0"}, "3.13.3") != original
    assert build_graph_contract(root, {"langgraph": "1.0"}, "3.14.0") != original
    # Moving the installation never makes an otherwise identical build incompatible.
    moved = tmp_path / "other" / "package"
    moved.mkdir(parents=True)
    for source in root.glob("*.py"):
        (moved / source.name).write_bytes(source.read_bytes())
    assert build_graph_contract(moved, {"langgraph": "1.0"}, "3.13.3") == original


def test_contract_never_reads_credentials_or_runtime_data(tmp_path):
    (tmp_path / "graph.py").write_text("graph = 1\n")
    (tmp_path / ".env").write_text("PRIVATE=original")
    original = build_graph_contract(tmp_path, {}, "3.13.3")
    (tmp_path / ".env").write_text("PRIVATE=changed")
    (tmp_path / "report.json").write_text("private research")
    assert build_graph_contract(tmp_path, {}, "3.13.3") == original
    assert str(Path(tmp_path)) not in original
