"""
train_probe.py — 계절·스타일 분류기를 학습해 app/data/probe_<모델>.json으로 저장한다.

eval_tagging.py 결과, 학습 없이(제로샷) 계절·스타일을 고르면 거의 찍는 수준이었다
(FashionCLIP 기준 계절 30.8%, 스타일 55.8%). 같은 CLIP 사진 벡터 위에 로지스틱 회귀를 학습하면
5-fold 교차검증에서 계절 63.0%, 스타일 88.9%까지 올라갔다. 그래서 그 분류기를 실제로 학습해 서버에서 쓴다.

- CLIP 모델 자체는 그대로 두고(고정), 마지막에 작은 선형 분류기만 학습한다 (linear probe).
  가중치는 "512차원 × 라벨 수" 숫자뿐이라 JSON 파일 하나(수십 KB)로 저장소에 넣을 수 있다.
- 종류(카테고리)는 제로샷으로도 94.6%라 학습하지 않는다. 상품 사진으로 학습한 분류기는
  집에서 찍은 사진과 분위기가 달라 오히려 덜 맞을 수 있어서, 학습이 꼭 필요한 항목에만 쓴다.
- 스타일은 데이터셋 라벨에 있는 캐주얼·포멀·스포티 3가지만 학습한다.

실행 (Colab GPU 권장):
  pip install -r requirements-ml.txt datasets scikit-learn
  python scripts/train_probe.py --per-class 1500
"""

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import tagger  # noqa: E402
from eval_tagging import SEASON, USAGE, category_of  # noqa: E402

FIELDS = {"season": ("season", SEASON), "style": ("usage", USAGE)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=1500, help="라벨마다 최대 몇 장으로 학습할지")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from datasets import load_dataset
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

    ds = load_dataset("ashraq/fashion-product-images-small", split="train")
    meta = ds.remove_columns("image")
    rng = random.Random(args.seed)

    # 항목별로 라벨마다 같은 수를 뽑는다 (캐주얼이 압도적으로 많아서, 그대로 쓰면 다 캐주얼로 찍는 분류기가 된다)
    picked: dict[str, dict[int, str]] = {}
    for field, (column, mapping) in FIELDS.items():
        by_label = defaultdict(list)
        for i, row in enumerate(meta):
            if category_of(row) and mapping.get(row[column]):
                by_label[mapping[row[column]]].append(i)
        chosen = {}
        for label, idx in by_label.items():
            rng.shuffle(idx)
            chosen.update({i: label for i in idx[:args.per_class]})
        picked[field] = chosen
        print(f"{field}: " + ", ".join(f"{l} {min(len(v), args.per_class)}장" for l, v in sorted(by_label.items())))

    all_idx = sorted(set().union(*[set(p) for p in picked.values()]))
    torch, model, processor, _, _ = tagger._load()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    t0 = time.perf_counter()
    emb = {}
    for s in range(0, len(all_idx), 64):
        batch = all_idx[s:s + 64]
        imgs = [ds[i]["image"].convert("RGB") for i in batch]
        with torch.no_grad():
            e = tagger._as_tensor(torch, model.get_image_features(**processor(images=imgs, return_tensors="pt").to(device)))
        e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()
        emb.update(zip(batch, e))
    print(f"사진 {len(all_idx)}장 벡터 계산: {time.perf_counter() - t0:.0f}초")

    out = {"model": tagger.MODEL_ID, "dataset": "ashraq/fashion-product-images-small", "fields": {}}
    for field, chosen in picked.items():
        idx = list(chosen)
        X = np.stack([emb[i] for i in idx])
        y = np.array([chosen[i] for i in idx])
        # 1) 5-fold 교차검증으로 성능 확인  2) 따로 떼어 둔 20%로 한 번 더 확인  3) 전체로 다시 학습해 저장
        cv = cross_val_score(LogisticRegression(max_iter=3000), X, y,
                             cv=StratifiedKFold(5, shuffle=True, random_state=args.seed)).mean()
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, stratify=y, random_state=args.seed)
        holdout = LogisticRegression(max_iter=3000).fit(Xtr, ytr).score(Xte, yte)
        clf = LogisticRegression(max_iter=3000).fit(X, y)
        out["fields"][field] = {
            "classes": clf.classes_.tolist(),
            "coef": np.round(clf.coef_, 5).tolist(),
            "intercept": np.round(clf.intercept_, 5).tolist(),
            "n_train": len(y), "cv_accuracy": round(cv, 4), "holdout_accuracy": round(holdout, 4),
        }
        print(f"{field}: 5-fold {cv:.1%}, 따로 뗀 20% {holdout:.1%} (학습 {len(y)}장, 라벨 {clf.classes_.tolist()})")

    path = tagger.probe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
