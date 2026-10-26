import pytest

from smartrouter.providers.urls import EndpointUrlError, check_endpoint_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # AWS, GCP and Azure instance metadata
        "http://[fe80::1]/v1",
        "http://[fd00:ec2::254]/v1",  # AWS IPv6 metadata
        "http://0.0.0.0/v1",
        "http://224.0.0.1/v1",
    ],
)
async def test_metadata_link_local_and_reserved_addresses_are_always_refused(url: str) -> None:
    with pytest.raises(EndpointUrlError):
        await check_endpoint_url(url, allow_private=True)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:11434/v1", "http://10.0.3.7/v1", "http://192.168.1.20:8000/v1"]
)
async def test_private_endpoints_follow_the_setting(url: str) -> None:
    await check_endpoint_url(url, allow_private=True)  # local Ollama, vLLM in the VPC
    with pytest.raises(EndpointUrlError, match="private or loopback"):
        await check_endpoint_url(url, allow_private=False)


async def test_hostnames_are_checked_by_what_they_resolve_to() -> None:
    with pytest.raises(EndpointUrlError, match="private or loopback"):
        await check_endpoint_url("http://localhost:4250/v1", allow_private=False)


async def test_unresolvable_hosts_and_missing_hosts_are_refused() -> None:
    with pytest.raises(EndpointUrlError, match="Cannot resolve"):
        await check_endpoint_url("http://no-such-host.invalid/v1", allow_private=True)
    with pytest.raises(EndpointUrlError, match="no host"):
        await check_endpoint_url("http:///v1", allow_private=True)


async def test_ipv6_loopback_is_private_not_reserved() -> None:
    await check_endpoint_url("http://[::1]:11434/v1", allow_private=True)
    with pytest.raises(EndpointUrlError, match="private or loopback"):
        await check_endpoint_url("http://[::1]:11434/v1", allow_private=False)


async def test_carrier_grade_nat_space_counts_as_private() -> None:
    await check_endpoint_url("http://100.64.0.10/v1", allow_private=True)
    with pytest.raises(EndpointUrlError, match="private or loopback"):
        await check_endpoint_url("http://100.64.0.10/v1", allow_private=False)
