"""
fetch.py — 사용자가 준 주소에서 페이지·사진을 안전하게 받아 온다.

사용자가 아무 주소나 넣을 수 있으므로 그대로 받으면 서버가 내부망(127.0.0.1, 192.168.x.x, 클라우드 메타데이터 주소 등)에
대신 접속하게 만들 수 있다(SSRF). 그래서
  - http/https 주소만 받고
  - 주소의 IP를 직접 확인해 사설·루프백·링크로컬 등 내부 주소면 거절하고
  - 리다이렉트는 자동으로 따라가지 않고 한 번씩 같은 검사를 다시 하며
  - 받는 크기에 상한을 둔다.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 MoodFit-link-preview")
MAX_REDIRECTS = 3


class FetchError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 실패 이유"""


def check_public(url: str, allowed_hosts: set[str] | None = None) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("http 또는 https로 시작하는 주소만 쓸 수 있어요.")
    host = parts.hostname
    if allowed_hosts is not None and host not in allowed_hosts:
        raise FetchError("허용되지 않은 주소예요.")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise FetchError("주소를 찾을 수 없어요. 링크를 다시 확인해 주세요.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("내부 네트워크 주소에는 접속할 수 없어요.")


def fetch(url: str, *, max_bytes: int, allowed_hosts: set[str] | None = None, timeout: float = 10.0) -> httpx.Response:
    """검사를 통과한 주소만 받는다. 응답 본문은 max_bytes를 넘으면 실패."""
    for _ in range(MAX_REDIRECTS + 1):
        check_public(url, allowed_hosts)
        try:
            with httpx.Client(follow_redirects=False, timeout=timeout,
                              headers={"User-Agent": BROWSER_UA, "Accept-Language": "ko-KR,ko;q=0.9"}) as client:
                with client.stream("GET", url) as res:
                    if res.is_redirect and res.headers.get("location"):
                        url = urljoin(url, res.headers["location"])
                        continue
                    if res.status_code >= 400:
                        raise FetchError(f"페이지를 열지 못했어요 (HTTP {res.status_code}). 쇼핑몰이 자동 접속을 막았을 수 있어요.")
                    body = b""
                    for chunk in res.iter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FetchError("페이지나 사진이 너무 커요.")
                    res._content = body
                    return res
        except httpx.HTTPError as e:
            raise FetchError(f"연결하지 못했어요 ({type(e).__name__}).") from e
    raise FetchError("리다이렉트가 너무 많아요.")
