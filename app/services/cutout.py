"""
cutout.py — 옷 사진의 배경을 지우고, 밝은 단색 정사각형 배경 위에 옷만 올린 사진을 만든다.

집에서 찍은 사진에는 바닥·손·옷걸이가 함께 찍혀 옷장 화면이 어수선해 보인다.
rembg(U²-Net 기반 배경 제거 모델, CPU로 사진 1장에 1초 안팎)로 옷만 오려 쇼핑몰 상품 사진처럼 맞춘다.
원본 사진은 그대로 두고 '<원본이름>_cut.jpg'로 따로 저장한다. AI 분류는 원본으로 한다(평가를 원본으로 했으므로).

rembg가 설치되지 않았으면 None을 돌려주고, 화면은 원본 사진을 쓴다.
"""

import io
import logging
from functools import lru_cache

from PIL import Image

log = logging.getLogger("moodfit")

BACKGROUND = (244, 242, 237)   # 화면의 카드 배경색(--surface)과 같은 색
MARGIN = 1.12                  # 옷 둘레 여백


@lru_cache(maxsize=1)
def _session():
    from rembg import new_session      # 없으면 ImportError
    return new_session("u2net")


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec("rembg") is not None


def make_cutout(photo_bytes: bytes, size: int = 800) -> bytes | None:
    try:
        from rembg import remove
        session = _session()
    except Exception as e:                   # 설치 안 됨, 모델 다운로드 실패 등
        log.info("배경 제거를 건너뜁니다: %s", e)
        return None
    image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    image.thumbnail((size, size))
    cut = remove(image, session=session, post_process_mask=True)
    # 옷이 있는 영역만 남기고(투명도 기준), 정사각형 가운데에 놓는다
    bbox = cut.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    if bbox is None:                         # 옷을 못 찾았으면 원본을 쓴다
        return None
    cut = cut.crop(bbox)
    side = int(max(cut.size) * MARGIN)
    canvas = Image.new("RGB", (side, side), BACKGROUND)
    canvas.paste(cut, ((side - cut.width) // 2, (side - cut.height) // 2), cut)
    canvas.thumbnail((size, size))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    return out.getvalue()
