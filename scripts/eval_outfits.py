"""
eval_outfits.py — 추천 점수가 사람의 평가(좋아요/별로)와 맞는지 잰다.

화면의 "🎲 무작위 코디 평가하기"로 모은 평가를 쓴다. 무작위 코디는 점수와 상관없이 골랐으므로,
"점수가 높은 코디일수록 좋아요를 받았는가"를 치우침 없이 볼 수 있다. (추천 결과에 남긴 평가는 이미 점수가 높은 것만
모여 있어 따로 표시만 한다.)

지표: AUC — 좋아요 1개와 별로 1개를 뽑았을 때 좋아요 쪽 점수가 더 높을 확률. 0.5면 동전 던지기와 같다.

비교하는 것
  - 전체 점수 (지금 앱이 쓰는 가중치)
  - 항목 하나씩만 쓴 경우 / 항목 하나씩 뺀 경우  → 어떤 항목이 실제로 도움이 되는지
  - 평가로 가중치를 학습한 경우 (로지스틱 회귀, 반복 층화 교차검증) → 가중치를 내 취향에 맞추면 얼마나 나아지는지

실행:  python scripts/eval_outfits.py            (기본 moodfit.db, 모든 사용자)
       python scripts/eval_outfits.py --email 내이메일
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import ClothingItem, OutfitFeedback, User  # noqa: E402
from app.services import outfits as engine  # noqa: E402

KEYS = list(engine.WEIGHTS)


def auc(scores, labels) -> float:
    """Mann-Whitney U로 계산한 AUC (동점은 0.5)"""
    pos = [s for s, y in zip(scores, labels) if y > 0]
    neg = [s for s, y in zip(scores, labels) if y < 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def fit_logistic(X, y, l2=1.0, steps=2000, lr=0.1):
    """아주 작은 로지스틱 회귀 (평가가 수십 개뿐이라 L2로 과적합을 줄인다)"""
    w, b = np.zeros(X.shape[1]), 0.0
    t = (y > 0).astype(float)
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        w -= lr * (X.T @ (p - t) / len(t) + l2 * w / len(t))
        b -= lr * (p - t).mean()
    return w, b


def cv_auc(X, y, folds: int = 3, repeats: int = 30, seed: int = 0) -> float:
    """반복 층화 k-겹 교차검증: 좋아요/별로 비율을 유지한 채 나눠, 학습에 안 쓴 평가로만 AUC를 재고 평균낸다.
    처음엔 leave-one-out을 썼는데, 평가가 수십 개이고 좋아요가 적으면 하나를 뺄 때마다 절편이 반대로 움직여
    AUC가 0.09처럼 비정상적으로 낮게 나왔다(LOO의 알려진 편향). 그래서 폴드마다 AUC를 재는 방식으로 바꿨다."""
    rng = np.random.default_rng(seed)
    pos, neg = np.where(y > 0)[0], np.where(y < 0)[0]
    scores = []
    for _ in range(repeats):
        p_split = np.array_split(rng.permutation(pos), folds)
        n_split = np.array_split(rng.permutation(neg), folds)
        for k in range(folds):
            test = np.concatenate([p_split[k], n_split[k]])
            train = np.setdiff1d(np.arange(len(y)), test)
            w, b = fit_logistic(X[train], y[train])
            a = auc(list(X[test] @ w + b), list(y[test]))
            if a == a:                                   # nan(한쪽 라벨만 있는 폴드)은 건너뜀
                scores.append(a)
    return float(np.mean(scores))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email")
    ap.add_argument("--all-sources", action="store_true", help="추천 결과에 남긴 평가도 포함")
    args = ap.parse_args()

    with SessionLocal() as db:
        q = select(OutfitFeedback)
        if args.email:
            user = db.scalar(select(User).where(User.email == args.email))
            if user is None:
                sys.exit(f"{args.email} 사용자가 없습니다.")
            q = q.where(OutfitFeedback.user_id == user.id)
        rows = list(db.scalars(q))
        items = {it.id: it for it in db.scalars(select(ClothingItem))}

    by_source = {}
    for r in rows:
        by_source.setdefault(r.source, [0, 0])[0 if r.rating > 0 else 1] += 1
    print("모인 평가:", ", ".join(f"{s} 좋아요 {a} / 별로 {b}" for s, (a, b) in by_source.items()) or "없음")

    use = [r for r in rows if args.all_sources or r.source == "random"]
    parts, labels, skipped = [], [], 0
    now = datetime.now(timezone.utc)
    for r in use:
        ids = json.loads(r.item_ids)
        if any(i not in items for i in ids.values()):
            skipped += 1                      # 그 사이에 삭제한 옷이 있는 평가
            continue
        slots = {s: items[i] for s, i in ids.items()}
        o = engine.score_outfit(slots, temp=r.temp, occasions=[x for x in r.occasions.split(",") if x],
                                mood=r.mood, style=r.style, now=now)
        o.parts["fresh"] = 1.0                # '최근 착용'은 평가한 날 기준이라 지금 다시 계산하면 의미가 없다
        parts.append([o.parts[k] for k in KEYS])
        labels.append(r.rating)
    if skipped:
        print(f"삭제된 옷이 들어 있는 평가 {skipped}개는 뺐습니다.")
    X, y = np.array(parts), np.array(labels)
    n_pos, n_neg = int((y > 0).sum()), int((y < 0).sum())
    print(f"분석에 쓴 평가: {len(y)}개 (좋아요 {n_pos} / 별로 {n_neg})\n")
    if n_pos < 3 or n_neg < 3:
        sys.exit("좋아요와 별로가 각각 3개 이상 있어야 잴 수 있어요. 화면의 '🎲 무작위 코디 평가하기'로 더 모아 주세요.")

    weights = np.array([engine.WEIGHTS[k] for k in KEYS], dtype=float)
    rows_out = [("전체 점수 (지금 앱)", auc(X @ weights, y))]
    for i, k in enumerate(KEYS):
        if k == "fresh":
            continue
        rows_out.append((f"{engine.COMPONENT_LABEL[k]}만", auc(X[:, i], y)))
    for i, k in enumerate(KEYS):
        if k == "fresh":
            continue
        w = weights.copy()
        w[i] = 0
        rows_out.append((f"{engine.COMPONENT_LABEL[k]} 빼고", auc(X @ w, y)))
    rows_out.append(("평가로 가중치 학습 (3겹 교차검증 × 30번 평균)", cv_auc(X, y)))

    print("| 방법 | AUC |\n|---|---|")
    for name, v in rows_out:
        print(f"| {name} | {v:.3f} |")
    w, _ = fit_logistic(X, y)
    print("\n전체 평가로 학습한 가중치(클수록 좋아요에 영향):",
          ", ".join(f"{engine.COMPONENT_LABEL[k]} {v:+.2f}" for k, v in zip(KEYS, w) if k != "fresh"))
    print("\n※ 평가가 수십 개 수준이면 AUC가 0.05~0.1씩 흔들릴 수 있어요. 숫자는 방향을 보는 용도로 쓰세요.")


if __name__ == "__main__":
    main()
