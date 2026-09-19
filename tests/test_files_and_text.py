# SPDX-License-Identifier: AGPL-3.0-or-later
"""Storing downloaded files (Brightspace zips HTML lessons and videos) and pulling text out of them."""
import io
import zipfile

import pytest

from d2l import extract, sync


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def files_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "FILES", tmp_path / "files")
    return tmp_path / "files"


def topic(i, title, ext):
    return {"id": i, "course_id": 7, "title": title, "ext": ext}


def test_plain_file_is_stored_as_is(files_dir):
    p = sync._save_bytes(topic(1, "Notes: Unit 1", "pdf"), b"%PDF-1.7 data")
    assert p.read_bytes() == b"%PDF-1.7 data" and p.name == "1-Notes_ Unit 1.pdf"


def test_zipped_video_is_unwrapped(files_dir):
    p = sync._save_bytes(topic(2, "Lecture", "mp4"), zipped({"Lecture 1.mp4": b"\x00\x00\x00\x18ftypmp42"}))
    assert p.suffix == ".mp4" and p.read_bytes()[4:8] == b"ftyp"


def test_zipped_lesson_becomes_a_folder_with_its_images(files_dir):
    p = sync._save_bytes(topic(3, "Limits", "html"), zipped({
        "course/unit6/limits.htm": b"<h1>Limits</h1><img src='img/a.png'>",
        "course/unit6/img/a.png": b"\x89PNG", "course/unit6/extra/page2.htm": b"<p>2</p>"}))
    assert p == files_dir / "7" / "3" / "course/unit6/limits.htm"
    assert (p.parent / "img" / "a.png").exists()


def test_office_files_are_zips_but_left_alone(files_dir):
    data = zipped({"word/document.xml": b"<w/>"})
    assert sync._save_bytes(topic(4, "Syllabus", "docx"), data).read_bytes() == data


def test_zip_with_several_files_is_kept_as_a_zip(files_dir):
    p = sync._save_bytes(topic(5, "Pack", "mp4"), zipped({"a.mp4": b"1", "b.mp4": b"2"}))
    assert p.suffix == ".zip"


def test_tidy_rejoins_one_word_per_line_pdfs_and_drops_dot_leaders():
    assert extract._tidy("\n \n".join("the quick brown fox jumps over the lazy dog".split())) == \
        "the quick brown fox jumps over the lazy dog"
    assert extract._tidy("Limits ........................ 3\nContinuity") == "Limits … 3\nContinuity"
    assert extract._tidy("Line one\nLine two") == "Line one\nLine two"     # normal text untouched


def test_text_of_html_and_zipped_html(tmp_path):
    page = tmp_path / "a.html"
    page.write_text("<html><style>x{}</style><script>bad()</script><h1>Title&amp;more</h1><p>Body</p></html>")
    assert extract.text_of(page) == "Title&more\nBody"
    z = tmp_path / "b.html"
    z.write_bytes(zipped({"x/index.html": b"<p>Inside zip</p>"}))
    assert extract.text_of(z) == "Inside zip"
