"""NCERT storage: private R2 bucket + local folder, allow-listing and leak-free errors.

A fake S3 client stands in for Cloudflare R2, so nothing here touches the
network or real credentials. The fake records every list/get call so tests can
prove that rejected input never reaches the bucket.
"""

import io
import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import src.agents.ncert.graph as ncert
import src.core.ncert_storage as storage
from src.core.ncert_storage import (
    LocalNcertSource,
    NcertNotFound,
    NcertPdfError,
    NcertUnavailable,
    R2NcertSource,
    UnavailableNcertSource,
    set_ncert_source,
)

NCERT = "/api/v1/ncert"
BUCKET = "test-bucket"
UNICODE_CH = "11-Nature\u2019s Treasures.pdf"
UNICODE_KEY = f"ncert/class7/socialscience(Part-1)/{UNICODE_CH}"

OBJECTS = {
    "ncert/": b"",  # folder markers created by dashboards
    "ncert/class6/science/": b"",
    "ncert/class6/science/1-Food.pdf": b"%PDF-food",
    "ncert/class6/science/2-Water.pdf": b"%PDF-water",
    "ncert/class6/science/notes.txt": b"not a pdf",
    "ncert/class6/stray.pdf": b"%PDF-wrong-depth",
    "ncert/class6/socialscience/1-Maps.pdf": b"%PDF-maps",
    UNICODE_KEY: b"%PDF-treasures",
    "ncert/a/b/c/too-deep.pdf": b"%PDF",
    "other/class6/science/outside.pdf": b"%PDF-outside-prefix",
}


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, data):
        self._data = data
        self.closed = False

    def read(self, n=-1):
        return self._data if n is None or n < 0 else self._data[:n]

    def close(self):
        self.closed = True


class FakeS3:
    def __init__(self, objects=None, page_size=1000, fail_list=False, fail_get=None, length=None):
        self.objects = dict(OBJECTS if objects is None else objects)
        self.page_size = page_size
        self.fail_list = fail_list
        self.fail_get = fail_get
        self.length = length  # override reported ContentLength
        self.list_calls = []
        self.get_calls = []
        self.bodies = []

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        self.list_calls.append({"Bucket": Bucket, "Prefix": Prefix, "Token": ContinuationToken})
        if self.fail_list:
            raise ConnectionError("endpoint https://secret-account.example unreachable")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start : start + self.page_size]
        page = {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in chunk]}
        if start + self.page_size < len(keys):
            page["IsTruncated"] = True
            page["NextContinuationToken"] = str(start + self.page_size)
        else:
            page["IsTruncated"] = False
        return page

    def get_object(self, Bucket, Key):
        self.get_calls.append({"Bucket": Bucket, "Key": Key})
        if self.fail_get:
            raise _ClientError(self.fail_get)
        if Key not in self.objects:
            raise _ClientError("NoSuchKey")
        data = self.objects[Key]
        body = _Body(data)
        self.bodies.append(body)
        return {"Body": body, "ContentLength": len(data) if self.length is None else self.length}


def r2(fake=None, **kwargs):
    fake = fake or FakeS3()
    return R2NcertSource(fake, BUCKET, "ncert/", **kwargs), fake


@pytest.fixture
def use_source():
    """Install a given NCERT source for the app; always reset afterwards."""
    yield set_ncert_source
    set_ncert_source(None)


# --------------------------------------------------------------------------- #
# R2 catalog
# --------------------------------------------------------------------------- #
def test_r2_catalog_lists_only_class_subject_chapter_pdfs():
    src, fake = r2()
    assert src.catalog() == {
        "class6": {
            "science": ["1-Food.pdf", "2-Water.pdf"],
            "socialscience": ["1-Maps.pdf"],
        },
        "class7": {"socialscience(Part-1)": [UNICODE_CH]},
    }
    assert fake.list_calls[0]["Bucket"] == BUCKET
    assert fake.list_calls[0]["Prefix"] == "ncert/"
    assert fake.get_calls == []  # listing never downloads objects


def test_r2_catalog_follows_pagination():
    src, fake = r2(FakeS3(page_size=2))
    assert src.chapters("class6", "science") == ["1-Food.pdf", "2-Water.pdf"]
    assert len(fake.list_calls) > 1
    assert fake.list_calls[1]["Token"] == "2"


