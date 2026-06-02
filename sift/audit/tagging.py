"""Natural-language captions (BLIP) and concrete keyword tags (Qwen3-VL).

The VLM reads the actual scene instead of matching a fixed vocabulary, so it
does not hallucinate absent subjects the way zero-shot CLIP did.
"""
from PIL import Image
from tqdm import tqdm

# ── Keyword tags (Qwen3-VL-8B-Instruct, 4-bit NF4) ───────────────────────────

QWEN_TAG_MODEL = "Qwen/Qwen3-VL-8B-Instruct"

# One instruction, tuned in the spike that retired the old CLIP-vs-vocab tagger:
# concrete keywords, no synonym spam, no gender hedging, no sentences. The VLM
# reads the actual scene (it correctly calls a stuffed cat a "plush toy"), so it
# does not hallucinate absent subjects the way zero-shot CLIP did — which is what
# let the curated vocabulary and the caption-grounding heuristics go away.
QWEN_TAG_PROMPT = (
    "List the distinct, concrete visual keywords describing this photo: "
    "main subject(s), setting, and notable attributes. Rules: at most {k} "
    "keywords, each one or two words, no synonyms, no duplicates, no "
    "sentences. Output ONLY a comma-separated list."
)


def _clean_tags(text: str, top_k: int) -> list[str]:
    """Parse the model's comma list into clean, deduped, lowercased tags."""
    import re
    # Strip a leading list marker only: a bullet/dash, or "1." / "2)" followed by
    # space. Crucially NOT a bare leading digit — that would mangle real tags like
    # "4k resolution" or "35mm" into "k resolution" / "mm".
    marker = re.compile(r"^\s*(?:[\-\u2022]\s*|\d+[.)]\s+)")
    seen: set[str] = set()
    out: list[str] = []
    for chunk in re.split(r"[,\n;]", text or ""):
        t = marker.sub("", chunk.strip()).strip(". ").lower()
        if not t or len(t) > 40 or len(t.split()) > 4:
            continue  # drop empties, list bullets/numbering, sentence-length junk
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= top_k:
            break
    return out


def run_qwen_tags(paths: list, device: str, top_k: int = 12,
                  max_side: int = 1024) -> dict:
    """{path: [tags]} via Qwen3-VL-8B-Instruct.

    4-bit NF4 on CUDA (~6 GB; the vision tower stays in BF16 for tag fidelity),
    full precision on CPU (correct but slow — bitsandbytes needs CUDA). Greedy
    decode, so the same image always yields the same tags."""
    import torch
    from PIL import ImageOps
    from transformers import Qwen3VLForConditionalGeneration, AutoProcessor

    out: dict = {p: [] for p in paths}
    print(f"\nLoading Qwen3-VL tagger ({QWEN_TAG_MODEL}) on {device}...")
    try:
        proc = AutoProcessor.from_pretrained(QWEN_TAG_MODEL)
        load_kw: dict = {"dtype": torch.bfloat16}
        if device == "cuda":
            from transformers import BitsAndBytesConfig
            load_kw["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
                llm_int8_skip_modules=["visual"],
            )
            load_kw["device_map"] = "cuda"
        else:
            load_kw["device_map"] = "cpu"
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            QWEN_TAG_MODEL, **load_kw).eval()

        prompt = QWEN_TAG_PROMPT.format(k=top_k)
        for p in tqdm(paths, desc="Qwen tags"):
            try:
                img = Image.open(p).convert("RGB")
                img = ImageOps.exif_transpose(img)
                if max(img.size) > max_side:
                    img.thumbnail((max_side, max_side))
            except Exception as e:
                print(f"  skip {p.name}: {e}")
                continue
            msgs = [{"role": "user", "content": [
                {"type": "image"}, {"type": "text", "text": prompt}]}]
            text = proc.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
            inputs = proc(text=[text], images=[img],
                          return_tensors="pt").to(model.device)
            with torch.no_grad():
                gen = model.generate(**inputs, max_new_tokens=96, do_sample=False)
            ans = proc.batch_decode(
                gen[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
            out[p] = _clean_tags(ans, top_k)

        del model
        if device == "cuda":
            torch.cuda.empty_cache()
    except Exception as e:
        print(f"  Qwen tagging failed: {e}")
    return out


# ── Captions (BLIP) + keyword tags (Qwen3-VL) ────────────────────────────────

def run_caption_and_tags(paths: list, device: str,
                         top_k: int = 12,
                         batch_size_blip: int = 16) -> dict:
    """
    Returns {path: {"caption": str, "tags": list[str]}} for every path.

    Caption : Salesforce/blip-image-captioning-base (~990 MB, in transformers)
    Tags    : Qwen3-VL-8B-Instruct (4-bit NF4 on GPU) — a vision-language model
              prompted for concrete keywords. It reads the actual scene rather
              than matching a fixed vocabulary, so it does not hallucinate absent
              subjects the way zero-shot CLIP did. See run_qwen_tags().
    """
    import torch
    from transformers import BlipProcessor, BlipForConditionalGeneration

    results: dict = {p: {"caption": "", "tags": []} for p in paths}

    # ── BLIP captions ──────────────────────────────────────────────────────────
    print(f"\nLoading BLIP captioning model on {device}...")
    try:
        blip_proc  = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        blip_model = BlipForConditionalGeneration.from_pretrained(
            "Salesforce/blip-image-captioning-base"
        ).to(device).eval()

        for i in tqdm(range(0, len(paths), batch_size_blip), desc="BLIP captions"):
            batch_paths = paths[i:i + batch_size_blip]
            images, bpaths = [], []
            for p in batch_paths:
                try:
                    images.append(Image.open(p).convert("RGB"))
                    bpaths.append(p)
                except Exception as e:
                    print(f"  skip {p.name}: {e}")
            if not images:
                continue

            inputs = blip_proc(images=images, return_tensors="pt").to(device)
            with torch.no_grad():
                out_ids = blip_model.generate(
                    **inputs, max_new_tokens=64,
                    num_beams=4, length_penalty=1.0,
                )
            captions = blip_proc.batch_decode(out_ids, skip_special_tokens=True)
            for p, cap in zip(bpaths, captions):
                results[p]["caption"] = cap.strip()

        del blip_model
        if device == "cuda":
            torch.cuda.empty_cache()

    except Exception as e:
        print(f"  BLIP captioning failed: {e}")

    # ── Qwen3-VL keyword tags ───────────────────────────────────────────────────
    # BLIP is freed above before the tagger loads, so the ~6 GB NF4 model has the
    # VRAM to itself. Tagging is independent of the caption (no grounding); the
    # VLM works straight off the pixels.
    qtags = run_qwen_tags(paths, device, top_k=top_k)
    for p, tg in qtags.items():
        results[p]["tags"] = tg

    return results
