"""The 'scene granularity' knob: POST /api/scenes/regroup re-segments scenes by
capture-time gap alone, and near-dup sets stay nested inside one scene."""


def _report(images):
    return {"folder": "/fake", "backend": "para", "caption_model": None,
            "face_model": None, "duplicate_groups": 0, "scene_groups": 0,
            "images": images}


def _img(name, t, dup=None):
    return {"path": f"/fake/{name}", "filename": name, "combined": 0.5,
            "sharpness": 0.5, "para_aesthetic": 0.5, "dup_group": dup,
            "scene_group": None, "capture_time": t, "imgw": 100, "imgh": 100,
            "faces": []}


def test_regroup_splits_on_gap(make_env):
    # Three tight shots, a 300s pause, then two more.
    env = make_env(_report([
        _img("a.jpg", 1000), _img("b.jpg", 1010), _img("c.jpg", 1020),
        _img("d.jpg", 1320), _img("e.jpg", 1330)]))
    c = env.client

    assert c.post("/api/scenes/regroup", json={"gap": 120}).json()["scene_groups"] == 2
    assert c.post("/api/scenes/regroup", json={"gap": 600}).json()["scene_groups"] == 1
    # Below every inter-shot gap (10s) → everything becomes a singleton.
    assert c.post("/api/scenes/regroup", json={"gap": 5}).json()["scene_groups"] == 0


def test_regroup_keeps_dup_set_in_one_scene(make_env):
    # c,d are a near-dup set straddling the 300s gap: coarsening must pull the
    # whole thing into a single scene even though time alone would split it.
    env = make_env(_report([
        _img("a.jpg", 1000), _img("b.jpg", 1010), _img("c.jpg", 1020, dup=0),
        _img("d.jpg", 1320, dup=0), _img("e.jpg", 1330)]))
    c = env.client

    assert c.post("/api/scenes/regroup", json={"gap": 120}).json()["scene_groups"] == 1
    # Chosen gap is remembered for the slider to re-open on.
    assert c.get("/api/meta").json()["meta"]["scene_gap"] == "120.0"


def test_merge_scenes_pins_and_survives_finer_regroup(make_env):
    env = make_env(_report([
        _img("a.jpg", 1000), _img("b.jpg", 1010),
        _img("c.jpg", 9000), _img("d.jpg", 9010), _img("e.jpg", 9020)]))
    c = env.client

    c.post("/api/scenes/regroup", json={"gap": 120})
    sgs = sorted(s["scene_group"] for s in c.get("/api/scenes").json()["scenes"])
    assert len(sgs) == 2

    assert c.post("/api/scenes/merge", json={"scene_groups": sgs}).json()["scene_groups"] == 1
    sc = c.get("/api/scenes").json()
    assert sc["total"] == 1
    assert sc["scenes"][0]["manual"] is True
    assert len(sc["scenes"][0]["items"]) == 5

    # A finer gap that would normally split them must not break the manual pin.
    c.post("/api/scenes/regroup", json={"gap": 30})
    sc = c.get("/api/scenes").json()
    assert sc["total"] == 1 and len(sc["scenes"][0]["items"]) == 5


def test_merge_survives_rebuild_and_unmerge_releases(make_env, rebuild):
    env = make_env(_report([
        _img("a.jpg", 1000), _img("b.jpg", 1010),
        _img("c.jpg", 9000), _img("d.jpg", 9010), _img("e.jpg", 9020)]))
    c = env.client
    c.post("/api/scenes/regroup", json={"gap": 120})
    sgs = sorted(s["scene_group"] for s in c.get("/api/scenes").json()["scenes"])
    c.post("/api/scenes/merge", json={"scene_groups": sgs})

    # A fresh ingest (re-analyze + index) must preserve the manual merge.
    rebuild(env)
    sc = c.get("/api/scenes").json()
    assert sc["total"] == 1 and sc["scenes"][0]["manual"] is True

    # Unmerge releases it back to time-based segmentation (two scenes again).
    sg = sc["scenes"][0]["scene_group"]
    c.post("/api/scenes/unmerge", json={"scene_group": sg})
    sc = c.get("/api/scenes").json()
    assert sc["total"] == 2
    assert all(s["manual"] is False for s in sc["scenes"])


def test_regroup_clamps_and_reflects_in_scenes_endpoint(make_env):
    env = make_env(_report([
        _img("a.jpg", 1000), _img("b.jpg", 1010),
        _img("d.jpg", 9000), _img("e.jpg", 9010)]))
    c = env.client

    c.post("/api/scenes/regroup", json={"gap": 120})
    scenes = c.get("/api/scenes").json()
    assert scenes["total"] == 2
    sizes = sorted(len(s["items"]) for s in scenes["scenes"])
    assert sizes == [2, 2]