def test_catalog_is_cached_until_ttl_expires():
    src, fake = r2(ttl_seconds=600)
    src.classes()
    src.subjects("class6")
    src.chapters("class6", "science")
    assert len(fake.list_calls) == 1
    src_nocache, fake2 = r2(ttl_seconds=0)
    src_nocache.classes()
    src_nocache.classes()
    assert len(fake2.list_calls) == 2


def test_listing_failure_is_unavailable_without_leaking_details(caplog):
    src, _ = r2(FakeS3(fail_list=True))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(NcertUnavailable) as exc:
            src.classes()
    assert "secret-account" not in str(exc.value)
    assert "secret-account" not in caplog.text


# --------------------------------------------------------------------------- #
# R2 reads
# --------------------------------------------------------------------------- #
def test_r2_reads_exact_key_with_parentheses_and_unicode():
    src, fake = r2()
    assert src.read_pdf("class7", "socialscience(Part-1)", UNICODE_CH) == b"%PDF-treasures"
    assert fake.get_calls == [{"Bucket": BUCKET, "Key": UNICODE_KEY}]
    assert fake.bodies[0].closed


BAD_TRIPLES = [
    ("..", "..", "secret.pdf"),
    ("class6", "science", "../../secret.pdf"),
    ("class6", "..", "1-Food.pdf"),
    ("class6", "science", "..\\1-Food.pdf"),
    ("class6", "science", "/etc/passwd"),
    ("class6", "science", "C:\\Windows\\win.ini"),
    ("class6", "science", "1-Food.pdf\x00"),
    ("class6", "science", "1-Food.pdf\n"),
    ("class6/science", "x", "1-Food.pdf"),
    ("", "science", "1-Food.pdf"),
    (" class6", "science", "1-Food.pdf"),
    ("class6", "science", "9-Not-In-Catalog.pdf"),
    ("class6", "science", "notes.txt"),
    ("class6", "socialscience", "1-Food.pdf"),  # real file, wrong subject
    ("class6", "science", "x" * 300 + ".pdf"),
]


@pytest.mark.parametrize("triple", BAD_TRIPLES)
def test_r2_rejects_anything_not_allow_listed_before_any_download(triple):
    src, fake = r2()
    with pytest.raises(NcertNotFound) as exc:
        src.read_pdf(*triple)
    assert str(exc.value) == "Chapter not found"
    assert fake.get_calls == []


def test_r2_refuses_objects_over_the_size_limit():
    src, fake = r2(max_pdf_bytes=4)
    with pytest.raises(NcertPdfError):
        src.read_pdf("class6", "science", "1-Food.pdf")
    assert fake.bodies[0].closed
    # Without a ContentLength the bounded read still enforces the limit.
    src2, _ = r2(FakeS3(length=None), max_pdf_bytes=4)
    src2._client.length = None
    src2._client.objects["ncert/class6/science/1-Food.pdf"] = b"%PDF-much-too-long"
    with pytest.raises(NcertPdfError):
        src2.read_pdf("class6", "science", "1-Food.pdf")


def test_r2_missing_object_is_not_found_and_errors_are_unavailable():
    src, fake = r2()
    src.catalog()  # cached listing still names the chapter...
    fake.objects.pop("ncert/class6/science/1-Food.pdf")  # ...but the object is gone
    with pytest.raises(NcertNotFound):
        src.read_pdf("class6", "science", "1-Food.pdf")

    src2, _ = r2(FakeS3(fail_get="AccessDenied"))
    with pytest.raises(NcertUnavailable):
        src2.read_pdf("class6", "science", "1-Food.pdf")


# --------------------------------------------------------------------------- #
# Local folder backend
# --------------------------------------------------------------------------- #
def test_local_source_lists_pdfs_and_blocks_escape(tmp_path):
    base = tmp_path / "library"
    (base / "class6" / "science").mkdir(parents=True)
    (base / "class6" / "science" / "1-Food.pdf").write_bytes(b"%PDF-food")
    (base / "class6" / "science" / "readme.txt").write_text("x")
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-secret")  # outside the library

    src = LocalNcertSource(str(base))
    assert src.catalog() == {"class6": {"science": ["1-Food.pdf"]}}
    assert src.read_pdf("class6", "science", "1-Food.pdf") == b"%PDF-food"
    for triple in [("..", "..", "secret.pdf"), ("class6", "science", "../../../secret.pdf")]:
        with pytest.raises(NcertNotFound):
            src.read_pdf(*triple)
    assert src.subjects("..") == []
    assert src.chapters("..", "..") == []


