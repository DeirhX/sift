"""`sift analyze --move-junk` moves low scorers aside; it must never lose one."""
from sift.audit.cli import move_junk


def test_move_junk_keeps_both_files_that_share_a_name(tmp_path):
    day1, day2, junk = tmp_path / "day1", tmp_path / "day2", tmp_path / "junk"
    day1.mkdir()
    day2.mkdir()
    (day1 / "IMG_0001.jpg").write_bytes(b"first")
    (day2 / "IMG_0001.jpg").write_bytes(b"second")
    (day2 / "good.jpg").write_bytes(b"good")
    records = [
        {"path": str(day1 / "IMG_0001.jpg"), "combined": 0.10},
        {"path": str(day2 / "IMG_0001.jpg"), "combined": 0.20},
        {"path": str(day2 / "good.jpg"), "combined": 0.90},
        {"path": str(day2 / "gone.jpg"), "combined": 0.05},      # vanished: skipped
    ]

    assert move_junk(records, junk, threshold=0.5) == 2

    assert sorted(p.name for p in junk.iterdir()) == ["IMG_0001.jpg", "IMG_0001_1.jpg"]
    assert {p.read_bytes() for p in junk.iterdir()} == {b"first", b"second"}
    assert (day2 / "good.jpg").exists()
