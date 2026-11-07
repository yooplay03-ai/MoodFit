"""
product_link.py — 상품 페이지 주소 하나에서 대표 사진과 상품명을 읽는다.

쇼핑몰은 카카오톡·인스타그램에 링크를 공유할 때 미리보기가 뜨도록 페이지에 og:image, og:title을 넣어 둔다.
그 값만 읽는다. 사용자가 링크를 넣을 때마다 그 한 페이지만 받으므로 사이트를 통째로 긁는 크롤링과는 다르다.
가져온 사진은 그 사용자의 옷장에서만 보이고, 저작권은 원래 쇼핑몰에 있다.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.services.fetch import FetchError, fetch

MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
# 상품명 앞뒤에 붙는 쇼핑몰 이름·홍보 문구를 덜어 낸다
SITE_SUFFIX = re.compile(r"\s*[|\-–—:]\s*(무신사|MUSINSA|29CM|지그재그|ZIGZAG|에이블리|W컨셉|SSF|쿠팡|네이버).*$", re.I)
BRACKETS = re.compile(r"^\s*[\[(【][^\])】]{1,20}[\])】]\s*")


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:image", "og:image:secure_url", "twitter:image", "og:title", "twitter:title") and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def clean_title(title: str) -> str:
    title = html.unescape(title).strip()
    title = SITE_SUFFIX.sub("", title)
    title = BRACKETS.sub("", title)
    return re.sub(r"\s+", " ", title).strip()[:50]


def parse_page(page_html: str, base_url: str) -> tuple[str | None, str]:
    p = MetaParser()
    p.feed(page_html)
    image = p.meta.get("og:image:secure_url") or p.meta.get("og:image") or p.meta.get("twitter:image")
    title = p.meta.get("og:title") or p.meta.get("twitter:title") or p.title
    return (urljoin(base_url, image) if image else None), clean_title(title or "")


def read_product(url: str) -> dict:
    """{"title": 상품명, "image": 사진 바이트, "content_type": ...}"""
    page = fetch(url, max_bytes=MAX_PAGE_BYTES)
    image_url, title = parse_page(page.text, str(page.url))
    if not image_url:
        raise FetchError("이 페이지에서 상품 사진을 찾지 못했어요. 상품 상세 페이지 주소인지 확인해 주세요.")
    img = fetch(image_url, max_bytes=MAX_IMAGE_BYTES)
    ctype = img.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in IMAGE_TYPES:
        raise FetchError("상품 사진 형식을 읽을 수 없어요 (JPG·PNG·WEBP만 가능).")
    return {"title": title, "image": img.content, "content_type": ctype, "image_url": image_url}
