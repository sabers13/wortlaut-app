"""Trusted Product HTTP transport tests for the Online dictionary.

These tests prove the Slice 11 Product transport trust policy:
HTTPS only; userinfo rejected; unexpected ports rejected; arbitrary
hosts rejected; approved GitHub Release redirect destinations accepted;
plain HTTP rejected; userinfo redirect rejected; redirect loop
rejected; non-2xx response rejected; network failure rejected; no
caller/browser Product source parameter.

The transport policy is the unit under test. An injectable low-level
opener seam drives every redirect case so the tests never reach the
public GitHub network.
"""

from __future__ import annotations

import inspect
import io
import socket
import ssl
import urllib.error
import urllib.request
from email.message import Message
from typing import Any
from urllib.response import addinfourl

import pytest

from app.online_manifest import (
    DEFAULT_DATASET_TOKEN,
    MANIFEST_SCHEMA_VERSION,
    SHARD_FAMILY_ENTRY,
    ManifestAsset,
    OnlineManifest,
    TrustedDistribution,
)
from app.online_transport import (
    DEFAULT_GITHUB_REPO,
    GitHubReleaseProductTransport,
    NoHTTPHandler,
    _ApprovedRedirectHandler,
    _build_default_opener,
    build_seam_transport,
    create_product_online_provider,
    create_product_shard_cache,
)
from app.provider import ProviderIntegrityError, ProviderNetworkError


def _build_manifest() -> OnlineManifest:
    entry_asset = ManifestAsset(
        family=SHARD_FAMILY_ENTRY,
        bucket=0,
        name="entry-000.sqlite",
        path="shards/entry/000.sqlite",
        byte_size=5,
        sha256="a" * 64,
        schema_version="entry-shard-v1",
    )
    filter_asset = ManifestAsset(
        family="membership_filter",
        bucket=0,
        name="membership-filter.bin",
        path="shards/membership-filter.bin",
        byte_size=len(b"placeholder-filter-bytes"),
        sha256="b" * 64,
        schema_version="membership-filter-v1",
    )
    return OnlineManifest(
        dataset_token=DEFAULT_DATASET_TOKEN,
        schema_version=MANIFEST_SCHEMA_VERSION,
        distribution=TrustedDistribution(
            base_origin="https://github.com",
            release_tag="dictionary-online-v2",
            redirect_policy="github_release_redirect_only",
        ),
        assets=(entry_asset, filter_asset),
    )


def _build_request(manifest: OnlineManifest) -> Any:
    from app.online_cache import ShardIdentity, ShardRequest

    asset = next(iter(manifest.assets))
    return ShardRequest(
        identity=ShardIdentity(family=asset.family, bucket=asset.bucket),
        asset=asset,
    )


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None


class _SeamOpener:
    """A test-only opener that drives the transport policy deterministically."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[str] = []

    def open(self, request: Any, timeout: float = 30.0) -> Any:  # noqa: ARG002
        self.calls.append(request.full_url)
        if not self._script:
            raise RuntimeError("seam opener: no scripted response")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(request)
        return item


def _http_error_redirect(code: int, location: str) -> urllib.error.HTTPError:
    from email.message import Message

    hdrs: Message = Message()
    hdrs["Location"] = location
    return urllib.error.HTTPError(
        url="https://github.com/sabers13/wortlaut-app/releases/download/dictionary-online-v2/entry-000.sqlite",
        code=code,
        msg="redirect",
        hdrs=hdrs,
        fp=None,
    )


def _transport_for(manifest: OnlineManifest, opener: Any) -> GitHubReleaseProductTransport:
    return GitHubReleaseProductTransport(
        distribution=manifest.distribution,
        opener=opener,
        max_redirects=3,
    )


def test_initial_url_is_exact_github_release_form() -> None:
    """The initial request must resolve to the approved GitHub Release URL."""
    manifest = _build_manifest()
    opener = _SeamOpener([_FakeResponse(200, {}, b"hello")])
    transport = _transport_for(manifest, opener)
    payload = transport(_build_request(manifest))
    assert payload == b"hello"
    assert len(opener.calls) == 1
    assert (
        opener.calls[0]
        == "https://github.com/sabers13/wortlaut-app/releases/download/dictionary-online-v2/entry-000.sqlite"
    )


def test_approved_release_redirect_is_accepted() -> None:
    """``objects.githubusercontent.com`` redirects resolve to the asset."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://objects.githubusercontent.com/asset/abc",
            ),
            _FakeResponse(200, {}, b"payload-bytes"),
        ]
    )
    transport = _transport_for(manifest, opener)
    payload = transport(_build_request(manifest))
    assert payload == b"payload-bytes"
    assert len(opener.calls) == 2


