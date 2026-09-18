# tools

Helper scripts that run **fully offline**. They never download anything and never
generate explicit content.

## `synth_dataset.py` — synthetic dataset builder

Creates a YOLO-format dataset aimed at the detector's weak spots:

* **procedural hard negatives** — skin-tone shapes, mosaic, gradients, very dark,
  noisy, blurred and heavily compressed frames (the exact cases that cause false
  positives);
* **photometric degradations of images you already own** (`--from-folder`), keeping
  geometry untouched so the pseudo-labels from the current model stay valid.

```bash
python tools/synth_dataset.py --out dataset --negatives 200
python tools/synth_dataset.py --out dataset --from-folder D:\photos --per-image 6
python tools/synth_dataset.py --out dataset --eval-only      # false positives only
```

Output: `dataset/images/{train,val}`, `dataset/labels/{train,val}`, `dataset.yaml`,
`manifest.json` and a readable `stats.md`.

## `evaluate.py` — quality report (per class, per brightness)

```bash
python tools/evaluate.py --images dataset/images/val --names dataset/dataset.yaml
python tools/evaluate.py --images dataset/images/val --no-multi --json single.json
```

Reports precision / recall / F1 at IoU ≥ 0.5 per class, plus the same numbers split
into brightness buckets (`<0.25`, `0.25–0.5`, `0.5–0.75`, `>0.75`) — that is how the
dark-frame regression is measured. Without labels it still reports detection
statistics and timings.

Example on the synthetic negative set (116 dark/skin-tone frames, no labels in the
ground truth, multi-exposure on):

```
images: 116  detections: 2  triggered: 2  avg 63 ms  passes: multi  threshold: 0.35

brightness bucket        images  triggered  TP  FP  FN   recall  precision
dark   (<0.25)             82          1   0   1   0     0.00       0.00
dim    (0.25-0.5)          29          1   0   1   0     0.00       0.00
normal (0.5-0.75)           5          0   0   0   0     0.00       0.00
```

Two false positives out of 116 hard negatives (1.7 %), both in dark frames — a
target for a fine-tune, not for guessing.

## Fine-tuning

`dataset.yaml` is ready for `ultralytics`:

```bash
python -m pip install ultralytics
yolo detect train model=yolov8n.pt data=dataset/dataset.yaml epochs=50 imgsz=320
```

Boundary: train only on data you are allowed to use (your own images, or a
properly licensed, hand-labelled dataset). "Just scrape it" or "generate it" is
not an option here, and the tooling above is deliberately built so that a useful
fine-tune is possible **without** any explicit imagery: the biggest real-world win
is removing false positives on skin tone, dark frames and compression artefacts.
