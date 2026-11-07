"""평가 스크립트의 지표 계산"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_auc_and_cv():
    from eval_outfits import auc, cv_auc
    assert auc([3, 2, 1, 0], [1, 1, -1, -1]) == 1.0
    assert auc([0, 1, 2, 3], [1, 1, -1, -1]) == 0.0
    assert auc([1, 1], [1, -1]) == 0.5
    rng = np.random.default_rng(0)
    X = rng.random((30, 3))
    y = np.where(X[:, 0] > 0.5, 1, -1)           # 첫 번째 항목만 취향을 가른다
    assert cv_auc(X, y) > 0.8
