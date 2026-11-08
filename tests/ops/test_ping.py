import urllib.error

from ledgerline.ops import ping


def test_pinger_fires_first_then_waits_for_the_interval():
    now = [0.0]
    sent = []
    pinger = ping.Pinger(
        "https://hc.example/x",
        300,
        clock=lambda: now[0],
        sender=lambda url: sent.append(url) or True,
    )
    assert pinger.tick() and pinger.tick() is False
    now[0] = 299
    assert pinger.tick() is False
    now[0] = 300
    assert pinger.tick() is True
    assert sent == ["https://hc.example/x"] * 2


def test_pinger_without_url_never_sends():
    pinger = ping.Pinger("", 1, sender=lambda _u: (_ for _ in ()).throw(AssertionError("sent")))
    assert pinger.tick() is False


def test_send_rejects_non_http_urls():
    assert ping.send("file:///etc/passwd") is False
    assert ping.send("") is False


def test_send_swallows_network_errors(monkeypatch):
    def boom(*_a, **_k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(ping.urllib.request, "urlopen", boom)
    assert ping.send("https://hc.example/x") is False


def test_send_appends_suffix(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, _n):
            return b""

    monkeypatch.setattr(
        ping.urllib.request, "urlopen", lambda url, timeout: seen.append(url) or Response()
    )
    assert ping.send("https://hc.example/x/", "/fail") is True
    assert seen == ["https://hc.example/x/fail"]
