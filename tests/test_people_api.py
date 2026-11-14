"""The People view's backend: people list, one person's faces, batch face
edits, face crops, and embedding-based similarity."""
import io

import numpy as np
from PIL import Image

from conftest import default_report, items_by_name
from sift.audit.embed_store import EmbedStore
from sift.web.routes.people import face_crop_box


def _people(client):
    return {p["cluster_id"]: p for p in client.get("/api/people").json()["people"]}


def _faces(client, cid, **params):
    return client.get(f"/api/people/{cid}/faces", params=params).json()


def test_people_lists_counts_and_best_face_as_cover(env):
    people = _people(env.client)
    assert set(people) == {0, 1}
    assert (people[0]["faces"], people[0]["photos"]) == (2, 2)
    # Person 0 is in a.jpg (sharp 0.90) and c.jpg (sharp 0.60): a.jpg is the cover.
    a_face = items_by_name(env.client)["a.jpg"]["faces"][0]["id"]
    assert people[0]["cover_face_id"] == a_face
    assert people[0]["manual"] is False


def test_named_people_sort_first(env):
    env.client.post("/api/clusters", json={"cluster_id": 1, "name": "Bo"})
    order = [p["cluster_id"] for p in env.client.get("/api/people").json()["people"]]
    assert order == [1, 0]


def test_person_faces_carry_photo_identity_and_sort(env):
    out = _faces(env.client, 0)
    assert out["total"] == 2 and out["name"] is None
    assert [f["filename"] for f in out["items"]] == ["a.jpg", "c.jpg"]     # best first
    face = out["items"][0]
    assert face["bbox"] == [10.0, 10.0, 50.0, 50.0] and face["rel"] == 0.1
    assert face["match"] is None and out["has_match"] is False             # no sidecar
    by_size = _faces(env.client, 0, sort="size")
    assert [f["filename"] for f in by_size["items"]] == ["c.jpg", "a.jpg"]
    assert env.client.get("/api/people/0/faces", params={"sort": "nope"}).status_code == 400


def test_batch_assign_to_new_person_survives_rebuild(env, rebuild):
    ids = [f["id"] for f in _faces(env.client, 1)["items"]]
    r = env.client.post("/api/faces/assign",
                        json={"face_ids": ids, "new_person": True, "name": "Cy"}).json()
    assert r["moved"] == 2
    people = _people(env.client)
    assert 1 not in people and people[r["cluster_id"]]["name"] == "Cy"
    assert people[r["cluster_id"]]["manual"] is True

    rebuild(env)
    people = _people(env.client)
    assert people[r["cluster_id"]]["faces"] == 2 and 1 not in people


def test_batch_delete_reaggregates_photos_and_survives_rebuild(env, rebuild):
    ids = [f["id"] for f in _faces(env.client, 0)["items"]]
    assert env.client.post("/api/faces/delete", json={"face_ids": ids}).json()["deleted"] == 2
    assert 0 not in _people(env.client)
    assert items_by_name(env.client)["a.jpg"]["faces"] == []
    assert items_by_name(env.client)["a.jpg"]["portrait"] is None
    rebuild(env)
    assert 0 not in _people(env.client)


def test_batch_edit_is_all_or_nothing_on_a_stale_face(env):
    ids = [f["id"] for f in _faces(env.client, 0)["items"]]
    r = env.client.post("/api/faces/assign", json={"face_ids": ids + [99999], "cluster_id": 1})
    assert r.status_code == 404
    assert _people(env.client)[0]["faces"] == 2
    assert env.client.post("/api/faces/assign", json={"face_ids": ids}).status_code == 400
    assert env.client.post("/api/faces/delete", json={"face_ids": []}).status_code == 422


def test_similar_people_and_match_come_from_cached_embeddings(env):
    items = items_by_name(env.client)
    # Person 0's faces point one way; person 1's are close (cosine ~0.9) to it.
    vec = {0: [1.0, 0.0, 0.0], 1: [0.9, 0.436, 0.0]}
    with EmbedStore(env.db.parent / ".embeddings.sqlite") as store:
        for it in items.values():
            store.put_faces(it["hash"], [(f["bbox"], vec[f["cluster_id"]]) for f in it["faces"]])

    out = _faces(env.client, 0, sort="match")
    assert out["has_match"] and all(f["match"] > 0.99 for f in out["items"])
    assert [(p["cluster_id"], round(p["similarity"], 1)) for p in out["similar"]] == [(1, 0.9)]

    # An intruder: one of person 1's faces filed under person 0 sorts first in review order.
    stray = _faces(env.client, 1)["items"][0]["id"]
    env.client.post("/api/faces/assign", json={"face_ids": [stray], "cluster_id": 0})
    review = _faces(env.client, 0, sort="match")["items"]
    assert review[0]["id"] == stray and review[0]["match"] < review[-1]["match"]


def test_face_crop_box_stays_square_inside_the_image():
    assert face_crop_box((40, 40, 60, 60), 200, 100) == (34, 34, 66, 66)
    left, top, right, bottom = face_crop_box((0, 0, 20, 20), 200, 100)      # corner face
    assert (left, top) == (0, 0) and right - left == bottom - top == 32
    assert face_crop_box((0, 0, 190, 90), 200, 100) == (45, 0, 145, 100)   # huge face


def test_face_crop_is_upright_and_cached(tmp_path, make_env):
    # Raw sensor frame 40x20 whose right half is red; EXIF says rotate (6), so
    # upright it is 20x40 with the red half at the bottom.
    photo = tmp_path / "portrait.jpg"
    raw = Image.new("RGB", (40, 20), (0, 0, 255))
    raw.paste((255, 0, 0), (20, 0, 40, 20))
    exif = Image.Exif()
    exif[0x0112] = 6
    raw.save(photo, "JPEG", quality=95, exif=exif)
    report = {"folder": str(tmp_path), "backend": "para", "coords": "upright",
              "images": [{"path": str(photo), "filename": photo.name, "combined": 0.5,
                          "imgw": 20, "imgh": 40,
                          "faces": [{"bbox": [6.0, 26.0, 14.0, 34.0], "prob": 0.99,
                                     "cluster_id": 0}]}]}
    env = make_env(report)
    fid = _faces(env.client, 0)["items"][0]["id"]

    r = env.client.get(f"/face/{fid}", params={"size": 96})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    crop = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert crop.size == (96, 96)
    red, _, blue = np.asarray(crop).reshape(-1, 3).mean(axis=0)
    assert red > 200 and blue < 60                       # the face region, not the top half

    assert len(list((env.thumbs / "faces").glob("*_96.webp"))) == 1
    photo.unlink()                                       # served from cache from now on
    assert env.client.get(f"/face/{fid}", params={"size": 96}).content == r.content
    assert env.client.get(f"/face/{fid}", params={"size": 97}).status_code == 400
    assert env.client.get("/face/99999").status_code == 404