def test_local_missing_directory_is_an_empty_library(tmp_path):
    assert LocalNcertSource(str(tmp_path / "nope")).classes() == []


# --------------------------------------------------------------------------- #
# Backend selection from settings
# --------------------------------------------------------------------------- #
R2_ATTRS = ("r2_endpoint_url", "r2_bucket_name", "r2_access_key_id", "r2_secret_access_key")
FAKE_SECRET = "fake-secret-value-for-tests-only"


def _r2_settings(monkeypatch, **values):
    for attr in R2_ATTRS:
        monkeypatch.setattr(storage.settings, attr, values.get(attr, ""))


def test_all_r2_settings_select_r2(monkeypatch):
    _r2_settings(
        monkeypatch,
        r2_endpoint_url="https://example.invalid",
        r2_bucket_name=BUCKET,
        r2_access_key_id="id",
        r2_secret_access_key=FAKE_SECRET,
    )
    monkeypatch.setattr(storage, "make_r2_client", lambda: FakeS3())
    assert isinstance(storage._build_source(), R2NcertSource)


def test_no_r2_settings_select_local_folder(monkeypatch):
    _r2_settings(monkeypatch)
    assert isinstance(storage._build_source(), LocalNcertSource)


def test_partial_r2_settings_are_unavailable_and_never_log_values(monkeypatch, caplog):
    _r2_settings(monkeypatch, r2_bucket_name=BUCKET, r2_secret_access_key=FAKE_SECRET)
    with caplog.at_level(logging.WARNING):
        src = storage._build_source()
    assert isinstance(src, UnavailableNcertSource)
    with pytest.raises(NcertUnavailable):
        src.classes()
    assert "R2_ENDPOINT_URL" in caplog.text and "R2_ACCESS_KEY_ID" in caplog.text
    assert FAKE_SECRET not in caplog.text


def test_r2_client_is_built_for_r2_without_network(monkeypatch):
    _r2_settings(
        monkeypatch,
        r2_endpoint_url="https://example.invalid",
        r2_bucket_name=BUCKET,
        r2_access_key_id="id",
        r2_secret_access_key=FAKE_SECRET,
    )
    client = storage.make_r2_client()
    assert client.meta.region_name == "auto"
    assert client.meta.endpoint_url == "https://example.invalid"
    assert client.meta.config.signature_version == "s3v4"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_routes_list_the_r2_library(client, make_user, use_source):
    h = make_user("r2list").headers
    src, _ = r2()
    use_source(src)
    classes = client.get(f"{NCERT}/classes", headers=h).json()
    assert classes["items"] == ["class6", "class7"] and classes["available"] is True
    assert client.get(f"{NCERT}/subjects/class7", headers=h).json()["items"] == [
        "socialscience(Part-1)"
    ]
    r = client.get(f"{NCERT}/chapters/class7/socialscience(Part-1)", headers=h)
    assert r.json()["items"] == [UNICODE_CH]


@pytest.mark.parametrize(
    "path", ["/subjects/%2E%2E", "/chapters/%2E%2E/%2E%2E", "/chapters/class6/%2E%2E"]
)
def test_listing_routes_reject_traversal(client, make_user, use_source, tmp_path, path):
    h = make_user("trav").headers
    (tmp_path / "lib" / "class6" / "science").mkdir(parents=True)
    use_source(LocalNcertSource(str(tmp_path / "lib")))
    r = client.get(NCERT + path, headers=h)
    assert r.status_code == 404
    assert str(tmp_path) not in r.text


@pytest.mark.parametrize("triple", BAD_TRIPLES[:6])
def test_study_rejects_traversal_with_generic_404(client, make_user, use_source, triple):
    h = make_user("study-trav").headers
    src, fake = r2()
    use_source(src)
    body = dict(zip(("class_name", "subject", "chapter"), triple))
    r = client.post(f"{NCERT}/study", headers=h, json=body)
    assert r.status_code == 404
    assert r.json() == {"detail": "Chapter not found"}
    assert fake.get_calls == []


