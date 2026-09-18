#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
synth_dataset.py - генератор СИНТЕТИЧЕСКОГО датасета для дообучения детектора nsfw_guard.

English summary: builds a local, synthetic YOLO dataset of *hard negatives*
(skin tone shapes, mosaic, gradients, dark/noisy/blurred/JPEG frames) and optional
photometric degradations of images you already own (pseudo-labelled with the
current model). It never downloads anything and never generates explicit content.

ЭТИКА И ПРИВАТНОСТЬ:
  * всё считается и сохраняется локально, сеть не используется вообще;
  * скрипт НЕ генерирует обнажённый контент и не удаляет одежду. Он делает:
      1) процедурные "сложные негативы" - тона кожи, тёмные/шумные/блюрные/JPEG-кадры, мозаика,
         градиенты: именно на них детектор даёт ложные срабатывания;
      2) фотометрические деградации ВАШИХ СОБСТВЕННЫХ изображений (--from-folder) с сохранением
         уже найденных детектором боксов как псевдо-разметки: чтобы модель училась находить то же
         самое в темноте, при сжатии, шуме и блюре.
  * геометрия не меняется (нет кропов/поворотов), поэтому псевдоразметка остаётся корректной.

Выход - формат YOLO: dataset/images/{train,val}/*.jpg + dataset/labels/{train,val}/*.txt
совместим с fine-tune NudeNet 320n / YOLOv8n (ultralytics).

Примеры:
  python synth_dataset.py --out dataset --negatives 150
  python synth_dataset.py --out dataset --from-folder D:\\photos --per-image 6
  python synth_dataset.py --out dataset --eval-only          # только посчитать FP на негативах
"""

import argparse
import json
import os
import random
import shutil
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))     # чтобы импортировать nsfw_guard из родителя

try:
    import nsfw_guard as guard
except Exception:                              # автономный режим без детектора
    guard = None

# порядок классов = порядок из nudenet (ALL_CLASSES в nsfw_guard)
CLASSES = guard.ALL_CLASSES if guard else []
BRIGHTNESS_LEVELS = [1.0, 0.7, 0.5, 0.35, 0.25, 0.15]


def rng_of(seed):
    return random.Random(seed), np.random.default_rng(seed)


def luminance(img):
    if img.ndim == 3:
        return float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
    return float(img.mean())


def skin_tone_mask_colors(rng):
    """Случайные оттенки кожи в BGR (широкий диапазон: светлая, тёмная, красноватая, оливковая)."""
    h = rng.choice([5, 8, 10, 12, 15, 18, 170])          # HSV hue
    s = rng.randint(40, 170)
    v = rng.randint(70, 245)
    hsv = np.uint8([[[int(h), int(s), int(v)]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(b), int(g), int(r)


def scene_skin_shapes(w, h, rng):
    """Сцена из пятен/эллипсов телесного цвета на случайном фоне (процедурный сложный негатив)."""
    bg = int(rng.randint(10, 70))
    img = np.full((h, w, 3), bg, np.uint8)
    for _ in range(rng.randint(3, 9)):
        color = skin_tone_mask_colors(rng)
        if rng.random() < 0.5:
            x, y = rng.randint(0, w - 1), rng.randint(0, h - 1)
            cv2.circle(img, (x, y), rng.randint(20, int(min(w, h) * 0.35)), color, -1)
        else:
            x1, y1 = rng.randint(0, w // 2), rng.randint(0, h // 2)
            x2, y2 = x1 + rng.randint(w // 6, w // 2), y1 + rng.randint(h // 6, h // 2)
            cv2.rectangle(img, (x1, y1), (min(x2, w - 1), min(y2, h - 1)), color, -1)
    if rng.random() < 0.5:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 3.0))
    return img


def scene_mosaic(w, h, rng):
    """Мозаика/плитка похожих оттенков - классический ловушек для skin-детекторов."""
    cell = rng.randint(8, 40)
    img = np.zeros((h, w, 3), np.uint8)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            img[y:y + cell, x:x + cell] = skin_tone_mask_colors(rng) if rng.random() < 0.7 \
                else (int(rng.randint(0, 255)), int(rng.randint(0, 255)), int(rng.randint(0, 255)))
    return img


def scene_gradient(w, h, rng):
    base = skin_tone_mask_colors(rng)
    ramp = np.linspace(rng.uniform(0.2, 0.6), 1.0, h, dtype=np.float32)[:, None, None]
    img = (np.array(base, np.float32)[None, None, :] * ramp).astype(np.uint8)
    return np.repeat(img, w, axis=1)


def scene_dark_noise(w, h, rng):
    """Тёмный шумный кадр (numpy-генератор)."""
    img = np.full((h, w, 3), int(rng.integers(4, 30)), np.uint8)
    noise = rng.normal(0, rng.uniform(3, 14), (h, w, 3))
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

def degrade(img, rng, brightness=None, jpeg=None, blur=None, noise=None, contrast=None):
    """Фотометрические деградации. Геометрия не меняется, поэтому боксы остаются валидными."""
    out = img.astype(np.float32)
    if brightness is not None:
        out *= float(brightness)
    if contrast is not None:
        mean = float(out.mean())
        out = (out - mean) * float(contrast) + mean
    out = np.clip(out, 0, 255).astype(np.uint8)
    if blur:
        k = int(blur) * 2 + 1
        out = cv2.GaussianBlur(out, (k, k), 0)
    if noise:
        out = np.clip(out.astype(np.float32) + rng.normal(0, float(noise), out.shape),
                      0, 255).astype(np.uint8)
    if jpeg:
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg)])
        if ok:
            out = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return out


def detect(image, detector):
    """Список детекций текущей модели (или пустой список, если детектора нет)."""
    if detector is None:
        return []
    try:
        return detector.analyse(image).get("detections", [])
    except Exception:
        return []


def yolo_lines(detections, iw, ih):
    lines = []
    for d in detections:
        cls = d.get("class")
        if cls not in CLASSES:
            continue
        x, y, w, h = [float(v) for v in d["box"]]
        nw, nh = w / iw, h / ih
        if nw <= 0 or nh <= 0:
            continue
        cx = max(0.0, min(1.0, (x + w / 2) / iw))
        cy = max(0.0, min(1.0, (y + h / 2) / ih))
        lines.append("%d %.6f %.6f %.6f %.6f" % (CLASSES.index(cls), cx, cy, nw, nh))
    return lines


def write_sample(root, split, idx, image, lines, manifest, origin, quality=88):
    img_dir = os.path.join(root, "images", split)
    lbl_dir = os.path.join(root, "labels", split)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    tag = origin.split(":")[0][:10].replace(" ", "_").replace("->", "")
    name = "%s_%05d" % (tag, idx)
    ip = os.path.join(img_dir, name + ".jpg")
    lp = os.path.join(lbl_dir, name + ".txt")
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return None
    with open(ip, "wb") as fh:
        fh.write(buf.tobytes())
    with open(lp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    manifest.append({
        "file": os.path.relpath(ip, root).replace("\\", "/"),
        "labels": os.path.relpath(lp, root).replace("\\", "/"),
        "boxes": len(lines),
        "origin": origin,
        "luma": round(luminance(image), 1),
    })
    return ip


def make_dataset_yaml(root):
    lines = ["path: %s" % os.path.abspath(root).replace("\\", "/"),
             "train: images/train", "val: images/val", "names:"]
    for i, name in enumerate(CLASSES):
        lines.append("  %d: %s" % (i, name))
    with open(os.path.join(root, "dataset.yaml"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_source_images(folder, limit):
    exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    found = []
    for base, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(exts):
                found.append(os.path.join(base, f))
                if len(found) >= limit:
                    return found
    return found

def generate(args):
    rng, nprng = rng_of(args.seed)
    detector = None
    if guard is not None and (args.from_folder or args.stats):
        detector = guard.Detector(threshold=args.threshold)

    root = args.out
    if args.clean and os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)

    manifest = []
    idx = {"train": 0, "val": 0}
    stats = {"negatives": 0, "negatives_dark": 0, "derived": 0, "fp_on_negatives": 0,
             "classes": {}, "brightness": {}, "sources_with_detections": 0,
             "sources_total": 0, "started": time.strftime("%Y-%m-%d %H:%M:%S")}

    def new_split():
        return "val" if rng.random() < args.val_split else "train"

    def add(image, lines, origin):
        s = new_split()
        if write_sample(root, s, idx[s], image, lines, manifest, origin):
            idx[s] += 1
            return s
        return None

    # ---------- 1) процедурные сложные негативы ----------
    if args.negatives:
        sizes = [(640, 480), (800, 600), (960, 540), (512, 512)]
        for _ in range(args.negatives):
            w, h = rng.choice(sizes)
            kind = rng.choice(["skin", "mosaic", "gradient", "dark"])
            if kind == "skin":
                img = scene_skin_shapes(w, h, rng)
            elif kind == "mosaic":
                img = scene_mosaic(w, h, rng)
            elif kind == "gradient":
                img = scene_gradient(w, h, rng)
            else:
                img = scene_dark_noise(w, h, nprng)
            img = degrade(img, nprng, brightness=rng.choice([0.25, 0.5, 0.8, 1.0, 1.4]),
                          jpeg=rng.choice([None, None, 45, 70]),
                          blur=rng.choice([0, 0, 0, 2]),
                          noise=rng.choice([0, 0, 6]))
            if add(img, [], "neg:%s" % kind):
                stats["negatives"] += 1
                if detector is not None and detect(img, detector):
                    stats["fp_on_negatives"] += 1
            # тёмная копия негатива: в темноте детектор тоже НЕ должен срабатывать
            if rng.random() < 0.5:
                dark = degrade(img, nprng, brightness=rng.choice([0.12, 0.18, 0.25]))
                if add(dark, [], "neg-dark:%s" % kind):
                    stats["negatives_dark"] += 1
                    if detector is not None and detect(dark, detector):
                        stats["fp_on_negatives"] += 1

    # ---------- 2) деградации ваших изображений + псевдоразметка ----------
    if args.from_folder:
        files = load_source_images(args.from_folder, args.max_sources)
        stats["sources_total"] = len(files)
        for path in files:
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                continue
            dets = detect(img, detector)
            if not dets:
                continue
            stats["sources_with_detections"] += 1
            ih, iw = img.shape[:2]
            lines = yolo_lines(dets, iw, ih)
            if not lines:
                continue
            for d in dets:
                stats["classes"][d["class"]] = stats["classes"].get(d["class"], 0) + 1
            if add(img, lines, "src:base"):
                stats["derived"] += 1
            for _ in range(max(0, args.per_image - 1)):
                b = rng.choice(BRIGHTNESS_LEVELS)
                deg = degrade(img, nprng, brightness=b,
                              jpeg=rng.choice([None, 50, 70, 85]),
                              blur=rng.choice([0, 0, 1, 2]),
                              noise=rng.choice([0, 4, 9]),
                              contrast=rng.choice([None, 0.7, 1.3]))
                if add(deg, lines, "src:brightness=%.2f" % b):
                    stats["derived"] += 1
                    key = "%.2f" % b
                    stats["brightness"][key] = stats["brightness"].get(key, 0) + 1

    return root, manifest, stats

def write_reports(root, manifest, stats, args):
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"args": vars(args), "stats": stats, "samples": manifest},
                  fh, ensure_ascii=False, indent=2)
    make_dataset_yaml(root)

    boxes = sum(m["boxes"] for m in manifest)
    negative_total = stats["negatives"] + stats["negatives_dark"]
    md = ["# Синтетический датасет для дообучения детектора", "",
          "Создан: %s (локально, без сетевых обращений)" % stats["started"], "",
          "## Состав", "", "| Что | Значение |", "|---|---|",
          "| Всего изображений | %d |" % len(manifest),
          "| Боксов всего | %d |" % boxes,
          "| Процедурных негативов | %d |" % stats["negatives"],
          "| ...из них тёмных | %d |" % stats["negatives_dark"],
          "| Производных от ваших изображений | %d |" % stats["derived"],
          "| Изображений-источников просмотрено | %d |" % stats["sources_total"],
          "| ...из них с детекциями | %d |" % stats["sources_with_detections"], "",
          "## Ложные срабатывания текущей модели на негативах", ""]
    if negative_total:
        md.append("Негативов: **%d**, модель пометила **%d** (FP %.1f%%) — это готовый материал "
                  "для подавления ложных срабатываний." % (
                      negative_total, stats["fp_on_negatives"],
                      100.0 * stats["fp_on_negatives"] / negative_total))
    else:
        md.append("Негативы не создавались.")
    md += ["", "## Классы в псевдоразметке (из ваших изображений)", ""]
    if stats["classes"]:
        for k, v in sorted(stats["classes"].items(), key=lambda kv: -kv[1]):
            md.append("* %s: %d" % (k, v))
    else:
        md.append("(нет — не передан --from-folder или детекций не нашлось)")
    md += ["", "## Яркость производных копий", ""]
    for k, v in sorted(stats["brightness"].items(), key=lambda kv: -float(kv[0])):
        md.append("* x%s: %d" % (k, v))
    md += ["", "## Важно понимать", "",
           "* Разметка производных копий — **псевдоразметка** (её дала та же модель). Это измеряет "
           "**устойчивость** («находит ли модель то же самое в темноте и при сжатии»), а не "
           "абсолютную точность.",
           "* Истинную precision/recall даёт только вручную размеченный набор.",
           "* Геометрия не меняется, поэтому боксы остаются валидными после деградаций.",
           "* Скрипт не создаёт обнажённый контент: только процедурные фигуры и деградации ваших файлов."]
    with open(os.path.join(root, "stats.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")


def build_parser():
    p = argparse.ArgumentParser(
        description="Синтетический датасет для дообучения детектора (полностью локально)")
    p.add_argument("--out", default="dataset", help="папка датасета")
    p.add_argument("--negatives", type=int, default=150, help="сколько процедурных негативов")
    p.add_argument("--from-folder", help="папка с ВАШИМИ изображениями (деградации + псевдоразметка)")
    p.add_argument("--per-image", type=int, default=6, help="вариантов на каждое изображение")
    p.add_argument("--max-sources", type=int, default=60, help="максимум изображений из папки")
    p.add_argument("--threshold", type=float, default=0.35, help="порог псевдоразметки")
    p.add_argument("--val-split", type=float, default=0.2, help="доля val")
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--clean", action="store_true", help="очистить папку перед генерацией")
    p.add_argument("--no-stats", action="store_true", help="не считать FP текущей модели")
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    args.stats = not args.no_stats
    t0 = time.time()
    root, manifest, stats = generate(args)
    write_reports(root, manifest, stats, args)
    print("датасет: %s" % os.path.abspath(root))
    print("изображений: %d | боксов: %d | производных: %d | негативов: %d (тёмных %d)" % (
        len(manifest), sum(m["boxes"] for m in manifest), stats["derived"],
        stats["negatives"], stats["negatives_dark"]))
    negative_total = stats["negatives"] + stats["negatives_dark"]
    if negative_total:
        print("FP текущей модели на негативах: %d/%d (%.1f%%)" % (
            stats["fp_on_negatives"], negative_total,
            100.0 * stats["fp_on_negatives"] / negative_total))
    print("время: %.1f с" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
