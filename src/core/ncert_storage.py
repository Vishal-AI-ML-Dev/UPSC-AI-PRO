"""NCERT chapter-PDF storage: private Cloudflare R2 bucket or a local folder.

Both backends expose the same catalog of ``<class>/<subject>/<chapter>.pdf``
entries and serve chapter bytes ONLY for an exact (class, subject, chapter)
triple present in that catalog. User input is never used to build a path or
object key until it has passed segment validation AND the catalog allowlist, so
``..``, separators, absolute paths and unknown names are rejected before any
file read or R2 request.

Privacy: R2 objects are downloaded server-side with the configured credentials.
No public or presigned URL is ever created, and credentials, endpoint and object
keys are never returned to clients or written to logs. Callers get a relative
reference (``class6/science/1-Food.pdf``) for display and citations.

Backend selection (``get_ncert_source``):
  * all four R2_* settings set  -> ``R2NcertSource``
  * none set                    -> ``LocalNcertSource(NCERT_DATA_DIR)`` (local dev)
  * only some set               -> ``UnavailableNcertSource`` (misconfiguration;
    no silent fallback to local files)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from src.core.config import settings

logger = logging.getLogger(__name__)

Catalog = dict[str, dict[str, list[str]]]

_MAX_SEGMENT_LEN = 200
_R2_SETTINGS = {
    "R2_ENDPOINT_URL": "r2_endpoint_url",
    "R2_BUCKET_NAME": "r2_bucket_name",
    "R2_ACCESS_KEY_ID": "r2_access_key_id",
    "R2_SECRET_ACCESS_KEY": "r2_secret_access_key",
}


class NcertNotFound(FileNotFoundError):
    """The requested class/subject/chapter is not in the library (or is invalid)."""


class NcertUnavailable(RuntimeError):
    """The NCERT storage backend cannot be reached or is misconfigured."""


class NcertPdfError(ValueError):
    """A chapter PDF exists but cannot be used (too large, unreadable, scanned)."""


def is_valid_segment(value) -> bool:
    """True if ``value`` can only ever name ONE entry inside its parent folder.

    Rejects empty/oversized values, ``.``/``..``, path separators, NUL and other
    control characters. This is a syntactic guard only; membership in the
    catalog is checked separately.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_SEGMENT_LEN:
        return False
    if value in (".", "..") or value != value.strip():
        return False
    if "/" in value or "\\" in value:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def chapter_ref(class_name: str, subject: str, chapter: str) -> str:
    """Relative, storage-neutral reference used in responses and citations."""
    return f"{class_name}/{subject}/{chapter}"


