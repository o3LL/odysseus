"""grep/ls/glob build the control-plane inventory once per call, not per entry.

Rebuilding it for every enumerated file made grep scale with
files x inventory cost and time out on trees of ~20k files.
"""
import asyncio
import json

import pytest

import src.agent_runtime.resources as resources
from src.agent_tools.filesystem_tools import GlobTool, GrepTool, LsTool


def make_tree(root, files_per_dir):
    for d in range(3):
        sub = root / f"d{d}"
        sub.mkdir(parents=True)
        for f in range(files_per_dir):
            (sub / f"f{f}.txt").write_text("plain\n", encoding="utf8")
    (root / "d0" / "hit.txt").write_text("needle\n", encoding="utf8")
    return root


def run(tool, workspace, payload):
    from src.tool_execution import _active_workspace

    async def invoke():
        token = _active_workspace.set(str(workspace))
        try:
            return await tool.execute(json.dumps(payload), {})
        finally:
            _active_workspace.reset(token)

    return asyncio.run(invoke())


def snapshot_builds_for(monkeypatch, tool, root, payload):
    calls = []
    real = resources._control_plane_snapshot

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(resources, "_control_plane_snapshot", counting)
    result = run(tool, root, payload)
    monkeypatch.setattr(resources, "_control_plane_snapshot", real)
    assert result["exit_code"] == 0, result
    return len(calls), result["output"]


@pytest.mark.parametrize("tool, payload, expected", [
    (GrepTool, lambda root: {"pattern": "needle", "path": str(root)}, "hit.txt"),
    (GlobTool, lambda root: {"pattern": "**/*.txt", "path": str(root)}, "hit.txt"),
    (LsTool, lambda root: {"path": str(root / "d1")}, "f0.txt"),
])
def test_snapshot_builds_do_not_grow_with_the_tree(tmp_path, monkeypatch, tool, payload, expected):
    small = make_tree(tmp_path / "small", 2)
    large = make_tree(tmp_path / "large", 40)

    small_builds, small_output = snapshot_builds_for(monkeypatch, tool(), small, payload(small))
    large_builds, large_output = snapshot_builds_for(monkeypatch, tool(), large, payload(large))

    assert expected in small_output and expected in large_output
    # Root resolution may observe fresh state once; the scan itself shares one.
    assert large_builds == small_builds <= 2


def test_scan_snapshot_still_hides_control_plane_files(tmp_path, monkeypatch):
    """Sharing one snapshot must not weaken the deny: a protected file inside
    the searched tree is still skipped."""
    root = make_tree(tmp_path / "tree", 2)
    protected = root / "d2" / "state.db"
    protected.write_text("needle\n", encoding="utf8")
    real = resources._control_plane_snapshot

    def with_protected():
        directories, files, identities = real()
        return directories, files | {str(protected.resolve())}, identities

    monkeypatch.setattr(resources, "_control_plane_snapshot", with_protected)
    result = run(GrepTool(), root, {"pattern": "needle", "path": str(root)})
    assert result["exit_code"] == 0, result
    assert "hit.txt" in result["output"]
    assert "state.db" not in result["output"]