def test_arbitrary_host_redirect_is_rejected() -> None:
    """An arbitrary non-GitHub host is rejected before follow-through."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://attacker.example.com/asset",
            ),
        ]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="not on the approved distribution"):
        transport(_build_request(manifest))


def test_plain_http_redirect_is_rejected() -> None:
    """A plain HTTP redirect target is rejected before follow-through."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "http://github.com/sabers13/wortlaut-app/releases/download/dictionary-online-v2/entry-000.sqlite",
            ),
        ]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="must use HTTPS"):
        transport(_build_request(manifest))


def test_userinfo_redirect_is_rejected() -> None:
    """A redirect with userinfo is rejected."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://user:pass@github.com/asset",
            ),
        ]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="userinfo"):
        transport(_build_request(manifest))


def test_unexpected_port_redirect_is_rejected() -> None:
    """A redirect with an unexpected port is rejected."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://github.com:8443/asset",
            ),
        ]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="port"):
        transport(_build_request(manifest))


def test_redirect_loop_is_rejected() -> None:
    """A redirect loop is rejected when the redirect limit is exhausted."""
    manifest = _build_manifest()
    redirect = _http_error_redirect(
        302, "https://objects.githubusercontent.com/asset/loop"
    )
    opener = _SeamOpener([redirect, redirect, redirect, redirect])
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="redirect limit"):
        transport(_build_request(manifest))


def test_non_2xx_response_is_rejected() -> None:
    """A non-2xx final response is rejected as a network failure."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(302, "https://objects.githubusercontent.com/asset/x"),
            _FakeResponse(500, {}, b""),
        ]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="unexpected status"):
        transport(_build_request(manifest))


def test_connection_failure_is_a_network_error() -> None:
    """A socket failure becomes a structured network error."""
    manifest = _build_manifest()
    opener = _SeamOpener([socket.error("dns failure")])
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="network failure"):
        transport(_build_request(manifest))


def test_ssl_failure_is_a_network_error() -> None:
    """An SSL failure becomes a structured network error."""
    manifest = _build_manifest()
    opener = _SeamOpener([ssl.SSLError("cert verify failed")])
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="network failure"):
        transport(_build_request(manifest))


def test_url_error_is_a_network_error() -> None:
    """A urllib URL error becomes a structured network error."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [urllib.error.URLError("connection refused")]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="URL error"):
        transport(_build_request(manifest))


def test_successful_payload_is_returned() -> None:
    """A successful 200 response yields the body bytes."""
    manifest = _build_manifest()
    opener = _SeamOpener([_FakeResponse(200, {}, b"\x00\x01\x02payload")])
    transport = _transport_for(manifest, opener)
    payload = transport(_build_request(manifest))
    assert payload == b"\x00\x01\x02payload"


def test_caller_cannot_supply_arbitrary_product_source() -> None:
    """The transport must not expose host/URL/manifest knobs to the caller."""
    manifest = _build_manifest()
    opener = _SeamOpener([_FakeResponse(200, {}, b"x")])
    transport = _transport_for(manifest, opener)
    forbidden_args = ("host", "url", "manifest_url", "source")
    for arg in forbidden_args:
        assert not hasattr(transport, arg), (
            f"transport must not expose {arg!r}"
        )


def test_create_product_shard_cache_uses_trusted_transport(tmp_path: Any) -> None:
    """``create_product_shard_cache`` wires the trusted Product transport."""
    from app.online_cache import ShardCache

    manifest = _build_manifest()
    cache = create_product_shard_cache(manifest, tmp_path / "cache")
    assert isinstance(cache, ShardCache)


