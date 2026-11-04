"""
tagger.py — 옷 사진을 보고 카테고리·계절·스타일·색상을 자동으로 채운다.

- 카테고리·계절·스타일: CLIP 제로샷 분류
  CLIP은 사진과 문장을 같은 벡터 공간에 두도록 학습된 모델이다. 사진 벡터를 "a photo of a t-shirt" 같은
  문장 벡터들과 비교해(코사인 유사도) 가장 가까운 문장의 라벨을 고른다. 따로 학습하지 않아도 된다.
  한 라벨에 여러 문장을 두고 그 문장 벡터들의 평균을 라벨 벡터로 쓴다(프롬프트 앙상블).
  확률을 문장별로 더하면 문장이 많은 라벨이 유리해지므로, 평균 벡터로 라벨끼리 공정하게 비교한다.
- 스타일: 제로샷은 56%라, CLIP 사진 벡터 위에 학습한 로지스틱 회귀(app/data/probe_*.json)를 쓴다.
- 계절: 상품 데이터의 계절 라벨(인도 쇼핑몰 기준)로 학습한 분류기는 실제 옷장 사진에서 대부분 '여름'으로
  틀렸다. 그래서 라벨 대신 눈에 보이는 '두께·소재'(두꺼운 패딩/울 니트 ↔ 얇은 반팔)를 CLIP으로 고르고,
  그걸 계절로 바꾼다. 가방·액세서리는 계절을 타지 않으므로 사계절로 둔다.
- 색상: CLIP 제로샷 ("a photo of white clothing" …)
  처음에는 k-means(가장자리를 배경으로 보고 빼낸 뒤 가장 큰 색 무리)를 썼지만, 실제 사진은 책상·벽·손·옷걸이가
  함께 찍혀 배경 가정이 깨졌다. CLIP은 사진 속 옷에 주목하므로 배경에 덜 흔들린다. k-means는 비교·대체용으로 남겼다.

torch·transformers가 없으면 이 기능만 꺼지고 서버는 그대로 동작한다 (requirements-ml.txt).
"""

import io
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger("moodfit")

MODEL_ID = os.environ.get("TAGGER_MODEL", "patrickjohncyh/fashion-clip")
PROBE_DIR = Path(__file__).resolve().parents[1] / "data"


def probe_path(model_id: str = None) -> Path:
    return PROBE_DIR / f"probe_{(model_id or MODEL_ID).split('/')[-1]}.json"

# 라벨마다 영어 문장 여러 개 (CLIP은 영어 문장으로 학습됨)
CATEGORY_PROMPTS = {
    "상의": ["a t-shirt", "a shirt", "a sweater", "a hoodie", "a blouse", "a sweatshirt", "a knit top"],
    "하의": ["a pair of jeans", "a pair of trousers", "a pair of shorts", "a skirt", "a pair of track pants"],
    "아우터": ["a jacket", "a coat", "a blazer", "a padded jacket", "a cardigan", "a trench coat"],
    "신발": ["a pair of sneakers", "a pair of shoes", "a pair of boots", "a pair of sandals", "a pair of loafers"],
    "가방": ["a handbag", "a backpack", "a tote bag", "a shoulder bag"],
    "액세서리": ["a cap", "a hat", "a belt", "a watch", "a scarf", "a pair of sunglasses", "a necklace"],
}
# 계절은 '두께·소재'로 판단한다 (봄·가을은 추천 점수에서 같은 간절기로 취급하므로 '가을' 하나로 둔다)
THICKNESS_PROMPTS = {
    "겨울": ["a thick padded puffer jacket", "a heavy wool knit sweater", "a warm fleece jacket",
            "a thick wool coat", "thick warm winter clothing"],
    "가을": ["a light jacket", "a cotton sweatshirt", "a hoodie", "a cardigan", "a long-sleeve shirt",
            "a pair of jeans", "a corduroy skirt"],
    "여름": ["a thin short-sleeve t-shirt", "a sleeveless top", "a pair of shorts", "a thin linen shirt",
            "light thin summer clothing"],
}
SEASONLESS = {"가방", "액세서리"}
USE_PROBE = {"style"}        # 학습한 분류기를 쓰는 항목 (계절 분류기는 실제 사진에서 틀려 쓰지 않음)

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
    "아이보리": (238, 232, 212), "라벤더": (190, 170, 220), "민트": (160, 215, 195), "와인": (110, 32, 48),
    "차콜": (62, 62, 66),
}
COLOR_EN = {
    "화이트": "white", "블랙": "black", "그레이": "gray", "베이지": "beige", "브라운": "brown",
    "네이비": "navy blue", "블루": "blue", "스카이블루": "light sky blue", "그린": "green", "카키": "khaki olive",
    "레드": "red", "핑크": "pink", "옐로우": "yellow", "오렌지": "orange", "퍼플": "purple",
    "아이보리": "ivory cream", "라벤더": "lavender", "민트": "mint", "와인": "burgundy wine", "차콜": "charcoal gray",
}
COLOR_PROMPTS = {ko: [f"{en} clothing", f"a {en} garment"] for ko, en in COLOR_EN.items()}


