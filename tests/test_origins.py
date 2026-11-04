import pytest

from kavita_anilist_sync.web import _trusted_origin


@pytest.mark.parametrize("origin", [
    "http://192.168.0.10:5000", "http://192.168.1.20:5000", "http://10.0.0.5", "http://172.16.0.2:8080",
    "http://172.31.255.255:5000", "http://localhost:5000", "http://127.0.0.1:5000", "http://kavita.local:5000", "http://nas.home.local",
    "http://[::1]:5000", "https://192.168.0.10",
])
def test_private_addresses_are_trusted(monkeypatch, origin):
    monkeypatch.setenv("CORS_ORIGINS", "private")
    assert _trusted_origin(origin)


@pytest.mark.parametrize("origin", [
    "https://evil.example", "http://172.32.0.1", "http://192.168.0.10.evil.example", "http://8.8.8.8",
    "http://localhost.evil.example", "http://evil.local.example.com", "", "null", "http://192.168.0.1:5000/path",
])
def test_other_addresses_are_not(monkeypatch, origin):
    monkeypatch.setenv("CORS_ORIGINS", "private")
    assert not _trusted_origin(origin)


def test_explicit_list_still_works(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://kavita.home:5000")
    assert _trusted_origin("http://kavita.home:5000")
    assert not _trusted_origin("http://192.168.0.10:5000")
