from types import SimpleNamespace

import pytest

from smartrouter.core.deps import client_ip


def _request(hops: int, xff: str | None, peer: str = "10.0.0.5") -> SimpleNamespace:
    settings = SimpleNamespace(trusted_proxy_hops=hops)
    app = SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(settings=settings)))
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(app=app, headers=headers, client=SimpleNamespace(host=peer))


def test_without_trusted_proxies_the_peer_is_the_client_and_the_header_is_ignored() -> None:
    assert client_ip(_request(0, "6.6.6.6")) == "10.0.0.5"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("hops", "xff", "expected"),
    [
        (1, "203.0.113.9", "203.0.113.9"),
        # A forged leftmost entry is ignored: one trusted proxy appended the real address.
        (1, "6.6.6.6, 203.0.113.9", "203.0.113.9"),
        (2, "6.6.6.6, 203.0.113.9, 10.0.1.4", "203.0.113.9"),
        (2, "203.0.113.9", "203.0.113.9"),
        (1, None, "10.0.0.5"),
    ],
)
def test_trusted_hops_read_from_the_right(hops: int, xff: str | None, expected: str) -> None:
    assert client_ip(_request(hops, xff)) == expected  # type: ignore[arg-type]
