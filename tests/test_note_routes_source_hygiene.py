"""routes/note/note_routes.py imports only what it uses, and says "Note changed elsewhere" once."""

import ast
import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "routes" / "note" / "note_routes.py"


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_every_imported_name_is_used():
    tree = _tree()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0:
            imported |= {alias.asname or alias.name for alias in node.names}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not sorted(imported - used), f"imported but never used: {sorted(imported - used)}"


def test_conflict_message_is_written_once():
    strings = [node.value for node in ast.walk(_tree())
               if isinstance(node, ast.Constant) and node.value == "Note changed elsewhere"]
    assert len(strings) == 1
