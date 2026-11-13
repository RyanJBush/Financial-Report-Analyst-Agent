"""Polite, cache-first download of one SEC filing.

Rules (from the project brief and SEC guidance as relayed in it; live recheck pending):
- Declared automated access: a User-Agent supplied by the operator via SEC_USER_AGENT.
  The tool never invents contact details and never contacts the network without one.
- Conservative limit: serial requests, at most one per second (SEC cap is 10/s).
- Bounded retries for transient failures only. 403/429 stop immediately (no bypass).
- Error pages are rejected, not cached.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from finreport.manifest import ExternalImport, Retrieval, SourceManifest, Target

USER_AGENT_ENV = "SEC_USER_AGENT"
MIN_INTERVAL_SECONDS = 1.0
MAX_ATTEMPTS = 3
DEFAULT_MIN_BYTES = 200_000
ACCEPTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
ERROR_PAGE_SIGNATURES = (
    "undeclared automated tool",
    "request rate threshold exceeded",
    "your request originated from",
    "sec.gov | request rate",
)


class IngestError(Exception):
    exit_code = 1


class MissingUserAgent(IngestError):
    exit_code = 3


class AccessDenied(IngestError):
    exit_code = 4


class InvalidFiling(IngestError):
    exit_code = 4


class FetchFailed(IngestError):
    exit_code = 7


class CacheMismatch(IngestError):
    exit_code = 5


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]  # lower-cased header names
    body: bytes
    final_url: str


Transport = Callable[[str, dict[str, str], float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the 3xx response is returned to the caller, which decides (it refuses).
    Following would issue a second request outside the caller's rate-limiter step."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
    """Real transport. Honors HTTPS_PROXY/SSL_CERT_FILE from the environment. Makes exactly one request."""
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)  # built per call so environment proxies are read at call time
    try:
        with opener.open(request, timeout=timeout) as resp:  # noqa: S310 (https only, checked by caller)
            return HttpResponse(
                status=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=resp.read(),
                final_url=resp.geturl(),
            )
    except urllib.error.HTTPError as err:
        return HttpResponse(
            status=err.code,
            headers={k.lower(): v for k, v in err.headers.items()},
            body=err.read(),
            final_url=err.geturl() or url,
        )


class RateLimiter:
    def __init__(
        self,
        min_interval: float = MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_user_agent(env: dict[str, str] | None = None) -> str:
    value = (env if env is not None else os.environ).get(USER_AGENT_ENV, "").strip()
    if not value:
        raise MissingUserAgent(
            f"{USER_AGENT_ENV} is not set. SEC requires a declared User-Agent with contact details; "
            "none was supplied, so no request was made."
        )
    return value


def _visible_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def validate_filing_body(body: bytes, target: Target, min_bytes: int) -> None:
    """Content checks shared by HTTP downloads and offline imports. Raises InvalidFiling."""
    head = body[:50_000].decode("utf-8", errors="replace").lower()
    for signature in ERROR_PAGE_SIGNATURES:
        if signature in head:
            raise InvalidFiling(f"response looks like an SEC error page (matched {signature!r})")
    if len(body) < min_bytes:
        raise InvalidFiling(f"body is {len(body)} bytes, below minimum {min_bytes}; likely not a full filing")
    if b"<html" not in body[:200_000].lower():
        raise InvalidFiling("body does not look like HTML")
    text = _visible_text(body).lower()
    required = (target.company.lower(), target.form.lower(), str(target.fiscal_period_end.year))
    missing = [m for m in required if m not in text]
    if missing:
        raise InvalidFiling(f"filing markers not found in body: {missing}")


def validate_filing_response(resp: HttpResponse, target: Target, min_bytes: int) -> None:
    """Raise InvalidFiling unless the response is plausibly the selected filing."""
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ACCEPTED_CONTENT_TYPES:
        raise InvalidFiling(f"unexpected content-type {content_type!r}")
    requested, final = urlparse(target.url), urlparse(resp.final_url)
    if (final.scheme, final.netloc, final.path) != (requested.scheme, requested.netloc, requested.path):
        raise InvalidFiling(f"redirected away from requested filing: {resp.final_url}")
    validate_filing_body(resp.body, target, min_bytes)


def cache_path(root: Path, target: Target) -> Path:
    return root / "data" / "cache" / Path(urlparse(target.url).path).name


def cache_is_valid(root: Path, manifest: SourceManifest) -> bool:
    return verify_cached_source(root, manifest)["verified"]


def _hash_and_size(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_cached_source(root: Path, manifest: SourceManifest) -> dict:
    """Check the CURRENT cached bytes against the manifest's recorded source, right now.

    Returns {"verified", "problems", "recorded", "actual"}. `problems` holds machine-readable codes:
      no_recorded_source    the manifest records neither a download nor an offline import
      cached_file_missing   no regular file at the cache path (absent, or a directory)
      cached_file_is_symlink  the cache path is a symlink, so the bytes' origin is not the cache
      filename_mismatch     the cache file's name differs from the recorded cached filename
      byte_count_mismatch   the file's size differs from the recorded byte count
      sha256_mismatch       the file's SHA-256 differs from the recorded one (also catches same-size edits)
    "verified" is True only when there are no problems.
    """
    recorded = manifest.recorded_source()
    if recorded is None:
        return {"verified": False, "problems": ["no_recorded_source"], "recorded": None, "actual": None}
    rec_name, rec_size, rec_sha = recorded
    path = cache_path(root, manifest.target)
    out = {"verified": False, "problems": [], "recorded": {"filename": rec_name, "byte_count": rec_size, "sha256": rec_sha}, "actual": None}
    if path.is_symlink():
        out["problems"].append("cached_file_is_symlink")
        return out
    if not path.is_file():
        out["problems"].append("cached_file_missing")
        return out
    sha, size = _hash_and_size(path)
    out["actual"] = {"filename": path.name, "byte_count": size, "sha256": sha}
    if path.name != rec_name:
        out["problems"].append("filename_mismatch")
    if size != rec_size:
        out["problems"].append("byte_count_mismatch")
    if sha != rec_sha:
        out["problems"].append("sha256_mismatch")
    out["verified"] = not out["problems"]
    return out


def fetch_filing(
    manifest: SourceManifest,
    root: Path,
    *,
    env: dict[str, str] | None = None,
    transport: Transport = urllib_transport,
    limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    refresh: bool = False,
    max_attempts: int = MAX_ATTEMPTS,
    min_bytes: int = DEFAULT_MIN_BYTES,
    timeout: float = 60.0,
) -> SourceManifest:
    """Return a manifest with a verified retrieval record, downloading only if needed."""
    if not refresh and cache_is_valid(root, manifest):
        return manifest  # zero requests

    user_agent = get_user_agent(env)  # raises before any transport call
    target = manifest.target
    if urlparse(target.url).scheme != "https":
        raise InvalidFiling("only https URLs are fetched")
    limiter = limiter or RateLimiter()
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}

    resp: HttpResponse | None = None
    last_error = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        limiter.wait()
        try:
            candidate = transport(target.url, headers, timeout)
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            last_error = f"transport error: {err!r}"
        else:
            if candidate.status in (403, 429):
                raise AccessDenied(
                    f"HTTP {candidate.status} from {target.url}. Stopping; not retrying or working around it."
                )
            if candidate.status == 200:
                resp = candidate
                break
            if 300 <= candidate.status < 400:
                raise InvalidFiling(
                    f"HTTP {candidate.status} redirect from {target.url} to {candidate.headers.get('location', '<no Location header>')!r}; "
                    "redirects are refused and not followed")
            if candidate.status >= 500:
                last_error = f"HTTP {candidate.status}"
            else:
                raise InvalidFiling(f"HTTP {candidate.status} for {target.url}")
        if attempt < max_attempts:
            sleep(2.0 ** attempt)
    if resp is None:
        raise FetchFailed(f"gave up after {max_attempts} attempts: {last_error}")

    validate_filing_response(resp, target, min_bytes)

    path = cache_path(root, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(resp.body)
    os.replace(tmp, path)
    saved_sha = sha256_file(path)  # checksum of the bytes actually on disk
    if saved_sha != hashlib.sha256(resp.body).hexdigest():
        raise CacheMismatch("saved file differs from downloaded bytes")

    retrieval = Retrieval(
        requested_url=target.url,
        final_url=resp.final_url,
        retrieved_at_utc=now(),
        http_status=resp.status,
        content_type=resp.headers.get("content-type", ""),
        cached_filename=path.name,
        byte_count=path.stat().st_size,
        sha256=saved_sha,
        declared_user_agent=True,
    )
    return manifest.model_copy(
        update={
            "status": "downloaded",
            "status_detail": "Downloaded and checksummed from the saved bytes.",
            "retrieval": retrieval,
            "external_import": None,
        }
    )


def import_external_file(
    manifest: SourceManifest,
    root: Path,
    source: Path,
    *,
    acquisition_method: str,
    claimed_source_url: str | None = None,
    operator_reported_acquired_at: str | None = None,
    expected_sha256: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    min_bytes: int = DEFAULT_MIN_BYTES,
) -> SourceManifest:
    """Offline path: validate and cache a filing that was downloaded outside this tool.

    Makes no network request. Records the operator's description of how the file was acquired and the
    SHA-256 of the bytes actually copied. Never fabricates an HTTP status, content type or download time.
    """
    if len(acquisition_method.strip()) < 10:
        raise ValueError("acquisition_method must describe how the file was actually obtained (at least 10 characters)")
    if not source.is_file():
        raise InvalidFiling(f"no such file: {source.name}")
    body = source.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and expected_sha256.strip().lower() != digest:
        raise CacheMismatch(f"operator-supplied sha256 {expected_sha256.strip().lower()} != computed {digest}")
    validate_filing_body(body, manifest.target, min_bytes)

    path = cache_path(root, manifest.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(body)
    os.replace(tmp, path)
    saved_sha = sha256_file(path)
    if saved_sha != digest:
        raise CacheMismatch("saved file differs from source bytes")

    record = ExternalImport(
        acquisition_method=acquisition_method,
        claimed_source_url=claimed_source_url,
        operator_reported_acquired_at=operator_reported_acquired_at,
        original_filename=source.name,
        imported_at_utc=now(),
        cached_filename=path.name,
        byte_count=path.stat().st_size,
        sha256=saved_sha,
        operator_supplied_sha256_verified=expected_sha256 is not None,
    )
    return manifest.model_copy(
        update={
            "status": "imported_offline",
            "status_detail": "Imported from a locally supplied file; no HTTP response or download time was observed by this tool.",
            "retrieval": None,
            "external_import": record,
        }
    )
