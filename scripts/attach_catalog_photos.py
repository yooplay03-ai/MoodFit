"""
attach_catalog_photos.py — 사진 없이 등록된 옷에 비슷한 기본 옷 사진(예시 사진)을 붙인다.

데모 계정을 만들 때 넣은 옷 12벌처럼 사진이 없는 옷은 그림으로 보이는데, 실제 사진들 사이에 섞이면 어색하다.
종류가 같고 색이 같거나 가까운 기본 옷 사진이 있으면 그 사진을 붙이고 "예시 사진"으로 표시한다.
맞는 사진이 없는 옷은 그대로 둔다(지우지 않는다).

실행:  python scripts/attach_catalog_photos.py            (무엇이 바뀔지 보여 주기만)
       python scripts/attach_catalog_photos.py --apply    (실제로 붙이기)
"""

import argparse
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ClothingItem  # noqa: E402
from app.services import catalog  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    attached, left = 0, []
    with SessionLocal() as db:
        for item in db.scalars(select(ClothingItem).where(ClothingItem.photo.is_(None))):
            e = catalog.match_photo(item.category, item.color, item.name)
            label = item.name or f"{item.color} {item.category}"
            if e is None:
                left.append(label)
                continue
            print(f"  {label:<16} ← {e['name']} 사진")
            attached += 1
            if args.apply:
                filename = f"{uuid.uuid4().hex}.jpg"
                shutil.copyfile(catalog.local_path(e["id"]), config.MEDIA_DIR / filename)
                item.photo = filename
                item.source = f"catalog:{e['id']}"
                if not item.occasion_fit and e.get("occasion_fit"):
                    import json
                    item.occasion_fit = json.dumps(e["occasion_fit"], ensure_ascii=False)
        if args.apply:
            db.commit()
    print(f"\n사진을 붙일 수 있는 옷 {attached}벌" + ("" if args.apply else " (아직 바꾸지 않았어요. --apply를 붙여 실행하면 바뀌어요)"))
    if left:
        print(f"맞는 사진이 없어 그대로 둘 옷 {len(left)}벌: {', '.join(left)}")


if __name__ == "__main__":
    main()
