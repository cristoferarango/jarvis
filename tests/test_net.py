import pytest

from crisvis.media.net import ProxyError, blocked_address, vet_target


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.3.4",
        "169.254.169.254",
        "100.100.100.100",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "no-es-una-ip",
    ],
)
def test_private_addresses_are_blocked(ip: str) -> None:
    assert blocked_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_pass(ip: str) -> None:
    assert not blocked_address(ip)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://router.local/",
        "http://127.0.0.1:8787/health",
        "http://2130706433/",  # 127.0.0.1 en decimal
        "http://0x7f000001/",  # 127.0.0.1 en hexadecimal
        "http://127.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_vet_target_blocks_internal(url: str) -> None:
    with pytest.raises(ProxyError) as err:
        vet_target(url)
    assert err.value.status == 403


@pytest.mark.parametrize("url", ["ftp://example.com/", "javascript:alert(1)", "", "/relativa"])
def test_vet_target_requires_http(url: str) -> None:
    with pytest.raises(ProxyError) as err:
        vet_target(url)
    assert err.value.status == 400


def test_vet_target_accepts_public() -> None:
    assert vet_target("https://example.com/a?b=c").host == "example.com"
