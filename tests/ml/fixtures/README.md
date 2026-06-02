# ML efficacy fixtures

Drop labeled photos here to turn `test_golden_set_accuracy` into a real accuracy
check (it skips while this folder has no `labels.json`).

Create `labels.json` as a list of entries; each names an image in this folder
plus one or more expectations:

```json
[
  {
    "file": "family_portrait.jpg",
    "expect_min_faces": 2,
    "expect_tags": ["person", "indoor"],
    "expect_caption_substr": "people"
  },
  {
    "file": "beach.jpg",
    "expect_tags": ["beach", "ocean"]
  }
]
```

Supported keys:

- `file` (required) — image filename in this directory.
- `expect_min_faces` — minimum faces `run_faces` must detect.
- `expect_tags` — substrings that must each appear in some Qwen3-VL tag.
- `expect_caption_substr` — substring the BLIP caption must contain (case-insensitive).

Run with weights loaded:

```bash
pytest tests/ml -m ml --run-ml
```

These images are intentionally **not** committed; keep your golden set local or
in a separate data repo to avoid bloating the source tree.
