"""Coverage for the duplicate-location endpoint and thumbnail/original serving,
including 404 paths. Uses real image files."""
from conftest import items_by_name


DUP_SPECS = [
    ("first.jpg", 0.80, None, "same"),    # byte-identical to second.jpg
    ("second.jpg", 0.70, None, "same"),
    ("solo.jpg", 0.50, None, "unique"),
]


def test_locations_reports_all_copies(real_library):
    env = real_library(DUP_SPECS)
    by = items_by_name(env.client)
    fid = by["first.jpg"]["id"]
    data = env.client.get(f"/api/images/{fid}/locations").json()
    assert data["count"] == 2
    paths = {loc["path"] for loc in data["locations"]}
    assert paths == {str(env.lib / "first.jpg"), str(env.lib / "second.jpg")}
    assert all(loc["exists"] for loc in data["locations"])


def test_duplicates_share_one_hash(real_library):
    env = real_library(DUP_SPECS)
    by = items_by_name(env.client)
    assert by["first.jpg"]["hash"] == by["second.jpg"]["hash"]
    assert by["solo.jpg"]["hash"] != by["first.jpg"]["hash"]


def test_locations_single_for_unique(real_library):
    env = real_library(DUP_SPECS)
    fid = items_by_name(env.client)["solo.jpg"]["id"]
    data = env.client.get(f"/api/images/{fid}/locations").json()
    assert data["count"] == 1


def test_locations_404(real_library):
    env = real_library(DUP_SPECS)
    assert env.client.get("/api/images/99999/locations").status_code == 404


def test_locations_flags_missing_copy(real_library):
    env = real_library(DUP_SPECS)
    (env.lib / "second.jpg").unlink()
    fid = items_by_name(env.client)["first.jpg"]["id"]
    locs = {loc["path"]: loc["exists"]
            for loc in env.client.get(f"/api/images/{fid}/locations").json()["locations"]}
    assert locs[str(env.lib / "first.jpg")] is True
    assert locs[str(env.lib / "second.jpg")] is False


# ── thumbnail + original serving ─────────────────────────────────────────────

def test_serve_thumb_ok(real_library):
    env = real_library([("p.jpg", 0.5, None, "p")], skip_thumbs=False)
    fid = items_by_name(env.client)["p.jpg"]["id"]
    r = env.client.get(f"/thumb/{fid}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"


def test_serve_full_ok(real_library):
    env = real_library([("p.jpg", 0.5, None, "p")])
    fid = items_by_name(env.client)["p.jpg"]["id"]
    assert env.client.get(f"/img/{fid}").status_code == 200


def test_serve_thumb_404_unknown_id(real_library):
    env = real_library([("p.jpg", 0.5, None, "p")], skip_thumbs=False)
    assert env.client.get("/thumb/99999").status_code == 404


def test_serve_full_404_unknown_id(real_library):
    env = real_library([("p.jpg", 0.5, None, "p")])
    assert env.client.get("/img/99999").status_code == 404


def test_serve_full_404_when_file_missing(real_library):
    env = real_library([("p.jpg", 0.5, None, "p")])
    fid = items_by_name(env.client)["p.jpg"]["id"]
    (env.lib / "p.jpg").unlink()
    assert env.client.get(f"/img/{fid}").status_code == 404
