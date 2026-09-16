"""Trusted Product HTTP transport for the Online dictionary corpus.

ADR-0009 requires the Slice 11 provider to retrieve shards through a
single Product-side trust boundary:

* HTTPS only, no HTTP;
* no caller/browser-supplied URL or manifest;
* the trusted distribution is fixed at construction to the committed
   Wortlaut GitHub Release contract:
   ``https://github.com/sabers13/wortlaut-app/releases/download/{release_tag}/{asset_name}``
  with a per-asset initial request that resolves only to the approved
  GitHub Release download form;
* every redirect is validated before being followed, with an explicit
  small redirect limit. Plain HTTP, userinfo, arbitrary hosts,
  unexpected ports, malformed targets, and redirect loops are all
  rejected.
* a network failure is a structured :class:`ProviderNetworkError`,
  never a dictionary miss.

The transport is implemented in terms of stdlib
:mod:`urllib.request` only. Slice 11 does not add a new runtime
dependency. The low-level opener is injectable so tests can simulate
the initial GitHub request, valid approved redirects, arbitrary
redirects, plain HTTP redirects, userinfo redirects, redirect loops,
non-2xx responses, connection failures, and a successful byte payload
without ever reaching the public GitHub network.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.online_manifest import TrustedDistribution
from app.provider import ProviderIntegrityError, ProviderNetworkError

DEFAULT_GITHUB_REPO: str = "sabers13/wortlaut-app"
DEFAULT_MAX_REDIRECTS: int = 3
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# Narrow explicit GitHub Release redirect host set. GitHub Release
# downloads resolve from ``github.com`` to GitHub-controlled CDN hosts;
# only these exact hosts are accepted. No wildcard rule: an arbitrary
# ``*.githubusercontent.com`` name is rejected unless it is one of the
# exact approved hosts below.
_APPROVED_REDIRECT_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


@dataclass(frozen=True, slots=True)
class _ApprovedUrl:
    """An HTTPS URL the transport has explicitly approved for this request."""

    url: str
    host: str
    port: int
    netloc: str


def _check_no_userinfo(parsed: Any) -> None:
    if getattr(parsed, "username", None) or getattr(parsed, "password", None):
        raise ProviderNetworkError("redirect target must not contain userinfo")


def _check_scheme_is_https(parsed: Any) -> None:
    if parsed.scheme != "https":
        raise ProviderNetworkError(
            f"redirect target must use HTTPS, got scheme={parsed.scheme!r}"
        )


def _check_port_is_default(parsed: Any, host: str) -> None:
    """Reject unexpected ports; allow only the implicit 443 for HTTPS."""
    explicit_port = parsed.port
    if explicit_port is None:
        return
    if explicit_port != 443:
        raise ProviderNetworkError(
            f"redirect target host {host!r} has unexpected port {explicit_port}"
        )


def _check_approved_host(parsed: Any) -> str:
    host = (parsed.hostname or "").lower()
    if not host:
        raise ProviderNetworkError("redirect target has empty host")
    if host not in _APPROVED_REDIRECT_HOSTS:
        raise ProviderNetworkError(
            f"redirect target host {host!r} is not on the approved distribution"
        )
    return host


def _validate_url(url: str) -> _ApprovedUrl:
    """Validate ``url`` against the approved distribution contract."""
    parsed = urlsplit(url)
    _check_scheme_is_https(parsed)
    _check_no_userinfo(parsed)
    host = _check_approved_host(parsed)
    _check_port_is_default(parsed, host)
    return _ApprovedUrl(
        url=url,
        host=host,
        port=parsed.port or 443,
        netloc=parsed.netloc,
    )


def _initial_distribution_url(
    distribution: TrustedDistribution,
    asset_name: str,
) -> str:
    """Build the trusted GitHub Release download URL for one asset.

    The URL is derived solely from the committed manifest and the fixed
    Wortlaut repository identity (``sabers13/wortlaut-app``). The asset name
    comes from the manifest entry; the caller cannot supply a URL, host,
    or repository.
    """
    if not isinstance(asset_name, str) or not asset_name:
        raise ProviderNetworkError("asset name must be a non-empty string")
    if "/" in asset_name or ".." in asset_name or "\\" in asset_name:
        raise ProviderNetworkError(f"asset name escapes distribution: {asset_name!r}")
    base = (distribution.base_origin or "").rstrip("/")
    if base != "https://github.com":
        raise ProviderNetworkError(
            f"unsupported base_origin for trusted distribution: {base!r}"
        )
    tag = distribution.release_tag
    if not tag or "/" in tag or ".." in tag:
        raise ProviderNetworkError(f"invalid release_tag: {tag!r}")
    url = f"https://github.com/{DEFAULT_GITHUB_REPO}/releases/download/{tag}/{asset_name}"
    return url


@dataclass(frozen=True, slots=True)
class GitHubReleaseProductTransport:
    """Trusted Product HTTP transport for the Wortlaut GitHub Release.

    The repository identity is fixed to ``sabers13/wortlaut-app`` internally;
    it is not a configurable field, so no caller/browser/API can
    substitute another repository.
    """

    distribution: TrustedDistribution
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    opener: Any | None = None

    def __call__(self, request: Any) -> bytes:
        return self.fetch(request)

    def fetch(self, request: Any) -> bytes:
        """Download the bytes for ``request`` under the trust policy."""
        asset = getattr(request, "asset", None)
        if asset is None or not hasattr(asset, "name"):
            raise ProviderNetworkError("shard request missing asset name")
        asset_name = str(asset.name)
        url = _initial_distribution_url(self.distribution, asset_name)
        try:
            return self._fetch_with_redirects(url)
        except ProviderNetworkError:
            raise
        except (urllib.error.URLError, socket.error, TimeoutError, ssl.SSLError) as exc:
            raise ProviderNetworkError(
                f"transport network failure for {asset_name}: {exc}"
            ) from exc
        except OSError as exc:
            raise ProviderNetworkError(
                f"transport I/O failure for {asset_name}: {exc}"
            ) from exc

    def _fetch_with_redirects(self, url: str) -> bytes:
        url_obj = _validate_url(url)
        opener = self.opener
        if opener is None:
            opener = _build_default_opener()
        result: bytes = self._fetch_recursive(
            opener, url_obj, redirects_left=self.max_redirects
        )
        return result

    def _fetch_recursive(
        self,
        opener: Any,
        approved: _ApprovedUrl,
        *,
        redirects_left: int,
    ) -> bytes:
        req = urllib.request.Request(
            approved.url,
            headers={"User-Agent": "wortlaut-online-dictionary/1"},
        )
        try:
            response: Any = opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                if redirects_left <= 0:
                    raise ProviderNetworkError(
                        "redirect limit exceeded before resolving an approved target"
                    )
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise ProviderNetworkError(
                        "redirect response missing Location header"
                    )
                next_url = urljoin(approved.url, location)
                next_approved = _validate_url(next_url)
                return self._fetch_recursive(
                    opener,
                    next_approved,
                    redirects_left=redirects_left - 1,
                )
            raise ProviderNetworkError(
                f"transport HTTP failure {exc.code} for {approved.url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderNetworkError(
                f"transport URL error for {approved.url}: {exc}"
            ) from exc
        try:
            try:
                status = getattr(response, "status", None)
                if status is not None and status not in (200, 206):
                    raise ProviderNetworkError(
                        f"transport unexpected status {status} for {approved.url}"
                    )
                payload: bytes = response.read()
                return payload
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except ProviderNetworkError:
            raise
        except (urllib.error.URLError, socket.error, TimeoutError) as exc:
            raise ProviderNetworkError(
                f"transport read failure for {approved.url}: {exc}"
            ) from exc


def _build_default_opener() -> Any:
    """Build the default stdlib HTTPS opener."""
    return urllib.request.build_opener(
        NoHTTPHandler(),
        _ApprovedRedirectHandler(),
    )


class NoHTTPHandler(urllib.request.HTTPHandler):
    """Reject plain HTTP requests at the opener level."""

    def http_open(self, req: Any) -> Any:  # pragma: no cover - never raised
        raise ProviderNetworkError("plain HTTP is not permitted by the Product transport")

    http_request = urllib.request.HTTPHandler.http_request


class _ApprovedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable automatic redirect following in the production opener.

    The handler re-raises the 3xx response as :class:`urllib.error.HTTPError`
    so that :meth:`GitHubReleaseProductTransport._fetch_recursive` receives
    it and runs the SAME manual redirect-validation state machine as the
    injected test path: extract ``Location``, validate the target against
    the approved distribution contract, and follow it manually within the
    explicit redirect limit. Automatic arbitrary following is disabled;
    validation is never bypassed.
    """

    def _reraise_as_http_error(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any
    ) -> Any:
        try:
            url = req.get_full_url()
        except Exception:
            url = ""
        if fp is not None:
            close = getattr(fp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        raise urllib.error.HTTPError(url, code, msg, headers, None)

    def http_error_301(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any) -> Any:  # noqa: ARG002
        return self._reraise_as_http_error(req, fp, code, msg, headers)

    def http_error_302(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any) -> Any:  # noqa: ARG002
        return self._reraise_as_http_error(req, fp, code, msg, headers)

    def http_error_303(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any) -> Any:  # noqa: ARG002
        return self._reraise_as_http_error(req, fp, code, msg, headers)

    def http_error_307(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any) -> Any:  # noqa: ARG002
        return self._reraise_as_http_error(req, fp, code, msg, headers)

    def http_error_308(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any) -> Any:  # noqa: ARG002
        return self._reraise_as_http_error(req, fp, code, msg, headers)


@dataclass(frozen=True, slots=True)
class _SeamOpener:
    """Test-only URL opener seam used to drive the trust policy.

    The Product transport policy is the unit under test; this seam lets
    tests inject canned responses without ever reaching the public
    GitHub network. The ``responses`` callable receives the request and
    returns either a ``(status, headers, body)`` tuple or raises an
    exception to simulate a network failure.
    """

    responses: Callable[[Any], "_SeamResponse | bytes"]

    def open(self, request: Any, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:  # noqa: ARG002
        result = self.responses(request)
        if isinstance(result, bytes):
            return _SeamResponse(200, {}, result)
        return _SeamResponse(result.status, result.headers, result.body)


@dataclass(frozen=True, slots=True)
class _SeamResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None


def build_seam_transport(
    distribution: TrustedDistribution,
    *,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    opener: Any | None = None,
) -> GitHubReleaseProductTransport:
    """Build a Product transport whose low-level opener is test-seam-only.

    The ``opener`` parameter is the Slice-12-ready seam described in
    Defect R5F: tests can inject a fake opener to exercise redirect
    policy and network failure paths. The production caller never
    passes an opener — the default stdlib HTTPS opener is used. The
    repository identity is always the fixed Product constant; it cannot
    be configured here.
    """
    return GitHubReleaseProductTransport(
        distribution=distribution,
        max_redirects=max_redirects,
        opener=opener,
    )


def create_product_shard_cache(
    manifest: Any,
    cache_dir: Any,
    *,
    opener: Any | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> "Any":
    """Construct a :class:`ShardCache` wired to the trusted Product transport.

    The Slice 11 / Slice 12 entry point. The browser/API caller never
    sees a URL, a manifest URL, a redirect target, or a repository
    identity — every shard download flows through the Product trust
    boundary fixed to ``sabers13/wortlaut-app``.
    """
    from app.online_cache import ShardCache

    transport = build_seam_transport(
        manifest.distribution,
        max_redirects=max_redirects,
        opener=opener,
    )
    return ShardCache(Path(cache_dir), transport=transport)


def create_product_online_provider(
    manifest: Any,
    cache_dir: Any,
    *,
    opener: Any | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> "Any":
    """Construct an :class:`OnlineDictionaryProvider` against the Product trust path.

    Downloads the membership filter through the trusted transport,
    verifies its exact manifest byte count and SHA-256 **before** the
    provider can consume it, then returns the provider wired against the
    same :class:`ShardCache`. Slice 12 does not need to know about
    redirect policy or the trust contract. The repository identity is
    fixed to ``sabers13/wortlaut-app`` and cannot be configured.
    """
    from hashlib import sha256 as _sha256

    from app.online_filter import BloomFilter
    from app.provider_online import OnlineDictionaryProvider

    cache = create_product_shard_cache(
        manifest,
        cache_dir,
        opener=opener,
        max_redirects=max_redirects,
    )
    filter_assets = list(manifest.filter_assets)
    if not filter_assets:
        raise ProviderIntegrityError("manifest declares no membership filter asset")
    filter_asset = filter_assets[0]
    filter_request = _filter_shard_request(manifest, filter_asset)
    transport = cache._transport
    filter_payload = transport(filter_request)
    # Verify the membership filter against the committed manifest BEFORE
    # the provider parses or uses it. Wrong size or digest is an
    # integrity failure, never a dictionary miss; malformed Bloom bytes
    # with a matching digest are still rejected by ``BloomFilter``.
    if len(filter_payload) != filter_asset.byte_size:
        raise ProviderIntegrityError(
            "membership filter byte size mismatch: expected "
            f"{filter_asset.byte_size} got {len(filter_payload)}"
        )
    if _sha256(filter_payload).hexdigest() != filter_asset.sha256:
        raise ProviderIntegrityError("membership filter SHA-256 mismatch")
    try:
        BloomFilter.from_bytes(filter_payload)
    except ValueError as exc:
        raise ProviderIntegrityError(
            f"membership filter payload is not a valid Bloom filter: {exc}"
        ) from exc
    return OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_payload,
        dataset_token=manifest.dataset_token,
    )


def _filter_shard_request(manifest: Any, filter_asset: Any) -> Any:
    """Build a ShardRequest for the membership filter asset."""
    from app.online_cache import ShardIdentity, ShardRequest

    return ShardRequest(
        identity=ShardIdentity(family=filter_asset.family, bucket=filter_asset.bucket),
        asset=filter_asset,
    )


__all__ = [
    "DEFAULT_GITHUB_REPO",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GitHubReleaseProductTransport",
    "build_seam_transport",
    "create_product_online_provider",
    "create_product_shard_cache",
]
