"""Source manifest: the selected filing and, once downloaded, its retrieval record."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from finreport.schemas import ACCESSION_RE, SHA256_RE, Strict


class Target(Strict):
    company: str
    cik: str
    form: str
    accession: str
    fiscal_period_end: date
    filing_date: date
    url: str
    index_url: str

    @model_validator(mode="after")
    def _check(self) -> "Target":
        if not ACCESSION_RE.match(self.accession):
            raise ValueError("malformed accession")
        return self


class Retrieval(Strict):
    requested_url: str
    final_url: str
    retrieved_at_utc: datetime
    http_status: int
    content_type: str
    cached_filename: str
    byte_count: int
    sha256: str
    declared_user_agent: bool

    @model_validator(mode="after")
    def _check(self) -> "Retrieval":
        if not SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        if self.byte_count <= 0:
            raise ValueError("byte_count must be positive")
        return self


class ExternalImport(Strict):
    """A filing obtained outside this tool and imported from a local file.

    Deliberately has no HTTP status, content type, final URL or download timestamp: this tool did not
    observe any of them. `operator_reported_acquired_at` is free text supplied by the operator and is
    not verified. `imported_at_utc` is when this tool copied and hashed the file, not when it was downloaded.
    """

    acquisition_method: str = Field(min_length=10)
    claimed_source_url: str | None = None
    operator_reported_acquired_at: str | None = None
    original_filename: str
    imported_at_utc: datetime
    cached_filename: str
    byte_count: int
    sha256: str
    operator_supplied_sha256_verified: bool

    @model_validator(mode="after")
    def _check(self) -> "ExternalImport":
        if not SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        if self.byte_count <= 0:
            raise ValueError("byte_count must be positive")
        return self


class SourceManifest(Strict):
    manifest_version: int = 1
    status: Literal["pending_download", "downloaded", "imported_offline"]
    status_detail: str
    target: Target
    retrieval: Retrieval | None = None
    external_import: ExternalImport | None = None
    amendment_search: dict[str, str]

    @model_validator(mode="after")
    def _consistent(self) -> "SourceManifest":
        expected = {
            "pending_download": (False, False),
            "downloaded": (True, False),
            "imported_offline": (False, True),
        }[self.status]
        if (self.retrieval is not None, self.external_import is not None) != expected:
            raise ValueError(
                f"status {self.status!r} requires retrieval={expected[0]} and external_import={expected[1]} records"
            )
        return self

    def recorded_source(self) -> tuple[str, int, str] | None:
        """(cached filename, byte count, sha256) of the recorded source, whichever way it was acquired."""
        rec = self.retrieval or self.external_import
        return (rec.cached_filename, rec.byte_count, rec.sha256) if rec else None


def initial_manifest() -> SourceManifest:
    return SourceManifest(
        status="pending_download",
        status_detail="No download attempted by the tool yet. Requires SEC_USER_AGENT and network access to www.sec.gov.",
        target=Target(
            company="Microsoft Corporation",
            cik="0000789019",
            form="10-K",
            accession="0000950170-24-087843",
            fiscal_period_end=date(2024, 6, 30),
            filing_date=date(2024, 7, 30),
            url="https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm",
            index_url="https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/0000950170-24-087843-index.htm",
        ),
        amendment_search={
            "status": "pending",
            "note": "Search for subsequent amendments (10-K/A) has not been performed. Do not claim none exist.",
        },
    )


def load_manifest(path: Path) -> SourceManifest:
    return SourceManifest.model_validate_json(path.read_text(encoding="utf-8"))


def save_manifest(manifest: SourceManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(manifest.model_dump_json())
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