class TaggerUnavailable(Exception):
    """torch/transformers가 설치되지 않았거나 모델을 불러오지 못했다."""


def rgb_to_lab(rgb) -> np.ndarray:
    """sRGB(0~255) → CIE Lab. Lab에서의 거리는 사람이 느끼는 색 차이에 더 가깝다."""
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


PALETTE_LAB = {name: rgb_to_lab(rgb) for name, rgb in PALETTE.items()}


def nearest_color(rgb, space: str = "lab") -> str:
    if space == "rgb":       # 예전 방식 (평가 비교용)
        return min(PALETTE, key=lambda n: sum((c - p) ** 2 for c, p in zip(rgb, PALETTE[n])))
    lab = rgb_to_lab(rgb)
    return min(PALETTE_LAB, key=lambda n: float(((lab - PALETTE_LAB[n]) ** 2).sum()))


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


def dominant_color(image: Image.Image, space: str = "lab") -> tuple[str, float]:
    """옷의 대표 색 이름과, 그 색이 옷 영역에서 차지하는 비율을 돌려준다.
    space="rgb"는 예전 방식(RGB 거리로 배경 제거·색 이름 매칭), 평가에서 비교용으로만 쓴다."""
    img = np.asarray(image.convert("RGB").resize((64, 64)), dtype=float).reshape(64, 64, 3)
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    background = np.median(border, axis=0)
    pixels = img.reshape(-1, 3)
    if space == "rgb":
        keep = np.sqrt(((pixels - background) ** 2).sum(1)) > 40
        fg = pixels[keep] if keep.sum() > 200 else pixels
    else:
        # 배경과 Lab 거리가 가까운 픽셀은 뺀다. 흰 배경 위 흰 옷처럼 거의 다 지워지면(옷 영역 8% 미만)
        # 배경을 빼지 않고 사진 가운데 부분을 그대로 쓴다.
        diff = np.sqrt(((rgb_to_lab(pixels) - rgb_to_lab(background)) ** 2).sum(1))
        keep = diff > 12
        center = img[16:48, 16:48].reshape(-1, 3)
        fg = pixels[keep] if keep.mean() > 0.08 else center
    centers, labels = kmeans(fg, k=3)
    counts = np.bincount(labels, minlength=len(centers))
    # 같은 색 이름으로 묶이는 무리(예: 진한 네이비, 옅은 네이비)는 합쳐서 비율을 센다
    by_name: dict[str, int] = {}
    for center, count in zip(centers, counts):
        name = nearest_color(center, space)
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
                  (("category", CATEGORY_PROMPTS), ("season", THICKNESS_PROMPTS), ("style", STYLE_PROMPTS),
                   ("color", COLOR_PROMPTS))}
    probes = load_probes(torch)
    log.info("CLIP 모델 준비 완료: %s (학습한 분류기: %s)", MODEL_ID, ", ".join(probes) or "없음")
    return torch, model, processor, text_banks, probes


def load_probes(torch) -> dict:
    """scripts/train_probe.py가 저장한 로지스틱 회귀 가중치. {항목: (라벨, W, b)}"""
    path = probe_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {field: (p["classes"], torch.tensor(p["coef"], dtype=torch.float32), torch.tensor(p["intercept"], dtype=torch.float32))
            for field, p in data["fields"].items() if field in USE_PROBE}


def image_embedding(image: Image.Image):
    torch, model, processor, _, _ = _load()
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


def classify_probe(image_emb, labels, coef, intercept) -> tuple[str, float, dict]:
    """학습한 로지스틱 회귀: 확률 = softmax(사진 벡터 · W + b)"""
    probs = (image_emb @ coef.T + intercept).softmax(dim=-1)[0].tolist()
    by_label = dict(zip(labels, probs))
    best = max(by_label, key=by_label.get)
    return best, by_label[best], {k: round(v, 3) for k, v in sorted(by_label.items(), key=lambda x: -x[1])}


def analyze(photo_bytes: bytes) -> dict:
    image = Image.open(io.BytesIO(photo_bytes))
    _, _, _, banks, probes = _load()
    emb = image_embedding(image)
    result = {}
    for field in ("category", "season", "style", "color"):
        if field in probes:
            label, conf, scores = classify_probe(emb, *probes[field])
            method = "linear-probe"
        else:
            label, conf, scores = classify(emb, *banks[field])
            method = "zero-shot"
        result[field] = {"value": label, "confidence": round(conf, 3), "scores": scores, "method": method}
    if result["category"]["value"] in SEASONLESS:
        result["season"] = {"value": "사계절", "confidence": 1.0, "scores": {}, "method": "rule"}
    kmeans_color, share = dominant_color(image)
    result["color"]["kmeans"] = kmeans_color          # 비교용으로 함께 돌려준다
    return result
