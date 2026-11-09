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
