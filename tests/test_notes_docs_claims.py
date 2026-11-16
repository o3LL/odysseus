"""The Notes user guide and spec describe what the code does now.

`website/notes.md` still said agent runs
"cannot edit your notes", described parts that "can only be edited in Markdown
source", and never mentioned the version history, while the code had moved on.
These checks tie each of those claims to the code it describes, so the next
behaviour change that leaves the docs behind fails here.
"""
import re
from pathlib import Path

from src import constants

REPO = Path(__file__).resolve().parent.parent
GUIDE = (REPO / "website" / "notes.md").read_text(encoding="utf-8")
SPEC = (REPO / "specs" / "calendar-tasks-notes.md").read_text(encoding="utf-8")
RICH_BODY = (REPO / "static" / "js" / "features" / "notes" / "ui" / "richBody.js").read_text(encoding="utf-8")
NOTE_RULES = (REPO / "static" / "js" / "features" / "notes" / "domain" / "noteRules.js").read_text(encoding="utf-8")
NOTE_ROUTES = (REPO / "routes" / "note" / "note_routes.py").read_text(encoding="utf-8")
MARKDOWN_JS = (REPO / "static" / "js" / "markdown.js").read_text(encoding="utf-8")

# Toolbar labels (the `title` of each button) and the words the guide uses for them.
TOOLBAR_WORDS = {
    "Bullet list": "bullets",
    "Numbered list": "numbered lists",
    "Checklist": "checklists",
    "Link": "links",
    "Insert image": "image",
    "Heading": "two heading levels",
    "Subheading": "two heading levels",
}


def _section(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:end if end != -1 else len(text)]


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_guide_and_spec_no_longer_describe_locked_parts():
    # The lock and its hint were removed from the editor.
    assert "can only be edited in" not in RICH_BODY
    for doc in (GUIDE, SPEC):
        flat = _flat(doc)
        assert "can only be edited in Markdown source" not in flat
        assert "locked block" not in flat.lower()
        assert "A block is locked" not in flat
    assert "locked parts" not in GUIDE.lower()


def test_guide_says_a_note_run_can_edit_its_note():
    # Note-started runs are told they may edit their note.
    assert "EDIT_SCOPE_NOTICE = 'You may edit the attached note (and only it)" in NOTE_RULES
    agent = _flat(_section(GUIDE, "## Asking the agent"))
    assert "cannot edit your notes" not in agent
    assert "can edit that note, and only that one" in agent
    assert "Agent edited this note" in agent


def test_guide_lists_every_toolbar_button():
    titles = re.findall(r"button\('[a-z0-9]+', '([^']+)'", RICH_BODY)
    assert len(titles) >= 15, titles
    toolbar = _flat(_section(GUIDE, "## Toolbar and keyboard")).lower()
    for title in titles:
        word = TOOLBAR_WORDS.get(title, title).lower()
        assert word in toolbar, f"guide does not list the toolbar's {title!r}"


def test_guide_documents_the_version_history_with_the_real_limits():
    assert f"{constants.NOTE_VERSIONS_MAX} versions" in _flat(_section(GUIDE, "## History"))
    assert constants.NOTE_VERSION_USER_GAP_SECONDS == 10 * 60
    assert "more than ten minutes old" in _flat(_section(GUIDE, "## History"))
    spec = _flat(SPEC)
    assert f"`NOTE_VERSIONS_MAX` ({constants.NOTE_VERSIONS_MAX})" in spec
    for route in ("/{note_id}/versions\"", "/{note_id}/versions/{version_id}\"", "/{note_id}/versions/{version_id}/restore\""):
        assert route in NOTE_ROUTES
    for path in ("GET /api/notes/{id}/versions`", "GET /api/notes/{id}/versions/{vid}`", "POST /api/notes/{id}/versions/{vid}/restore`"):
        assert path in spec


def test_external_image_rule_is_scoped_to_notes():
    # Chat's default renderer loads external markdown images; only Notes refuses them.
    assert "function imageHtml(" in MARKDOWN_JS
    images = _flat(_section(GUIDE, "## Images and sketches"))
    assert "Images from other websites are not loaded in notes" in images
    assert "chat messages do load images from other sites" in images
    assert "which does load external markdown images" in _flat(SPEC)


def test_docs_describe_the_chat_context_cards():
    assert "## Asking the agent" in GUIDE
    agent = _flat(_section(GUIDE, "## Asking the agent"))
    assert "small cards" in agent
    assert "Context cards (`ui/contextStrip.js`" in SPEC
    assert "metadata.context_notes" in SPEC
