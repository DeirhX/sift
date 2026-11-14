import pytest

from conftest import items_by_name


@pytest.mark.parametrize("headers", [
    {"Origin": "https://outside.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_foreign_form_cannot_move_photos(real_library, headers):
    env = real_library([("photo.jpg", 0.8, None, "photo")])
    client = env.client
    photo = items_by_name(client)["photo.jpg"]
    assert client.post("/api/decisions", json={"hash": photo["hash"], "decision": "del"}).is_success

    blocked = client.post("/api/apply", data={"unused": "1"}, headers=headers)
    assert blocked.status_code == 403
    assert (env.lib / "photo.jpg").exists()
    allowed = client.post("/api/apply", headers={"Origin": "http://testserver"})
    assert allowed.json()["moved"] == 1


def test_local_dev_origin_can_write(env):
    client = env.client
    photo = items_by_name(client)["a.jpg"]
    origin = "http://127.0.0.1:5173"
    response = client.post("/api/decisions", headers={"Origin": origin},
                           json={"hash": photo["hash"], "decision": "keep"})
    assert response.is_success
    assert response.headers["access-control-allow-origin"] == origin
    assert items_by_name(client)["a.jpg"]["decision"] == "keep"


# ── Host allowlist (DNS-rebinding defence) ────────────────────────────────────

def test_allowed_hosts_follow_the_bind_address():
    from sift.web import server

    loopback = server._allowed_hosts_for("127.0.0.1")
    assert loopback == {"localhost", "127.0.0.1", "::1"}
    assert server._allowed_hosts_for("localhost") == loopback
    assert server._allowed_hosts_for("[::1]") == loopback
    assert "photos.lan" in server._allowed_hosts_for("127.0.0.1", ["Photos.LAN"])
    # Serving the network is an explicit choice; its hostnames are unknowable.
    assert server._allowed_hosts_for("0.0.0.0") is None
    assert server._allowed_hosts_for("192.168.1.20") is None


def test_rebound_hostname_is_refused_for_reads_and_writes(real_library, monkeypatch):
    from sift.web import server

    env = real_library([("photo.jpg", 0.8, None, "photo")])
    client = env.client
    photo = items_by_name(client)["photo.jpg"]
    assert client.post("/api/decisions", json={"hash": photo["hash"], "decision": "del"}).is_success
    monkeypatch.setattr(server, "ALLOWED_HOSTS", server._allowed_hosts_for("127.0.0.1"))

    # A page on evil.example re-pointed at 127.0.0.1: same-origin to the
    # browser, so Origin matches Host and the write guard alone would pass it.
    rebound = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
    assert client.get("/api/meta", headers=rebound).status_code == 403
    assert client.get("/api/fs/complete?q=", headers=rebound).status_code == 403
    assert client.post("/api/apply", headers=rebound).status_code == 403
    assert (env.lib / "photo.jpg").exists()

    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST"):
        assert client.get("/api/meta", headers={"Host": host}).status_code == 200, host
    moved = client.post("/api/apply", headers={"Host": "localhost:8000", "Origin": "http://localhost:8000"})
    assert moved.json()["moved"] == 1
