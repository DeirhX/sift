"""Manual face edits must survive what a re-analysis actually does to the data:
cluster ids get renumbered, boxes shift by a pixel, and (once) the whole
coordinate frame moves from raw sensor pixels to upright."""
import json
import sqlite3

import pytest
from PIL import Image

from conftest import default_report, face_id, items_by_name
from sift.audit.grouping import compute_phashes
from sift.audit.imaging import read_upright_size, upright_box, upright_size

# EXIF orientation -> the transpose PIL's exif_transpose applies for it.
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@pytest.mark.parametrize("orientation", range(1, 9))
def test_upright_box_matches_exif_transpose_geometry(orientation):
    raw = Image.new("L", (40, 20), 0)
    box = (30, 2, 38, 10)
    raw.paste(255, box)
    upright = raw.transpose(_TRANSPOSE[orientation]) if orientation > 1 else raw

    assert upright.size == upright_size(raw.size, orientation)
    mapped = upright_box(box, raw.size, orientation)
    # The mapped box is exactly where the marked pixels landed.
    assert tuple(mapped) == upright.getbbox()


def _rotated_jpeg(path, size=(40, 20), orientation=6):
    exif = Image.Exif()
    exif[0x0112] = orientation
    Image.new("RGB", size, (90, 120, 150)).save(path, "JPEG", exif=exif)


def test_sizes_are_reported_upright(tmp_path):
    p = tmp_path / "portrait.jpg"
    _rotated_jpeg(p)
    assert read_upright_size(p) == (20, 40)
    _, sizes = compute_phashes([p])
    assert sizes[p] == (20, 40)


def _reanalyzed(report):
    """What a later analyze does to the same library: the clusterer hands the
    same two people swapped ids, and every box lands a pixel off."""
    out = json.loads(json.dumps(report))
    for im in out["images"]:
        for f in im["faces"]:
            f["cluster_id"] = 1 - f["cluster_id"]
            f["bbox"] = [v + 1.0 for v in f["bbox"]]
    return out


def _clusters(client):
    items = items_by_name(client)
    return {fn: sorted((f["bbox"][0], f["cluster_id"]) for f in it["faces"])
            for fn, it in items.items()}


def test_assign_follows_the_person_when_cluster_ids_are_renumbered(make_env, rebuild):
    report = default_report()
    env = make_env(report)
    # c.jpg's small face (person 1) is really the person in a.jpg (person 0).
    small = next(f for f in items_by_name(env.client)["c.jpg"]["faces"]
                 if f["bbox"][0] == 100.0)
    r = env.client.post(f"/api/faces/{small['id']}/assign", json={"cluster_id": 0})
    assert r.status_code == 200

    env.report.write_text(json.dumps(_reanalyzed(report)), encoding="utf-8")
    rebuild(env)

    after = _clusters(env.client)
    a_person = after["a.jpg"][0][1]
    b_person = after["b.jpg"][0][1]
    assert a_person != b_person
    # Both of c.jpg's faces are a.jpg's person — whatever id that person has now.
    assert [cid for _, cid in after["c.jpg"]] == [a_person, a_person]


def test_merge_follows_the_person_when_cluster_ids_are_renumbered(make_env, rebuild):
    report = default_report()
    report["images"].append(
        {"path": "/fake/d.jpg", "filename": "d.jpg", "combined": 0.40,
         "sharpness": 0.40, "para_aesthetic": 0.40, "dup_group": None,
         "imgw": 400, "imgh": 300,
         "faces": [{"bbox": [10.0, 10.0, 90.0, 90.0], "prob": 0.98,
                    "cluster_id": 2, "name": None, "sharp": 0.5, "expr": 0.5}]})
    env = make_env(report)
    assert env.client.post("/api/clusters/merge",
                           json={"from": 2, "into": 1}).json()["moved"] == 1

    # Re-analysis: person 2 keeps its id, persons 0 and 1 swap.
    again = json.loads(json.dumps(report))
    for im in again["images"]:
        for f in im["faces"]:
            f["cluster_id"] = {0: 1, 1: 0, 2: 2}[f["cluster_id"]]
    env.report.write_text(json.dumps(again), encoding="utf-8")
    rebuild(env)

    after = _clusters(env.client)
    assert after["d.jpg"][0][1] == after["b.jpg"][0][1]      # b.jpg was person 1
    assert after["d.jpg"][0][1] != after["a.jpg"][0][1]


