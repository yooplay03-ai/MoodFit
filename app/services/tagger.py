"""
tagger.py — 옷 사진을 보고 카테고리·계절·스타일·색상을 자동으로 채운다.

- 카테고리·계절·스타일: CLIP 제로샷 분류
  CLIP은 사진과 문장을 같은 벡터 공간에 두도록 학습된 모델이다. 사진 벡터를 "a photo of a t-shirt" 같은
  문장 벡터들과 비교해(코사인 유사도) 가장 가까운 문장의 라벨을 고른다. 따로 학습하지 않아도 된다.
  한 라벨에 여러 문장을 두고 그 문장 벡터들의 평균을 라벨 벡터로 쓴다(프롬프트 앙상블).
  확률을 문장별로 더하면 문장이 많은 라벨이 유리해지므로, 평균 벡터로 라벨끼리 공정하게 비교한다.
- 색상: k-means 군집화
  사진 가장자리 색을 배경으로 보고 빼낸 뒤, 남은 픽셀을 3개 색 무리로 묶어 가장 큰 무리를 옷 색으로 본다.

torch·transformers가 없으면 이 기능만 꺼지고 서버는 그대로 동작한다 (requirements-ml.txt).
"""

import io
import logging
import os
from functools import lru_cache

import numpy as np
from PIL import Image

log = logging.getLogger("moodfit")

MODEL_ID = os.environ.get("TAGGER_MODEL", "patrickjohncyh/fashion-clip")

# 라벨마다 영어 문장 여러 개 (CLIP은 영어 문장으로 학습됨)
CATEGORY_PROMPTS = {
    "상의": ["a t-shirt", "a shirt", "a sweater", "a hoodie", "a blouse", "a sweatshirt", "a knit top"],
    "하의": ["a pair of jeans", "a pair of trousers", "a pair of shorts", "a skirt", "a pair of track pants"],
    "아우터": ["a jacket", "a coat", "a blazer", "a padded jacket", "a cardigan", "a trench coat"],
    "신발": ["a pair of sneakers", "a pair of shoes", "a pair of boots", "a pair of sandals", "a pair of loafers"],
    "가방": ["a handbag", "a backpack", "a tote bag", "a shoulder bag"],
    "액세서리": ["a cap", "a hat", "a belt", "a watch", "a scarf", "a pair of sunglasses", "a necklace"],
}
SEASON_PROMPTS = {
    "여름": ["light summer clothing", "thin breathable clothing for hot weather"],
    "겨울": ["warm winter clothing", "thick padded clothing for cold weather"],
    "봄": ["light spring clothing"],
    "가을": ["autumn clothing", "fall layering clothing"],
}
STYLE_PROMPTS = {
    "캐주얼": ["casual everyday clothing"],
    "포멀": ["formal business clothing", "office formal wear"],
    "스트릿": ["streetwear", "oversized street fashion"],
    "미니멀": ["minimal simple clean clothing"],
    "빈티지": ["vintage retro clothing"],
    "스포티": ["sportswear", "athletic gym clothing"],
}

PALETTE = {
    "화이트": (248, 248, 246), "블랙": (24, 24, 24), "그레이": (140, 140, 140), "베이지": (222, 201, 168),
    "브라운": (122, 84, 52), "네이비": (32, 42, 82), "블루": (64, 112, 200), "스카이블루": (150, 196, 232),
    "그린": (70, 130, 82), "카키": (132, 128, 84), "레드": (190, 52, 52), "핑크": (232, 156, 184),
    "옐로우": (230, 202, 62), "오렌지": (222, 132, 52), "퍼플": (122, 82, 162),
}


class TaggerUnavailable(Exception):
    """torch/transformers가 설치되지 않았거나 모델을 불러오지 못했다."""


def nearest_color(rgb) -> str:
    r, g, b = rgb
    return min(PALETTE, key=lambda n: sum((c - p) ** 2 for c, p in zip((r, g, b), PALETTE[n])))