def _is_pdf_name(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _add(catalog: Catalog, class_name: str, subject: str, chapter: str) -> None:
    if all(is_valid_segment(p) for p in (class_name, subject, chapter)) and _is_pdf_name(chapter):
        catalog.setdefault(class_name, {}).setdefault(subject, []).append(chapter)


def _sorted(catalog: Catalog) -> Catalog:
    return {
        c: {s: sorted(set(chs)) for s, chs in sorted(subjects.items())}
        for c, subjects in sorted(catalog.items())
    }


class _CatalogSource:
    """Shared catalog caching, listing and allowlist resolution."""

    kind = "base"

    def __init__(self, ttl_seconds: Optional[float] = None, max_pdf_bytes: Optional[int] = None):
        self._ttl = settings.ncert_catalog_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._max_bytes = (
            settings.ncert_max_pdf_mb * 1024 * 1024 if max_pdf_bytes is None else max_pdf_bytes
        )
        self._lock = threading.Lock()
        self._catalog: Optional[Catalog] = None
        self._loaded_at = 0.0

    # -- to implement ---------------------------------------------------- #
    def _load_catalog(self) -> Catalog:  # pragma: no cover - abstract
        raise NotImplementedError

    def _read(self, class_name: str, subject: str, chapter: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    # -- catalog --------------------------------------------------------- #
    def catalog(self) -> Catalog:
        """The (cached) library catalog. Raises NcertUnavailable if it can't be loaded."""
        with self._lock:
            fresh = self._catalog is not None and (time.monotonic() - self._loaded_at) < self._ttl
            if not fresh:
                self._catalog = _sorted(self._load_catalog())
                self._loaded_at = time.monotonic()
            return self._catalog

    def clear_cache(self) -> None:
        with self._lock:
            self._catalog = None

    def classes(self) -> list[str]:
        return list(self.catalog())

    def subjects(self, class_name: str) -> list[str]:
        return list(self.catalog().get(class_name, {})) if is_valid_segment(class_name) else []

    def chapters(self, class_name: str, subject: str) -> list[str]:
        if not (is_valid_segment(class_name) and is_valid_segment(subject)):
            return []
        return list(self.catalog().get(class_name, {}).get(subject, []))

    def resolve(self, class_name: str, subject: str, chapter: str) -> tuple[str, str, str]:
        """Return the triple only if it is valid AND listed; else NcertNotFound."""
        if not all(is_valid_segment(p) for p in (class_name, subject, chapter)):
            raise NcertNotFound("Chapter not found")
        if chapter not in self.catalog().get(class_name, {}).get(subject, []):
            raise NcertNotFound("Chapter not found")
        return class_name, subject, chapter

    def read_pdf(self, class_name: str, subject: str, chapter: str) -> bytes:
        """Bytes of an allow-listed chapter PDF."""
        return self._read(*self.resolve(class_name, subject, chapter))

    def _too_large(self, size: Optional[int]) -> bool:
        return size is not None and size > self._max_bytes


class LocalNcertSource(_CatalogSource):
    """Chapter PDFs in a local folder ``<base>/<class>/<subject>/<chapter>.pdf``."""

    kind = "local"

    def __init__(self, base_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._base = os.path.realpath(base_dir)

    def _load_catalog(self) -> Catalog:
        catalog: Catalog = {}
        if not os.path.isdir(self._base):
            logger.warning(
                "NCERT base directory not found. Place chapter PDFs as "
                "<NCERT_DATA_DIR>/<class>/<subject>/<chapter>.pdf or configure R2."
            )
            return catalog
        for class_name in os.listdir(self._base):
            class_dir = os.path.join(self._base, class_name)
            if not os.path.isdir(class_dir):
                continue
            for subject in os.listdir(class_dir):
                subject_dir = os.path.join(class_dir, subject)
                if not os.path.isdir(subject_dir):
                    continue
                for chapter in os.listdir(subject_dir):
                    if os.path.isfile(os.path.join(subject_dir, chapter)):
                        _add(catalog, class_name, subject, chapter)
        return catalog

    def _read(self, class_name: str, subject: str, chapter: str) -> bytes:
        path = os.path.realpath(os.path.join(self._base, class_name, subject, chapter))
        # Defence in depth: the resolved file must still live under the base dir.
        if os.path.commonpath([self._base, path]) != self._base or not os.path.isfile(path):
            raise NcertNotFound("Chapter not found")
        if self._too_large(os.path.getsize(path)):
            raise NcertPdfError("This chapter PDF is too large to process.")
        with open(path, "rb") as fh:
            return fh.read()


class R2NcertSource(_CatalogSource):
    """Chapter PDFs in a private Cloudflare R2 bucket (S3-compatible API)."""

    kind = "r2"

    def __init__(self, client, bucket: str, prefix: str = "ncert/", **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._bucket = bucket
        prefix = (prefix or "").strip("/")
        self._prefix = f"{prefix}/" if prefix else ""

    def _load_catalog(self) -> Catalog:
        catalog: Catalog = {}
        kwargs = {"Bucket": self._bucket, "Prefix": self._prefix}
        try:
            while True:
                page = self._client.list_objects_v2(**kwargs)
                for obj in page.get("Contents", []) or []:
                    rel = str(obj.get("Key", ""))[len(self._prefix) :]
                    parts = rel.split("/")
                    if len(parts) == 3:
                        _add(catalog, *parts)
                if not page.get("IsTruncated"):
                    break
                kwargs["ContinuationToken"] = page["NextContinuationToken"]
        except Exception as exc:  # noqa: BLE001 - any client/network error
            logger.error("NCERT R2 listing failed (%s)", type(exc).__name__)
            raise NcertUnavailable("NCERT library is temporarily unavailable") from None
        return catalog

    def _read(self, class_name: str, subject: str, chapter: str) -> bytes:
        key = self._prefix + chapter_ref(class_name, subject, chapter)
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise NcertNotFound("Chapter not found") from None
            logger.error("NCERT R2 download failed (%s %s)", type(exc).__name__, code or "-")
            raise NcertUnavailable("NCERT library is temporarily unavailable") from None
        body = obj["Body"]
        try:
            if self._too_large(obj.get("ContentLength")):
                raise NcertPdfError("This chapter PDF is too large to process.")
            data = body.read(self._max_bytes + 1)
        except NcertPdfError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("NCERT R2 download failed (%s)", type(exc).__name__)
            raise NcertUnavailable("NCERT library is temporarily unavailable") from None
        finally:
            try:
                body.close()
            except Exception:  # noqa: BLE001
                pass
        if len(data) > self._max_bytes:
            raise NcertPdfError("This chapter PDF is too large to process.")
        return data


class UnavailableNcertSource(_CatalogSource):
    """Used when R2 is only partly configured: every access reports unavailable."""

    kind = "unavailable"

    def _load_catalog(self) -> Catalog:
        raise NcertUnavailable("NCERT library is temporarily unavailable")

    def catalog(self) -> Catalog:
        return self._load_catalog()


def make_r2_client():
    """boto3 S3 client for Cloudflare R2 (credentials from settings, never logged)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
            # R2 does not accept every default checksum newer botocore sends.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _build_source() -> _CatalogSource:
    present = {name: bool(getattr(settings, attr)) for name, attr in _R2_SETTINGS.items()}
    if all(present.values()):
        logger.info("NCERT library: Cloudflare R2 (bucket=%s)", settings.r2_bucket_name)
        try:
            client = make_r2_client()
        except Exception as exc:  # noqa: BLE001
            logger.error("NCERT R2 client could not be created (%s)", type(exc).__name__)
            return UnavailableNcertSource()
        return R2NcertSource(client, settings.r2_bucket_name, settings.ncert_r2_prefix)
    if any(present.values()):
        missing = ", ".join(name for name, ok in present.items() if not ok)
        logger.warning("NCERT R2 is partly configured (missing: %s); library unavailable", missing)
        return UnavailableNcertSource()
    logger.info("NCERT library: local folder (NCERT_DATA_DIR)")
    return LocalNcertSource(settings.ncert_data_dir)


_source: Optional[_CatalogSource] = None
_source_lock = threading.Lock()


def get_ncert_source() -> _CatalogSource:
    """Process-wide NCERT source, built lazily from settings."""
    global _source
    with _source_lock:
        if _source is None:
            _source = _build_source()
        return _source


def set_ncert_source(source: Optional[_CatalogSource]) -> None:
    """Replace (or with None, reset) the process-wide source. For tests/scripts."""
    global _source
    with _source_lock:
        _source = source
