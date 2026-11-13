import json
from datetime import datetime, timezone

import pytest

from finreport.ingest import (AccessDenied, FetchFailed, HttpResponse, InvalidFiling, MissingUserAgent, RateLimiter,
                              cache_is_valid, cache_path, fetch_filing, sha256_file)
from finreport.manifest import SourceManifest, initial_manifest
from helpers import TARGET

UA = {"SEC_USER_AGENT": "unit-test-agent (placeholder contact)"}
BODY = b"<html><body>Examplecorp FORM 10-K fiscal year ended June 30, 2024 " + b"x" * 500 + b"</body></html>"
URL = initial_manifest().target.url


def manifest() -> SourceManifest:
    m = initial_manifest()
    # point the real manifest's target at our fictional body markers
    return m.model_copy(update={"target": m.target.model_copy(update={"company": "Examplecorp"})})


def ok(body=BODY, **kw):
    base = dict(status=200, headers={"content-type": "text/html; charset=utf-8"}, body=body, final_url=URL)
    return HttpResponse(**{**base, **kw})


class Fake:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def fetch(tmp_path, fake, env=UA, clock=None, **kw):
    clock = clock or Clock()
    return fetch_filing(manifest(), tmp_path, env=env, transport=fake, limiter=RateLimiter(1.0, clock.now, clock.sleep),
                        sleep=clock.sleep, now=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), min_bytes=100, **kw)


def test_missing_user_agent_stops_before_any_request(tmp_path):
    fake = Fake(ok())
    with pytest.raises(MissingUserAgent):
        fetch(tmp_path, fake, env={})
    assert fake.calls == [] and not list(tmp_path.rglob("*.htm"))


def test_blank_user_agent_counts_as_missing(tmp_path):
    with pytest.raises(MissingUserAgent):
        fetch(tmp_path, Fake(ok()), env={"SEC_USER_AGENT": "   "})


def test_success_records_checksum_of_saved_bytes(tmp_path):
    fake = Fake(ok())
    m = fetch(tmp_path, fake)
    r = m.retrieval
    saved = cache_path(tmp_path, m.target)
    assert saved.read_bytes() == BODY
    assert r.sha256 == sha256_file(saved) and r.byte_count == len(BODY) == saved.stat().st_size
    assert (r.requested_url, r.final_url, r.http_status, r.cached_filename) == (URL, URL, 200, "msft-20240630.htm")
    assert r.retrieved_at_utc.isoformat() == "2026-01-02T03:04:05+00:00" and r.content_type.startswith("text/html")
    assert m.status == "downloaded" and fake.calls[0][1]["User-Agent"] == UA["SEC_USER_AGENT"]


def test_user_agent_value_is_never_written_to_the_manifest(tmp_path):
    m = fetch(tmp_path, Fake(ok()))
    assert UA["SEC_USER_AGENT"] not in m.model_dump_json() and m.retrieval.declared_user_agent is True


def test_valid_cache_means_zero_requests_and_no_user_agent_needed(tmp_path):
    m = fetch(tmp_path, Fake(ok()))
    again = fetch_filing(m, tmp_path, env={}, transport=Fake())
    assert again == m


def test_tampered_cache_is_not_trusted(tmp_path):
    m = fetch(tmp_path, Fake(ok()))
    cache_path(tmp_path, m.target).write_bytes(BODY + b"tamper")
    assert not cache_is_valid(tmp_path, m)
    with pytest.raises(MissingUserAgent):
        fetch_filing(m, tmp_path, env={}, transport=Fake())


def test_requests_are_serial_and_spaced_by_at_least_one_second(tmp_path):
    clock = Clock()
    fetch(tmp_path, Fake(ok(status=503, body=b""), ok(status=503, body=b""), ok()), clock=clock)
    # spacing waits come from the limiter; retry back-off adds more
    assert all(s >= 1.0 for s in clock.sleeps) and len(clock.sleeps) >= 2


def test_limiter_enforces_minimum_interval():
    clock = Clock()
    lim = RateLimiter(1.0, clock.now, clock.sleep)
    lim.wait()
    clock.t += 0.25
    lim.wait()
    assert clock.sleeps == [pytest.approx(0.75)]


def test_transient_errors_retry_then_succeed(tmp_path):
    fake = Fake(ok(status=503, body=b""), OSError("reset"), ok())
    fetch(tmp_path, fake)
    assert len(fake.calls) == 3


def test_retries_are_bounded(tmp_path):
    fake = Fake(*[ok(status=500, body=b"") for _ in range(10)])
    with pytest.raises(FetchFailed):
        fetch(tmp_path, fake)
    assert len(fake.calls) == 3 and not list(tmp_path.rglob("*.htm"))


@pytest.mark.parametrize("status", [403, 429])
def test_access_denial_stops_immediately_without_retry(tmp_path, status):
    fake = Fake(ok(status=status, body=b"denied"), ok())
    with pytest.raises(AccessDenied):
        fetch(tmp_path, fake)
    assert len(fake.calls) == 1


def test_not_found_is_not_retried(tmp_path):
    fake = Fake(ok(status=404, body=b""), ok())
    with pytest.raises(InvalidFiling):
        fetch(tmp_path, fake)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("response", [
    ok(body=b"<html>Your Request Originated from an Undeclared Automated Tool " + b"x" * 500 + b"</html>"),
    ok(headers={"content-type": "application/json"}),
    ok(body=b"<html>short</html>"),
    ok(final_url="https://www.sec.gov/cgi-bin/error"),
    ok(body=b"<html>Othercorp 10-K 2024 " + b"x" * 500 + b"</html>"),
    ok(body=b"plain text, no markup but long enough " + b"x" * 500),
])
def test_error_pages_and_wrong_documents_are_rejected_and_not_cached(tmp_path, response):
    with pytest.raises(InvalidFiling):
        fetch(tmp_path, Fake(response))
    assert not list(tmp_path.rglob("*.htm")) and not list(tmp_path.rglob("*.part"))


def test_http_urls_are_refused(tmp_path):
    m = manifest()
    m = m.model_copy(update={"target": m.target.model_copy(update={"url": URL.replace("https", "http")})})
    with pytest.raises(InvalidFiling):
        fetch_filing(m, tmp_path, env=UA, transport=Fake(ok()))


def test_error_page_signature_alone_is_enough_to_reject(tmp_path):
    """Body passes size, content-type, URL and marker checks; only the SEC error signature is wrong."""
    body = (b"<html><body>Request Rate Threshold Exceeded. Examplecorp 10-K fiscal year ended June 30, 2024 "
            + b"x" * 500 + b"</body></html>")
    with pytest.raises(InvalidFiling, match="error page"):
        fetch(tmp_path, Fake(ok(body=body)))
    assert not list(tmp_path.rglob("*.htm"))