def test_create_product_online_provider_uses_trusted_transport(tmp_path: Any) -> None:
    """``create_product_online_provider`` returns a working provider."""
    from app.online_filter import BloomFilter
    from app.provider_online import OnlineDictionaryProvider

    filter_payload = BloomFilter.from_closure_keys(["Haus", "See"]).to_bytes()
    manifest = _build_manifest_with_filter(filter_payload)
    opener = _SeamOpener(
        [
            _FakeResponse(200, {}, filter_payload),
        ]
    )
    provider = create_product_online_provider(
        manifest, tmp_path / "cache", opener=opener
    )
    assert isinstance(provider, OnlineDictionaryProvider)
    assert provider.asset_token == DEFAULT_DATASET_TOKEN


def _build_manifest_with_filter(filter_payload: bytes) -> OnlineManifest:
    """Return a manifest whose filter asset matches ``filter_payload``."""
    from hashlib import sha256 as _sha256

    entry_asset = ManifestAsset(
        family=SHARD_FAMILY_ENTRY,
        bucket=0,
        name="entry-000.sqlite",
        path="shards/entry/000.sqlite",
        byte_size=5,
        sha256="a" * 64,
        schema_version="entry-shard-v1",
    )
    filter_asset = ManifestAsset(
        family="membership_filter",
        bucket=0,
        name="membership-filter.bin",
        path="shards/membership-filter.bin",
        byte_size=len(filter_payload),
        sha256=_sha256(filter_payload).hexdigest(),
        schema_version="membership-filter-v1",
    )
    return OnlineManifest(
        dataset_token=DEFAULT_DATASET_TOKEN,
        schema_version=MANIFEST_SCHEMA_VERSION,
        distribution=TrustedDistribution(
            base_origin="https://github.com",
            release_tag="dictionary-online-v2",
            redirect_policy="github_release_redirect_only",
        ),
        assets=(entry_asset, filter_asset),
    )


# ---------------------------------------------------------------------------
# C1 — production opener redirect handling
# ---------------------------------------------------------------------------


class _ScriptedHTTPSHandler(urllib.request.HTTPSHandler):
    """Scripted stand-in for the real HTTPS protocol handler.

    Plugged beneath the SAME opener stack production uses
    (:func:`_build_default_opener`), so redirect responses traverse the
    real ``_ApprovedRedirectHandler`` and the manual validation path.
    Script items are ``(status, headers, body)`` tuples or exceptions.
    """

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self._script = list(script)
        self.calls: list[str] = []

    def https_open(self, req: Any) -> Any:
        self.calls.append(req.get_full_url())
        if not self._script:
            raise RuntimeError("scripted https: no scripted response")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, headers, body = item
        response_headers = Message()
        for key, value in dict(headers).items():
            response_headers[key] = value
        if status in (301, 302, 303, 307, 308):
            # Mimic the real HTTPS handler: route the 3xx through
            # ``parent.error`` so the production redirect handler sees it.
            return self.parent.error(
                "http", req, io.BytesIO(body), status, "redirect", response_headers
            )
        response = addinfourl(
            io.BytesIO(body), response_headers, req.get_full_url(), code=status
        )
        # The real handler copies ``msg`` onto the wrapper; the opener's
        # response processors require it.
        setattr(response, "msg", "OK")
        return response


def _production_opener_with_scripted_https(
    script: list[Any],
) -> tuple[Any, _ScriptedHTTPSHandler]:
    """Return ``(_build_default_opener(), fake)`` with HTTPS scripted.

    Only the HTTPS protocol handler is replaced; the production
    ``NoHTTPHandler`` and ``_ApprovedRedirectHandler`` stay in place.
    """
    opener = _build_default_opener()
    fake = _ScriptedHTTPSHandler(script)
    fake.add_parent(opener)
    opener.handle_open["https"] = [fake]
    return opener, fake


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_production_redirect_handler_surfaces_http_error(code: int) -> None:
    """The production redirect handler must re-raise the 3xx as HTTPError.

    Raising ``ProviderNetworkError`` here would reject an ordinary GitHub
    Release 302 before the manual redirect validator sees the Location
    target. Re-raising HTTPError routes it into the same manual
    validation state machine the injected test path exercises.
    """
    handler = _ApprovedRedirectHandler()
    req = urllib.request.Request(
        "https://github.com/sabers13/wortlaut-app/releases/download/t/a"
    )
    hdrs = Message()
    hdrs["Location"] = "https://objects.githubusercontent.com/asset/1"
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        getattr(handler, f"http_error_{code}")(
            req, io.BytesIO(b""), code, "redirect", hdrs
        )
    assert excinfo.value.code == code
    assert (
        excinfo.value.headers.get("Location")
        == "https://objects.githubusercontent.com/asset/1"
    )