def kmeans(pixels: np.ndarray, k: int = 3, iters: int = 15, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """간단한 k-means. (중심 색들, 각 픽셀의 무리 번호)를 돌려준다."""
    rng = np.random.default_rng(seed)
    centers = pixels[rng.choice(len(pixels), size=min(k, len(pixels)), replace=False)].astype(float)
    for _ in range(iters):
        dist = ((pixels[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        labels = dist.argmin(1)
        new = np.array([pixels[labels == i].mean(0) if (labels == i).any() else centers[i] for i in range(len(centers))])
        if np.allclose(new, centers):
            break
        centers = new
    return centers, labels


def dominant_color(image: Image.Image) -> tuple[str, float]:
    """옷의 대표 색 이름과, 그 색이 옷 영역에서 차지하는 비율을 돌려준다."""
    img = np.asarray(image.convert("RGB").resize((64, 64)), dtype=float).reshape(64, 64, 3)
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    background = np.median(border, axis=0)
    pixels = img.reshape(-1, 3)
    # 배경색과 거의 같은 픽셀은 뺀다 (흰 이불·바닥 위에 펼쳐 찍은 사진 가정)
    keep = np.sqrt(((pixels - background) ** 2).sum(1)) > 40
    fg = pixels[keep] if keep.sum() > 200 else pixels
    centers, labels = kmeans(fg, k=3)
    counts = np.bincount(labels, minlength=len(centers))
    # 같은 색 이름으로 묶이는 무리(예: 진한 네이비, 옅은 네이비)는 합쳐서 비율을 센다
    by_name: dict[str, int] = {}
    for center, count in zip(centers, counts):
        name = nearest_color(center)
        by_name[name] = by_name.get(name, 0) + int(count)
    best = max(by_name, key=by_name.get)
    return best, by_name[best] / int(counts.sum())


def _as_tensor(torch, out):
    """transformers 버전에 따라 get_*_features가 텐서 또는 출력 객체를 돌려준다. 둘 다 받는다."""
    if torch.is_tensor(out):
        return out
    for name in ("image_embeds", "text_embeds", "pooler_output"):
        value = getattr(out, name, None)
        if value is not None:
            return value
    raise TypeError(f"알 수 없는 출력 형식: {type(out).__name__}")


def installed() -> bool:
    """모델을 불러오지 않고, 필요한 패키지가 설치돼 있는지만 확인한다."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


@lru_cache(maxsize=1)
def _load():
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except ImportError as e:
        raise TaggerUnavailable("torch, transformers가 설치되지 않았습니다 (pip install -r requirements-ml.txt)") from e
    model = CLIPModel.from_pretrained(MODEL_ID).eval()
    processor = CLIPProcessor.from_pretrained(MODEL_ID)

    def encode_texts(prompt_map):
        """라벨마다 문장 벡터 평균 → 라벨 벡터. (라벨 목록, [라벨 수 × 차원])"""
        labels, vectors = [], []
        for label, prompts in prompt_map.items():
            with torch.no_grad():
                inputs = processor(text=[f"a photo of {p}" for p in prompts], return_tensors="pt", padding=True)
                emb = _as_tensor(torch, model.get_text_features(**inputs))
            emb = emb / emb.norm(dim=-1, keepdim=True)
            mean = emb.mean(dim=0)
            labels.append(label)
            vectors.append(mean / mean.norm())
        return labels, torch.stack(vectors)

    text_banks = {name: encode_texts(m) for name, m in
                  (("category", CATEGORY_PROMPTS), ("season", SEASON_PROMPTS), ("style", STYLE_PROMPTS))}
    log.info("CLIP 모델 준비 완료: %s", MODEL_ID)
    return torch, model, processor, text_banks


def image_embedding(image: Image.Image):
    torch, model, processor, _ = _load()
    with torch.no_grad():
        inputs = processor(images=image.convert("RGB"), return_tensors="pt")
        emb = _as_tensor(torch, model.get_image_features(**inputs))
    return emb / emb.norm(dim=-1, keepdim=True)


def classify(image_emb, labels, label_emb, temperature: float = 100.0) -> tuple[str, float, dict]:
    """사진 벡터와 라벨 벡터의 코사인 유사도를 확률로 바꿔 가장 높은 라벨을 고른다."""
    probs = (temperature * image_emb @ label_emb.T).softmax(dim=-1)[0].tolist()
    by_label = dict(zip(labels, probs))
    best = max(by_label, key=by_label.get)
    return best, by_label[best], {k: round(v, 3) for k, v in sorted(by_label.items(), key=lambda x: -x[1])}


def analyze(photo_bytes: bytes) -> dict:
    image = Image.open(io.BytesIO(photo_bytes))
    _, _, _, banks = _load()
    emb = image_embedding(image)
    result = {}
    for field in ("category", "season", "style"):
        label, conf, scores = classify(emb, *banks[field])
        result[field] = {"value": label, "confidence": round(conf, 3), "scores": scores}
    color, share = dominant_color(image)
    result["color"] = {"value": color, "confidence": round(share, 3)}
    return result

