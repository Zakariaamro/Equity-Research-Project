from __future__ import annotations

import gzip
import json

import pytest

from edgar import config, db, fetch
from edgar.edgar_client import EdgarNotFoundError

_EMPTY_INDEX_HTML = b"<html><body>no Document Format Files table here</body></html>"


class FakeFetchClient:
    def __init__(
        self, index_items=None, file_contents=None, missing=None, index_html=None
    ) -> None:
        self.index_items = index_items or []
        self.file_contents = file_contents or {}
        self.missing = missing or set()
        self.index_html = index_html if index_html is not None else _EMPTY_INDEX_HTML
        self.requested_files: list[str] = []

    def get_filing_index(self, cik: str, accession_no: str):
        return self.index_items

    def get_archive_file(self, cik: str, accession_no: str, filename: str) -> bytes:
        self.requested_files.append(filename)
        if filename in self.missing:
            raise EdgarNotFoundError(f"{filename} missing")
        if filename == f"{accession_no}{config.FILING_INDEX_HTML_SUFFIX}":
            return self.index_html
        return self.file_contents.get(filename, filename.encode())


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "RAW_ARCHIVE_DIR", tmp_path / "raw")
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection
    connection.close()


def insert_filing(conn, accession_no, cik, form_type, primary_doc, items=None):
    conn.execute(
        """
        INSERT INTO filings (
            accession_no, cik, form_type, filing_date, period_end,
            items, primary_doc, raw_path, discovered_at, status
        ) VALUES (?, ?, ?, '2026-01-01', '2025-12-31', ?, ?, NULL, '2026-01-01T00:00:00', 'discovered')
        """,
        (accession_no, cik, form_type, items, primary_doc),
    )
    conn.commit()


def test_fetch_10k_archives_primary_and_summary(conn):
    insert_filing(conn, "0001018724-26-000004", "0001018724", "10-K", "amzn-20251231.htm")
    client = FakeFetchClient(
        file_contents={
            "amzn-20251231.htm": b"<html>10-K</html>",
            "FilingSummary.xml": b"<FilingSummary/>",
        }
    )

    status = fetch.fetch_filing(conn, client, "0001018724-26-000004")

    assert status == "fetched"
    archive_dir = config.RAW_ARCHIVE_DIR / "0001018724" / "0001018724-26-000004"
    assert (archive_dir / "amzn-20251231.htm.gz").exists()
    assert (archive_dir / "FilingSummary.xml.gz").exists()
    assert gzip.decompress((archive_dir / "amzn-20251231.htm.gz").read_bytes()) == (
        b"<html>10-K</html>"
    )

    row = conn.execute(
        "SELECT status, raw_path FROM filings WHERE accession_no = ?",
        ("0001018724-26-000004",),
    ).fetchone()
    assert row["status"] == "fetched"
    assert row["raw_path"] == str(archive_dir)


def test_fetch_filingsummary_absent_is_not_an_error(conn):
    insert_filing(conn, "0001018724-26-000099", "0001018724", "10-Q", "amzn-x.htm")
    client = FakeFetchClient(
        file_contents={"amzn-x.htm": b"body"},
        missing={"FilingSummary.xml"},
    )

    status = fetch.fetch_filing(conn, client, "0001018724-26-000099")

    assert status == "fetched"
    archive_dir = config.RAW_ARCHIVE_DIR / "0001018724" / "0001018724-26-000099"
    assert (archive_dir / "amzn-x.htm.gz").exists()
    assert not (archive_dir / "FilingSummary.xml.gz").exists()


def test_fetch_8k_archives_all_index_documents(conn):
    insert_filing(
        conn,
        "0001018724-26-000002",
        "0001018724",
        "8-K",
        "amzn-20260205.htm",
        items="2.02,9.01",
    )
    index_items = [
        {"name": "amzn-20260205.htm"},
        {"name": "amzn-20251231xex991.htm"},
        {"name": "amzn-20251231xex992.htm"},
        {"name": "FilingSummary.xml"},
    ]
    client = FakeFetchClient(index_items=index_items)

    status = fetch.fetch_filing(conn, client, "0001018724-26-000002")

    assert status == "fetched"
    archive_dir = config.RAW_ARCHIVE_DIR / "0001018724" / "0001018724-26-000002"
    for item in index_items:
        assert (archive_dir / f"{item['name']}.gz").exists()


def test_fetch_8k_missing_exhibit_991_by_type_does_not_fail(conn, caplog):
    insert_filing(
        conn, "0001018724-26-000003", "0001018724", "8-K", "amzn-20260206.htm", items="2.02"
    )
    index_items = [{"name": "amzn-20260206.htm"}]
    # A Document Format Files table that genuinely has no EX-99.1 row.
    index_html = b"""
    <html><body>
    <table class="tableFile" summary="Document Format Files">
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>8-K</td><td><a href="x">amzn-20260206.htm</a></td><td>8-K</td><td>100</td></tr>
    </table>
    </body></html>
    """
    client = FakeFetchClient(index_items=index_items, index_html=index_html)

    with caplog.at_level("WARNING"):
        status = fetch.fetch_filing(conn, client, "0001018724-26-000003")

    assert status == "fetched"
    assert any("no document of type EX-99.1" in message for message in caplog.messages)


