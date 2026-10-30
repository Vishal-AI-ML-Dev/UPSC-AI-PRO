"""NCERT library listing: a missing data directory is reported clearly, not as an error."""

import pytest

import src.agents.ncert.graph as ncert_graph

NCERT = "/api/v1/ncert"


@pytest.fixture
def ncert_base(monkeypatch):
    """Point the NCERT module at a chosen directory with fresh listing caches."""

    def _set(path):
        monkeypatch.setattr(ncert_graph, "BASE", str(path))
        for fn in (ncert_graph.get_classes, ncert_graph.get_subjects, ncert_graph.get_chapters):
            fn.cache_clear()

    yield _set
    for fn in (ncert_graph.get_classes, ncert_graph.get_subjects, ncert_graph.get_chapters):
        fn.cache_clear()


def test_missing_library_returns_setup_message(client, make_user, ncert_base, tmp_path):
    h = make_user("ncert").headers
    ncert_base(tmp_path / "does-not-exist")

    r = client.get(f"{NCERT}/classes", headers=h)

    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["available"] is False
    assert body["message"] == ncert_graph.NCERT_UNAVAILABLE_MESSAGE
    assert str(tmp_path) not in body["message"]  # no server paths leak to users


def test_installed_library_lists_classes_subjects_chapters(client, make_user, ncert_base, tmp_path):
    h = make_user("ncert").headers
    chapter_dir = tmp_path / "class6" / "science"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "1-Food.pdf").write_bytes(b"%PDF-1.4\n")
    ncert_base(tmp_path)

    classes = client.get(f"{NCERT}/classes", headers=h).json()
    assert classes["items"] == ["class6"]
    assert classes["available"] is True
    assert classes["message"] is None
    assert client.get(f"{NCERT}/subjects/class6", headers=h).json()["items"] == ["science"]
    assert client.get(f"{NCERT}/chapters/class6/science", headers=h).json()["items"] == [
        "1-Food.pdf"
    ]