def test_default_opener_uses_approved_handlers() -> None:
    """The production opener pins redirect handling to the approved handler."""
    opener = _build_default_opener()
    kinds = [type(handler) for handler in opener.handlers]
    assert _ApprovedRedirectHandler in kinds
    assert NoHTTPHandler in kinds
    # No stock handler may shadow the pinned ones: every redirect/HTTP
    # handler in the stack must be our subclass.
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.HTTPRedirectHandler):
            assert isinstance(handler, _ApprovedRedirectHandler)
        if type(handler) is urllib.request.HTTPHandler:
            raise AssertionError("stock HTTPHandler must not shadow NoHTTPHandler")


def test_production_opener_redirect_traverses_manual_validation() -> None:
    """A simulated GitHub 302 through the production stack resolves manually.

    ``github.com -> 302 -> release-assets.githubusercontent.com -> 200``
    traverses the actual production redirect code and yields the final
    bytes only after manual target validation.
    """
    manifest = _build_manifest()
    opener, fake = _production_opener_with_scripted_https(
        [
            (
                302,
                {"Location": "https://release-assets.githubusercontent.com/asset/1"},
                b"",
            ),
            (200, {}, b"payload-bytes"),
        ]
    )
    transport = GitHubReleaseProductTransport(
        distribution=manifest.distribution, opener=opener
    )
    payload = transport(_build_request(manifest))
    assert payload == b"payload-bytes"
    assert fake.calls == [
        "https://github.com/sabers13/wortlaut-app/releases/download/"
        "dictionary-online-v2/entry-000.sqlite",
        "https://release-assets.githubusercontent.com/asset/1",
    ]


def test_production_opener_rejects_attacker_redirect_without_follow() -> None:
    """An attacker redirect through the production stack is never followed."""
    manifest = _build_manifest()
    opener, fake = _production_opener_with_scripted_https(
        [(302, {"Location": "https://attacker.example.com/x"}, b"")]
    )
    transport = GitHubReleaseProductTransport(
        distribution=manifest.distribution, opener=opener
    )
    with pytest.raises(ProviderNetworkError, match="not on the approved distribution"):
        transport(_build_request(manifest))
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# C2 — approved GitHub Release CDN host set
# ---------------------------------------------------------------------------


def test_release_assets_cdn_host_is_accepted() -> None:
    """``release-assets.githubusercontent.com`` redirects resolve."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://release-assets.githubusercontent.com/asset/abc",
            ),
            _FakeResponse(200, {}, b"payload-bytes"),
        ]
    )
    transport = _transport_for(manifest, opener)
    assert transport(_build_request(manifest)) == b"payload-bytes"


def test_objects_cdn_host_is_accepted() -> None:
    """``objects.githubusercontent.com`` redirects resolve."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [
            _http_error_redirect(
                302,
                "https://objects.githubusercontent.com/asset/abc",
            ),
            _FakeResponse(200, {}, b"payload-bytes"),
        ]
    )
    transport = _transport_for(manifest, opener)
    assert transport(_build_request(manifest)) == b"payload-bytes"


