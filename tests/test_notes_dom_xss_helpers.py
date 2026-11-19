"""Regression guards for Notes DOM rendering helpers."""

from pathlib import Path

from tests.helpers.notes_source import notes_source


_REPO = Path(__file__).resolve().parent.parent


def test_notes_image_src_guard_rejects_script_capable_data_images():
    src = notes_source("ui/text", "ui/drawing")

    assert "function _safeImgSrc(s)" in src
    assert r"^data:image\/(?:png|jpe?g|gif|webp);base64," in src
    assert r"^data:image\/i.test(v)" not in src


def test_notes_linkify_escapes_href_attribute():
    src = notes_source("ui/text", "ui/drawing")

    assert "function _attrEsc(s)" in src
    assert 'href="${_attrEsc(href)}"' in src
    assert 'href="${href}"' not in src


def test_drawing_canvas_uses_safe_image_src_guard():
    # The note window renders images through mdToHtml's same-origin note
    # policy (tests/notesMarkdownRender.test.mjs); the canvas still loads a
    # starting image itself, so it keeps the guard.
    src = notes_source("ui/text", "ui/drawing")

    assert "const safeInitialImageUrl = _safeImgSrc(initialImageUrl);" in src
    assert "img.src = safeInitialImageUrl;" in src
    assert "img.src = initialImageUrl;" not in src
