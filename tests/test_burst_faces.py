from pathlib import Path

import numpy as np
from PIL import Image

from sift.audit.faces import _burst_sources, _retry_face


def test_burst_candidates_require_time_and_composition():
    anchor, missed, far, different, rotated = map(Path, ("a", "b", "c", "d", "e"))
    same = np.full((32, 32, 3), 80, dtype=np.int16)
    previews = {
        anchor: ((400, 300), 1, same),
        missed: ((800, 600), 1, same + 5),
        far: ((400, 300), 1, same),
        different: ((400, 300), 1, same + 40),
        rotated: ((400, 300), 6, same),
    }
    times = {anchor: 100, missed: 104, far: 109, different: 104, rotated: 104}
    assert dict(_burst_sources(previews, times, {anchor: [object()]})) == {missed: [anchor]}


def test_crop_retry_requires_target_detection_and_matching_identity():
    img = Image.new("RGB", (400, 300))
    expected = [140, 100, 220, 180]
    identity = np.array([1, 0], dtype=np.float32)

    def candidate(embedding):
        # Expected crop origin is (60, 20) with 2x magnification.
        return lambda crop, _: [([160, 160, 320, 320], 0.99, embedding)]

    assert _retry_face(img, expected, identity, lambda *_: [], [], 80) is None
    assert _retry_face(img, expected, identity, candidate(identity[::-1]), [], 80) is None
    assert _retry_face(img, expected, identity, candidate(identity), [expected], 80) is None
    found = _retry_face(img, expected, identity, candidate(identity), [], 80)
    assert found is not None and found[0] == expected


# ── Acceptance tiers ─────────────────────────────────────────────────────────

def test_small_face_needs_near_certain_detection():
    from sift.audit.faces import _accept_direct
    assert _accept_direct(0.91, 0.05, 0.04)             # big enough on its own
    assert _accept_direct(0.99, 0.02, 0.04)             # small but near-certain
    assert not _accept_direct(0.96, 0.02, 0.04)         # small and merely likely
    assert not _accept_direct(1.00, 0.01, 0.04)         # too small for any confidence


def test_candidate_is_kept_when_it_matches_someone_in_another_photo():
    from sift.audit.faces import _known_face_matches
    anna = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    ben = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    loose_anna = np.array([0.55, 0.0, 0.835], dtype=np.float32)   # cosine 0.55 to anna
    here, soon, later = Path("here"), Path("soon"), Path("later")
    times = {here: 1000.0, soon: 1100.0, later: 90000.0}
    box = [0, 0, 10, 10]
    candidates = {
        soon: [(box, 0.97, loose_anna),      # anna was just photographed -> kept
               (box, 0.97, ben),             # nobody like this anywhere -> dropped
               (box, 0.90, anna)],           # not confident enough to be rescued
        later: [(box, 0.97, loose_anna),     # hours later: a loose match isn't enough
                (box, 0.97, anna)],          # ...a strong one still is
    }
    assert _known_face_matches(candidates, [(here, anna)], times) == [(soon, 0), (later, 1)]


def test_a_face_cannot_vouch_for_another_face_in_the_same_photo():
    from sift.audit.faces import _known_face_matches
    emb = np.array([1.0, 0.0], dtype=np.float32)
    p = Path("only")
    assert _known_face_matches({p: [([0, 0, 9, 9], 0.99, emb)]}, [(p, emb)], {p: 5.0}) == []
    assert _known_face_matches({p: [([0, 0, 9, 9], 0.99, emb)]}, [], {p: 5.0}) == []
