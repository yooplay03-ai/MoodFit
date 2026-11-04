"""
eval_tagging.py — 옷 사진 자동 분류가 얼마나 맞는지 잰다.

데이터: Hugging Face의 ashraq/fashion-product-images-small
        (Kaggle "Fashion Product Images (Small)"을 옮긴 것. 쇼핑몰 상품 사진 4.4만 장과
         종류·색·계절·용도 라벨이 있다. 저장소에는 넣지 않고 실행할 때 내려받는다)
비교:
  A. 제로샷 — CLIP 모델 2개 (일반 CLIP, 패션 사진으로 더 학습된 FashionCLIP)
  B. 선형 분류기 — CLIP 사진 벡터 위에 로지스틱 회귀를 학습 (5-fold 교차검증)
  C. 색상 — 사진 평균색 vs k-means(RGB 거리) vs k-means(Lab 거리 + 흰 옷 보정) vs CLIP 제로샷
  D. 계절 — '두께·소재' 제로샷 (--photos로 실제 사진에서 확인. 데이터셋의 계절 라벨은 기준이 달라 참고용)
선택: --photos 폴더에 내 옷 사진과 labels.csv를 넣으면, 실제 휴대폰 사진에서도 같은 평가를 한다.

실행 (Colab GPU 권장):
  pip install -r requirements-ml.txt datasets scikit-learn
  python scripts/eval_tagging.py --per-class 150
"""

import argparse
import csv
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import tagger  # noqa: E402

MODELS = ["openai/clip-vit-base-patch32", "patrickjohncyh/fashion-clip"]

# ── 데이터셋 라벨 → MoodFit 라벨 ─────────────────────────
TOP = {"Tshirts", "Shirts", "Tops", "Sweatshirts", "Sweaters", "Tunics"}
OUTER = {"Jackets", "Blazers", "Rain Jacket", "Waistcoat", "Nehru Jackets", "Shrug", "Coats"}
BOTTOM_SUB = {"Bottomwear"}
ACCESSORY_SUB = {"Watches", "Belts", "Headwear", "Eyewear", "Jewellery", "Scarves", "Mufflers", "Ties"}
SEASON = {"Summer": "여름", "Winter": "겨울", "Spring": "봄", "Fall": "가을"}
USAGE = {"Casual": "캐주얼", "Formal": "포멀", "Sports": "스포티"}
COLOR = {"Black": "블랙", "White": "화이트", "Grey": "그레이", "Navy Blue": "네이비", "Blue": "블루",
         "Beige": "베이지", "Brown": "브라운", "Green": "그린", "Olive": "카키", "Khaki": "카키", "Red": "레드",
         "Pink": "핑크", "Yellow": "옐로우", "Orange": "오렌지", "Purple": "퍼플"}


def category_of(row) -> str | None:
    if row["articleType"] in OUTER:
        return "아우터"
    if row["articleType"] in TOP:
        return "상의"
    if row["subCategory"] in BOTTOM_SUB:
        return "하의"
    if row["masterCategory"] == "Footwear" and row["subCategory"] != "Socks":
        return "신발"
    if row["subCategory"] == "Bags":
        return "가방"
    if row["subCategory"] in ACCESSORY_SUB:
        return "액세서리"
    return None          # 원피스, 이너웨어, 화장품 등은 MoodFit 카테고리에 없어 제외


def load_dataset_sample(per_class: int, seed: int):
    from datasets import load_dataset
    ds = load_dataset("ashraq/fashion-product-images-small", split="train")
    meta = ds.remove_columns("image")
    by_cat = defaultdict(list)
    for i, row in enumerate(meta):
        cat = category_of(row)
        if cat:
            by_cat[cat].append(i)
    rng = random.Random(seed)
    picked = []
    for cat, idx in sorted(by_cat.items()):
        rng.shuffle(idx)
        picked += idx[:per_class]
    print("카테고리별 표본:", {c: min(len(v), per_class) for c, v in sorted(by_cat.items())})
    samples = []
    for i in picked:
        row = ds[i]
        samples.append({
            "image": row["image"].convert("RGB"),
            "category": category_of(row),
            "season": SEASON.get(row["season"]),
            "style": USAGE.get(row["usage"]),
            "color": COLOR.get(row["baseColour"]),
            "name": row["productDisplayName"],
        })
    return samples


def load_my_photos(folder: Path):
    """labels.csv: filename,category,color,season,style (모르는 칸은 비워 둔다)"""
    from PIL import Image
    samples = []
    with open(folder / "labels.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            samples.append({"image": Image.open(folder / row["filename"]).convert("RGB"),
                            "category": row["category"] or None, "color": row.get("color") or None,
                            "season": row.get("season") or None, "style": row.get("style") or None,
                            "name": row["filename"]})
    return samples


# ── 평가 ────────────────────────────────────────────────
def embed_all(samples, model_id, batch=32):
    tagger.MODEL_ID = model_id
    tagger._load.cache_clear()
    torch, model, processor, banks, _ = tagger._load()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    embs = []
    t0 = time.perf_counter()
    for s in range(0, len(samples), batch):
        imgs = [x["image"] for x in samples[s:s + batch]]
        with torch.no_grad():
            inputs = processor(images=imgs, return_tensors="pt").to(device)
            e = tagger._as_tensor(torch, model.get_image_features(**inputs))
        embs.append((e / e.norm(dim=-1, keepdim=True)).cpu())
    ms = (time.perf_counter() - t0) * 1000 / len(samples)
    banks = {k: (labels, emb.cpu()) for k, (labels, emb) in banks.items()}
    return torch.cat(embs), banks, ms