def test_fetch_8k_exhibit_991_type_lookup_unavailable_does_not_warn(conn, caplog):
    """If the type lookup itself fails, we cannot claim EX-99.1 is absent --
    only that we don't know. That must not produce a false 'missing' warning."""
    insert_filing(
        conn, "0001018724-26-000003", "0001018724", "8-K", "amzn-20260206.htm", items="2.02"
    )
    index_items = [{"name": "amzn-20260206.htm"}]
    client = FakeFetchClient(
        index_items=index_items,
        missing={"0001018724-26-000003-index.html"},
    )

    with caplog.at_level("WARNING"):
        status = fetch.fetch_filing(conn, client, "0001018724-26-000003")

    assert status == "fetched"
    assert not any("Exhibit 99.1" in m or "EX-99.1" in m for m in caplog.messages)


def test_fetch_skips_existing_archive(conn):
    insert_filing(conn, "0001018724-26-000004", "0001018724", "10-K", "amzn-20251231.htm")
    client = FakeFetchClient(
        file_contents={"amzn-20251231.htm": b"body", "FilingSummary.xml": b"summary"}
    )

    fetch.fetch_filing(conn, client, "0001018724-26-000004")
    calls_before = len(client.requested_files)
    status = fetch.fetch_filing(conn, client, "0001018724-26-000004")

    assert status == "skipped"
    assert len(client.requested_files) == calls_before


def test_fetch_forced_redownloads_existing_archive(conn):
    insert_filing(conn, "0001018724-26-000004", "0001018724", "10-K", "amzn-20251231.htm")
    client = FakeFetchClient(
        file_contents={"amzn-20251231.htm": b"body", "FilingSummary.xml": b"summary"}
    )

    fetch.fetch_filing(conn, client, "0001018724-26-000004")
    calls_before = len(client.requested_files)
    status = fetch.fetch_filing(conn, client, "0001018724-26-000004", force=True)

    assert status == "fetched"
    assert len(client.requested_files) > calls_before


def test_fetch_marks_failed_when_nothing_can_be_archived(conn):
    insert_filing(conn, "0001018724-26-000005", "0001018724", "10-K", "missing.htm")
    client = FakeFetchClient(missing={"missing.htm", "FilingSummary.xml"})

    status = fetch.fetch_filing(conn, client, "0001018724-26-000005")

    assert status == "failed"
    row = conn.execute(
        "SELECT status FROM filings WHERE accession_no = ?", ("0001018724-26-000005",)
    ).fetchone()
    assert row["status"] == "failed"


def test_fetch_pending_continues_after_one_failure(conn):
    insert_filing(conn, "0001018724-26-000005", "0001018724", "10-K", "missing.htm")
    insert_filing(conn, "0001018724-26-000004", "0001018724", "10-K", "amzn-20251231.htm")

    class MixedClient(FakeFetchClient):
        def get_archive_file(self, cik, accession_no, filename):
            if accession_no == "0001018724-26-000005":
                raise EdgarNotFoundError("missing")
            return super().get_archive_file(cik, accession_no, filename)

    client = MixedClient(
        file_contents={"amzn-20251231.htm": b"body", "FilingSummary.xml": b"summary"}
    )

    results = fetch.fetch_pending(conn, client)
    statuses = {r["accession_no"]: r["status"] for r in results}

    assert statuses["0001018724-26-000005"] == "failed"
    assert statuses["0001018724-26-000004"] == "fetched"


def test_fetch_10k_index_json_failure_degrades_manifest_not_ingestion(conn):
    """index.json failing for a 10-K must not block the actual archive --
    only the manifest's completeness degrades."""
    insert_filing(conn, "0001018724-26-000004", "0001018724", "10-K", "amzn-20251231.htm")

    class NoIndexClient(FakeFetchClient):
        def get_filing_index(self, cik, accession_no):
            raise EdgarNotFoundError("index.json missing")

    client = NoIndexClient(
        file_contents={"amzn-20251231.htm": b"<html>10-K</html>", "FilingSummary.xml": b"s"}
    )

    status = fetch.fetch_filing(conn, client, "0001018724-26-000004")

    assert status == "fetched"
    archive_dir = config.RAW_ARCHIVE_DIR / "0001018724" / "0001018724-26-000004"
    manifest = json.loads((archive_dir / config.MANIFEST_FILENAME).read_text())
    filenames = {d["filename"] for d in manifest["documents"]}
    assert filenames == {"amzn-20251231.htm", "FilingSummary.xml"}


def test_fetch_8k_index_json_failure_is_a_hard_failure(conn):
    """For 8-Ks, index.json IS the download plan -- its failure must fail the fetch."""
    insert_filing(
        conn, "0001018724-26-000002", "0001018724", "8-K", "amzn-20260205.htm", items="2.02"
    )

    class NoIndexClient(FakeFetchClient):
        def get_filing_index(self, cik, accession_no):
            raise EdgarNotFoundError("index.json missing")

    client = NoIndexClient()

    status = fetch.fetch_filing(conn, client, "0001018724-26-000002")

    assert status == "failed"