def test_bare_githubusercontent_host_is_rejected() -> None:
    """The generic ``githubusercontent.com`` host is not approved."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [_http_error_redirect(302, "https://githubusercontent.com/asset")]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="not on the approved distribution"):
        transport(_build_request(manifest))


def test_arbitrary_githubusercontent_subdomain_is_rejected() -> None:
    """No wildcard rule: only the exact approved hosts are accepted."""
    manifest = _build_manifest()
    opener = _SeamOpener(
        [_http_error_redirect(302, "https://evil.githubusercontent.com/asset")]
    )
    transport = _transport_for(manifest, opener)
    with pytest.raises(ProviderNetworkError, match="not on the approved distribution"):
        transport(_build_request(manifest))


# ---------------------------------------------------------------------------
# C3 — fixed Product repository identity
# ---------------------------------------------------------------------------


def test_product_repository_identity_is_fixed() -> None:
    """No public Product constructor can substitute another repository."""
    import dataclasses

    field_names = {
        field.name for field in dataclasses.fields(GitHubReleaseProductTransport)
    }
    assert "github_repo" not in field_names
    for factory in (
        build_seam_transport,
        create_product_shard_cache,
        create_product_online_provider,
    ):
        assert "github_repo" not in inspect.signature(factory).parameters, (
            f"{factory.__name__} must not accept github_repo"
        )
    manifest = _build_manifest()
    opener = _SeamOpener([_FakeResponse(200, {}, b"hello")])
    transport = _transport_for(manifest, opener)
    transport(_build_request(manifest))
    assert opener.calls[0] == (
        f"https://github.com/{DEFAULT_GITHUB_REPO}/releases/download/"
        "dictionary-online-v2/entry-000.sqlite"
    )
    assert DEFAULT_GITHUB_REPO == "sabers13/wortlaut-app"


def test_caller_cannot_configure_product_repository(tmp_path: Any) -> None:
    """``github_repo`` is not an accepted knob on any Product constructor."""
    manifest = _build_manifest()
    opener = _SeamOpener([_FakeResponse(200, {}, b"x")])
    transport = _transport_for(manifest, opener)
    assert not hasattr(transport, "github_repo")
    with pytest.raises(TypeError):
        create_product_shard_cache(  # type: ignore[call-arg]
            manifest, tmp_path / "nowhere", github_repo="attacker/example"
        )
    with pytest.raises(TypeError):
        create_product_online_provider(  # type: ignore[call-arg]
            manifest, tmp_path / "nowhere", github_repo="attacker/example"
        )


# ---------------------------------------------------------------------------
# C4 — membership filter verified before use
# ---------------------------------------------------------------------------


def test_product_filter_wrong_sha_is_rejected(tmp_path: Any) -> None:
    """A filter payload with a wrong manifest SHA is an integrity failure."""
    from app.online_filter import BloomFilter

    filter_payload = BloomFilter.from_closure_keys(["Haus", "See"]).to_bytes()
    manifest = _build_manifest_with_filter(filter_payload)
    # Corrupt one byte in transit: size still matches, SHA does not.
    tampered = bytearray(filter_payload)
    tampered[-1] ^= 0xFF
    opener = _SeamOpener([_FakeResponse(200, {}, bytes(tampered))])
    with pytest.raises(ProviderIntegrityError, match="SHA-256"):
        create_product_online_provider(manifest, tmp_path / "cache", opener=opener)


def test_product_filter_wrong_size_is_rejected(tmp_path: Any) -> None:
    """A filter payload with a wrong manifest byte count is rejected."""
    from hashlib import sha256 as _sha256

    from app.online_filter import BloomFilter

    filter_payload = BloomFilter.from_closure_keys(["Haus", "See"]).to_bytes()
    manifest = _build_manifest_with_filter(filter_payload)
    truncated = filter_payload[:-1]
    # Rebuild the manifest so the SHA matches the truncated bytes but the
    # committed size does not — the size check must still fail first.
    assets = list(manifest.assets)
    wrong_size = ManifestAsset(
        family="membership_filter",
        bucket=0,
        name="membership-filter.bin",
        path="shards/membership-filter.bin",
        byte_size=len(filter_payload),
        sha256=_sha256(truncated).hexdigest(),
        schema_version="membership-filter-v1",
    )
    manifest = OnlineManifest(
        dataset_token=manifest.dataset_token,
        schema_version=manifest.schema_version,
        distribution=manifest.distribution,
        assets=(assets[0], wrong_size),
    )
    opener = _SeamOpener([_FakeResponse(200, {}, truncated)])
    with pytest.raises(ProviderIntegrityError, match="byte size"):
        create_product_online_provider(manifest, tmp_path / "cache", opener=opener)


def test_product_filter_malformed_bloom_is_rejected(tmp_path: Any) -> None:
    """Malformed Bloom bytes with a matching digest are still rejected."""
    garbage = b"\x00" * 64
    manifest = _build_manifest_with_filter(garbage)
    opener = _SeamOpener([_FakeResponse(200, {}, garbage)])
    with pytest.raises(ProviderIntegrityError, match="Bloom"):
        create_product_online_provider(manifest, tmp_path / "cache", opener=opener)