def zero_shot(img_emb, labels, label_emb, allowed=None):
    """라벨 벡터(프롬프트 평균)와 비교해 고른다. allowed가 있으면 그 라벨들 안에서만 고른다."""
    keep = [i for i, l in enumerate(labels) if allowed is None or l in allowed]
    probs = (100.0 * img_emb @ label_emb[keep].T).softmax(dim=-1).numpy()
    names = [labels[i] for i in keep]
    return [names[i] for i in probs.argmax(1)], probs.max(1)


def accuracy(pred, gold):
    """정답이 "봄/가을"처럼 여러 개면 그중 하나만 맞혀도 정답으로 센다."""
    pairs = [(p, g) for p, g in zip(pred, gold) if g]
    if not pairs:
        return 0.0, 0
    return sum(p in g.split("/") for p, g in pairs) / len(pairs), len(pairs)


def linear_probe(X, y, seed):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    keep = [i for i, v in enumerate(y) if v]
    Xk, yk = X[keep], np.array([y[i] for i in keep])
    if min(Counter(yk).values()) < 5:
        return None
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    return cross_val_score(LogisticRegression(max_iter=3000), Xk, yk, cv=cv).mean()


def mean_color(image):
    a = np.asarray(image.resize((32, 32)), dtype=float)[8:24, 8:24].reshape(-1, 3).mean(0)
    return tagger.nearest_color(a, space="rgb")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=150)
    ap.add_argument("--photos", type=Path, help="내 옷 사진 폴더 (labels.csv 포함)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--models", nargs="+", default=MODELS)
    args = ap.parse_args()

    samples = load_my_photos(args.photos) if args.photos else load_dataset_sample(args.per_class, args.seed)
    source = args.photos.name if args.photos else f"fashion-product-images (클래스당 {args.per_class})"
    gold = {k: [s[k] for s in samples] for k in ("category", "season", "style", "color")}
    rows = []

    # 색상: 모델과 상관없는 비교
    color_methods = (("평균색", mean_color),
                     ("k-means, RGB", lambda im: tagger.dominant_color(im, space="rgb")[0]),
                     ("k-means, Lab + 흰 옷 보정", lambda im: tagger.dominant_color(im, space="lab")[0]))
    for method, fn in color_methods:
        pred = [fn(s["image"]) for s in samples]
        acc, n = accuracy(pred, gold["color"])
        rows.append({"data": source, "model": "-", "field": "color", "method": method, "accuracy": round(acc, 3), "n": n})
        wrong = Counter((g, p) for g, p in zip(gold["color"], pred) if g and p not in g.split("/"))
        print(f"색상 [{method}] 자주 틀린 경우(정답 → 예측):", wrong.most_common(6))
        if args.photos:
            print("    " + ", ".join(f"{s_['name']}: {p_}" for s_, p_ in zip(samples, pred)))

    for model_id in args.models:
        X, banks, ms = embed_all(samples, model_id)
        print(f"\n[{model_id}] 사진 1장당 {ms:.1f}ms")
        allowed = {"style": {"캐주얼", "포멀", "스포티"}}   # 데이터셋 용도 라벨에 있는 3가지 안에서 비교
        for field in ("category", "season", "style", "color"):
            pred, conf = zero_shot(X, *banks[field], allowed=allowed.get(field))
            acc, n = accuracy(pred, gold[field])
            rows.append({"data": source, "model": model_id, "field": field, "method": "zero-shot", "accuracy": round(acc, 3), "n": n})
            if field == "category":
                confident = [i for i, c in enumerate(conf) if c >= 0.6]
                acc_c, n_c = accuracy([pred[i] for i in confident], [gold[field][i] for i in confident])
                rows.append({"data": source, "model": model_id, "field": field, "method": "zero-shot, 확신 60% 이상만",
                             "accuracy": round(acc_c, 3), "n": n_c})
            if field in ("category", "color", "season"):
                confusion = Counter((g, p) for g, p in zip(gold[field], pred) if g and p not in g.split("/"))
                print(f"  {field} 자주 틀린 경우(정답 → 예측):", confusion.most_common(5))
            if args.photos:
                for s_, p_ in zip(samples, pred):
                    print(f"    {s_['name']}: {field} 예측 {p_} / 정답 {s_[field] or '-'}")
            if not args.photos and field != "color":
                probe = linear_probe(X.numpy(), gold[field], args.seed)
                if probe is not None:
                    rows.append({"data": source, "model": model_id, "field": field, "method": "linear probe (5-fold)",
                                 "accuracy": round(probe, 3), "n": n})

    print("\n| 모델 | 항목 | 방법 | 정확도 | 표본 |\n|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model'].split('/')[-1]} | {r['field']} | {r['method']} | {r['accuracy']:.1%} | {r['n']} |")
    out = Path("tagging_results.csv")
    new = not out.exists()
    with out.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"\n결과를 {out}에 추가했습니다.")


if __name__ == "__main__":
    main()