def test_unavailable_library_reports_cleanly(client, make_user, use_source):
    h = make_user("unavail").headers
    use_source(UnavailableNcertSource())
    classes = client.get(f"{NCERT}/classes", headers=h)
    assert classes.status_code == 200
    assert classes.json()["available"] is False
    assert classes.json()["message"] == ncert.NCERT_TEMPORARILY_UNAVAILABLE_MESSAGE
    assert client.get(f"{NCERT}/subjects/class6", headers=h).status_code == 503
    body = {"class_name": "class6", "subject": "science", "chapter": "1-Food.pdf"}
    assert client.post(f"{NCERT}/study", headers=h, json=body).status_code == 503


@pytest.fixture
def study_stubs(monkeypatch):
    """No model, no embeddings, no index writes."""
    monkeypatch.setattr(
        ncert, "get_llm", lambda: RunnableLambda(lambda _: AIMessage(content="notes"))
    )
    import src.core.study_aids as aids

    monkeypatch.setattr(aids, "generate_study_aids", lambda *a, **k: ("", ""))
    captured = {}
    monkeypatch.setattr(ncert, "vector_store_exists", lambda key: False)
    monkeypatch.setattr(
        ncert,
        "upsert_documents",
        lambda key, docs, rebuild=False: captured.update(key=key, docs=docs),
    )
    return captured


def test_study_reads_pdf_from_r2_page_by_page(
    client, make_user, use_source, monkeypatch, study_stubs
):
    h = make_user("r2study").headers
    src, fake = r2()
    use_source(src)
    seen = {}

    class _Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class _Reader:
        def __init__(self, stream):
            assert isinstance(stream, io.BytesIO)  # parsed in memory, no temp file
            seen["bytes"] = stream.getvalue()
            self.pages = [_Page("Food page one."), _Page(""), _Page("Food page three.")]

    monkeypatch.setattr(ncert, "PdfReader", _Reader)
    body = {"class_name": "class6", "subject": "science", "chapter": "1-Food.pdf"}
    r = client.post(f"{NCERT}/study", headers=h, json=body)

    assert r.status_code == 200, r.text
    assert seen["bytes"] == b"%PDF-food"
    assert fake.get_calls == [{"Bucket": BUCKET, "Key": "ncert/class6/science/1-Food.pdf"}]
    assert r.json()["notes"] == "notes"
    assert r.json()["chapter_path"] == "class6/science/1-Food.pdf"
    docs = study_stubs["docs"]
    assert study_stubs["key"] == "ncert_v2_class6_science_1-food.pdf"  # index key unchanged
    assert {d.metadata["pdf_page"] for d in docs} == {1, 3}
    for d in docs:
        assert d.metadata["document"] == "1-Food.pdf"
        assert d.metadata["source_path"] == "class6/science/1-Food.pdf"
        assert BUCKET not in str(d.metadata)  # no storage location in the index


def test_unreadable_pdf_is_400_without_details(client, make_user, use_source, study_stubs):
    h = make_user("badpdf").headers
    src, _ = r2()
    use_source(src)
    body = {"class_name": "class6", "subject": "science", "chapter": "1-Food.pdf"}
    r = client.post(f"{NCERT}/study", headers=h, json=body)
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Could not")


def test_unexpected_study_error_does_not_leak(client, make_user, use_source, monkeypatch):
    h = make_user("leak").headers

    def _boom(**_):
        raise RuntimeError("internal detail /srv/app/data/ncert token=abc")

    import src.api.routes.ncert as route

    monkeypatch.setattr(route, "generate_study_session", _boom)
    body = {"class_name": "class6", "subject": "science", "chapter": "1-Food.pdf"}
    r = client.post(f"{NCERT}/study", headers=h, json=body)
    assert r.status_code == 500
    assert "srv" not in r.text and "token" not in r.text


def test_chat_with_invalid_names_never_touches_the_index(monkeypatch):
    def _no_index(key):
        raise AssertionError("index must not be loaded")

    monkeypatch.setattr(ncert, "load_vector_store", _no_index)
    out = "".join(ncert.ask_ncert("What is food?", "..", "..", "x.pdf"))
    assert "generate study session first" in out