def test_deleted_face_stays_deleted_when_its_box_shifts(make_env, rebuild):
    report = default_report()
    env = make_env(report)
    assert env.client.delete(f"/api/faces/{face_id(env.client, 'a.jpg')}").status_code == 200

    env.report.write_text(json.dumps(_reanalyzed(report)), encoding="utf-8")
    rebuild(env)
    assert items_by_name(env.client)["a.jpg"]["faces"] == []


def test_re_editing_a_face_replaces_its_earlier_override(make_env, rebuild):
    report = default_report()
    env = make_env(report)
    env.client.post(f"/api/faces/{face_id(env.client, 'a.jpg')}/assign",
                    json={"cluster_id": 1})
    env.report.write_text(json.dumps(_reanalyzed(report)), encoding="utf-8")
    rebuild(env)
    # Same face, box now a pixel off: deleting it must supersede the assign.
    env.client.delete(f"/api/faces/{face_id(env.client, 'a.jpg')}")

    conn = sqlite3.connect(env.db)
    rows = conn.execute(
        "SELECT action FROM face_overrides o JOIN images i ON i.content_hash = o.hash "
        "WHERE i.filename = 'a.jpg'").fetchall()
    conn.close()
    assert rows == [("delete",)]
    rebuild(env)
    assert items_by_name(env.client)["a.jpg"]["faces"] == []


def test_face_edits_migrate_from_raw_to_upright_coordinates(tmp_path, make_env, rebuild):
    photo = tmp_path / "portrait.jpg"
    _rotated_jpeg(photo)                       # raw 40x20, displayed 20x40
    raw_box = [30.0, 2.0, 38.0, 10.0]
    old = {"folder": str(tmp_path), "backend": "para", "face_model": "mtcnn+vggface2",
           "images": [{"path": str(photo), "filename": photo.name, "combined": 0.5,
                       "imgw": 40, "imgh": 20,
                       "faces": [{"bbox": raw_box, "prob": 0.99, "cluster_id": 0}]}]}
    env = make_env(old)
    env.client.post("/api/clusters", json={"cluster_id": 0, "name": "Aja"})
    fid = face_id(env.client, photo.name)
    env.client.post(f"/api/faces/{fid}/assign", json={"new_person": True, "name": "Bo"})

    # The same library re-analyzed by a version that reports upright boxes.
    new = json.loads(json.dumps(old))
    new["coords"] = "upright"
    new["images"][0].update(imgw=20, imgh=40)
    new["images"][0]["faces"][0]["bbox"] = upright_box(raw_box, (40, 20), 6)
    env.report.write_text(json.dumps(new), encoding="utf-8")

    for _ in range(2):                         # second pass: migration is one-time
        rebuild(env)
        face = items_by_name(env.client)[photo.name]["faces"][0]
        names = {c["cluster_id"]: c["name"]
                 for c in env.client.get("/api/meta").json()["clusters"]}
        assert names[face["cluster_id"]] == "Bo"



def test_assignment_reference_migrates_with_target_rotation(tmp_path, make_env, rebuild):
    target = tmp_path / "target.jpg"
    _rotated_jpeg(target, size=(100, 80))
    raw_box = [10, 15, 30, 35]
    old = {"folder": str(tmp_path), "images": [
        {"path": "/fake/source.jpg", "filename": "source.jpg", "imgw": 100, "imgh": 80,
         "faces": [{"bbox": [10, 10, 30, 30], "prob": 0.98, "cluster_id": 1}]},
        {"path": str(target), "filename": target.name, "imgw": 100, "imgh": 80,
         "faces": [{"bbox": raw_box, "prob": 0.99, "cluster_id": 2}]},
    ]}
    env = make_env(old)
    fid = face_id(env.client, "source.jpg")
    assert env.client.post(f"/api/faces/{fid}/assign", json={"cluster_id": 2}).is_success

    old["coords"] = "upright"
    old["images"][1].update(imgw=80, imgh=100)
    old["images"][1]["faces"][0].update(bbox=upright_box(raw_box, (100, 80), 6), cluster_id=3)
    env.report.write_text(json.dumps(old), encoding="utf-8")
    for _ in range(2):
        rebuild(env)
        assert _clusters(env.client)["source.jpg"][0][1] == 3
