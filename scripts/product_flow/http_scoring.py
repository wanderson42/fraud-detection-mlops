"""Bounded loopback HTTP scoring; never load weights or fall back to local inference."""

import hashlib
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fraud_detection_mlops.serving.request_schema import ScoreResponse


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def checked_response(payload, request, manifest):
    response = ScoreResponse.model_validate(payload)
    expected = {
        "transaction_id": request.transaction_id,
        "model_id": manifest.model_id,
        "model_sha256": manifest.model_sha256,
        "serving_release_id": manifest.serving_release_id,
        "feature_contract_version": manifest.feature_contract_version,
        "score_semantics": manifest.score_semantics,
    }
    if any(getattr(response, name) != value for name, value in expected.items()):
        raise ValueError("HTTP response identifies another transaction, release or model")
    return response


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class HTTPScorer:
    """One immutable model identity, at most three attempts per score request."""

    def __init__(self, url, manifest, *, timeout=3.0, attempts=3):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not 0 < timeout <= 10
            or type(attempts) is not int
            or not 1 <= attempts <= 3
        ):
            raise ValueError("Use a scoped IPv4 loopback forward and bounded HTTP retries")
        self.url, self.manifest = url, manifest
        self.timeout, self.attempts = timeout, attempts
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.record = None
        self.requests = self.successful = 0

    def information(self):
        return {
            "transport": "HTTP_no_local_fallback_v1",
            "model_sha256": self.manifest.model_sha256,
            "serving_release_id": self.manifest.serving_release_id,
            "model_id": self.manifest.model_id,
        }

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise ValueError("Unexpected HTTP success status")
            data = response.read(1_048_577)
        if len(data) > 1_048_576:
            raise ValueError("HTTP response exceeds the response size bound")
        return json.loads(data)

    def ready(self):
        ready, info = self._request("/ready"), self._request("/info")
        if (
            not isinstance(ready, dict)
            or not isinstance(info, dict)
            or ready.get("status") != "ready"
            or ready.get("serving_release_id") != self.manifest.serving_release_id
            or any(
                info.get(key) != value
                for key, value in self.manifest.model_dump().items()
                if key
                in {
                    "model_id",
                    "model_sha256",
                    "serving_release_id",
                    "score_semantics",
                    "feature_columns",
                    "feature_contract_version",
                }
            )
        ):
            raise ValueError("HTTP readiness or information differs from the pinned release")

    def score(self, request):
        if self.record is None:
            raise ValueError("Attach the HTTP exchange to its durable transaction")
        start = time.monotonic()
        payload = request.model_dump()
        for attempt in range(1, self.attempts + 1):
            self.requests += 1
            try:
                response = checked_response(
                    self._request("/score", payload), request, self.manifest
                )
                break
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.attempts:
                    raise
            except URLError, TimeoutError, ConnectionError:
                if attempt == self.attempts:
                    raise
            time.sleep(0.1 * attempt)
        self.successful += 1
        self.record(
            request,
            response,
            {
                "attempts": attempt,
                "elapsed_ms": (time.monotonic() - start) * 1000,
                "request_sha256": digest(payload),
                "response_sha256": digest(response.model_dump()),
            },
        )
        return response
