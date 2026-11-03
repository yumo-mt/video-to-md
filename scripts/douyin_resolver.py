#!/usr/bin/env python3
"""抖音帖子解析：分享链接 → 结构化帖子信息。

解析策略（逐级降级）：
  1. 传统 SSR（window._ROUTER_DATA）—— 2026 年抖音已基本剥离，通常失败，仅作快速尝试
  2. 浏览器方案：Playwright 驱动系统 Chrome 建立会话 → 打开帖子页预热 → 调用
     aweme/detail 接口（无需签名），成功率高；接口偶发返回空 body 时自动刷新重试
  3. DOM 兜底：从页面标题与 <video> 元素取标题/直链

输出统一结构：
  {"video_id","type":"video|note","title","author","duration_ms","url","play_url","images",
   "cover_url","source_url","degraded"}
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DESKTOP_UA, MOBILE_UA, load as load_config  # noqa: E402

URL_RE = re.compile(r"https?://\S+")
ID_RE = re.compile(r"/(?:video|note|share/video|share/note)/(\d{15,25})")

DETAIL_JS = """async (vid) => {
    const p = new URLSearchParams({
        device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
        aweme_id: vid, pc_client_type: '1', version_code: '170400',
        version_name: '17.4.0', cookie_enabled: 'true', platform: 'PC', downlink: '10'
    });
    const r = await fetch('/aweme/v1/web/aweme/detail/?' + p,
        {headers: {'Accept': 'application/json'}});
    const t = await r.text();
    return {status: r.status, body: t};
}"""


def extract_url(text: str) -> str:
    m = URL_RE.search(text or "")
    if not m:
        raise ValueError("未找到有效的分享链接")
    url = m.group(0).rstrip("，。,.；;！!）)")
    # 去掉分享文案里附带的查询串噪声，保留原始链接即可
    return url


def parse_share_url(text: str, timeout: int = 25) -> tuple[str, str]:
    """跟随短链跳转，返回 (aweme_id, kind)。kind ∈ {video, note}"""
    url = extract_url(text)
    parsed = urlsplit(url)
    if parsed.hostname in {"douyin.com", "www.douyin.com"}:
        modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
        if modal_id.isdigit() and 15 <= len(modal_id) <= 25:
            return modal_id, "video"
    m = ID_RE.search(url.split("?")[0])
    if m:
        kind = "note" if "note" in url else "video"
        return m.group(1), kind
    r = requests.get(url, headers={"User-Agent": MOBILE_UA}, timeout=timeout, allow_redirects=True)
    final = r.url
    m = ID_RE.search(final)
    if not m:
        raise ValueError(f"无法从链接解析帖子 ID：{final}")
    kind = "note" if "/note/" in final else "video"
    return m.group(1), kind


def _build(aweme: dict, kind: str, share_url: str) -> dict:
    video = aweme.get("video") or {}
    images = aweme.get("images") or []
    play = ""
    for u in (video.get("play_addr") or {}).get("url_list") or []:
        if "douyin" in u:
            play = u
            break
    if not play:
        urls = (video.get("play_addr") or {}).get("url_list") or []
        play = urls[0] if urls else ""
    covers = (video.get("cover") or {}).get("url_list") or []
    info = {
        "video_id": aweme.get("aweme_id", ""),
        "type": "note" if images else "video",
        "title": (aweme.get("desc") or "").strip(),
        "author": (aweme.get("author") or {}).get("nickname", ""),
        "author_id": (aweme.get("author") or {}).get("sec_uid", ""),
        "duration_ms": aweme.get("duration") or 0,
        "play_url": "" if images else play,
        "images": [i["url_list"][0] for i in images if i.get("url_list")],
        "cover_url": covers[0] if covers else "",
        "create_time": aweme.get("create_time") or 0,
        "source_url": share_url,
        "degraded": True,
    }
    if not info["title"]:
        info["title"] = f"douyin_{info['video_id']}"
    return info


def _browser_lookup(video_id: str, kind: str, share_url: str, attempts: int = 3) -> dict:
    """打开帖子页，拦截页面自身发出的 aweme/detail 响应取数据。

    抖音网页端的详情接口需要 JS 签名（a_bogus），外部无法直接构造；但页面加载时
    自己会带着签名请求一次，因此监听响应即可拿到完整 aweme_detail，无需破解签名。
    """
    from playwright.sync_api import sync_playwright

    page_url = f"https://www.douyin.com/{'note' if kind == 'note' else 'video'}/{video_id}"
    last_err = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel=load_config().get("browser_channel", "chrome"),
            headless=True,
            args=["--headless=new", "--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        ctx = browser.new_context(user_agent=DESKTOP_UA, locale="zh-CN",
                                  viewport={"width": 1280, "height": 720})
        ctx.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        page = ctx.new_page()
        captured: dict = {}

        def on_response(resp):
            if captured or "aweme/detail" not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return
            aweme = data.get("aweme_detail")
            if aweme:
                captured["aweme"] = aweme
            elif data.get("filter_detail"):
                captured["filter"] = data["filter_detail"]

        page.on("response", on_response)
        try:
            for attempt in range(1, attempts + 1):
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    last_err = str(e)
                # 详情接口通常在 1~4s 内返回，轮询等待
                for _ in range(20):
                    if captured:
                        break
                    page.wait_for_timeout(500)

                if captured.get("aweme"):
                    return _build(captured["aweme"], kind, share_url)
                if captured.get("filter"):
                    last_err = f"接口无详情（{captured['filter'].get('filter_reason') or '未知'}）"
                else:
                    last_err = "未捕获到详情接口响应（可能被风控或帖子不可见）"

                # 主动调用一次接口作为兜底（部分帖子页面不会自动请求）
                try:
                    res = page.evaluate(DETAIL_JS, video_id)
                    body = (res or {}).get("body") or ""
                    if body and '"aweme_detail"' in body:
                        aweme = (json.loads(body) or {}).get("aweme_detail")
                        if aweme:
                            return _build(aweme, kind, share_url)
                except Exception:
                    pass

                page.wait_for_timeout(2000 * attempt)
                captured.clear()

            # DOM 兜底：标题 + <video> 直链
            title = (page.title() or "").replace(" - 抖音", "").strip()
            src = page.evaluate(
                "() => { const v=document.querySelector('video');"
                " return v ? (v.currentSrc || v.src || '') : ''; }")
            if title and src.startswith("http") and kind != "note":
                return {
                    "video_id": video_id, "type": kind, "title": title, "author": "",
                    "duration_ms": 0, "play_url": src, "images": [],
                    "cover_url": "", "create_time": 0, "source_url": share_url,
                    "degraded": True, "fallback": "dom",
                }
            raise RuntimeError(f"浏览器方案失败：{last_err}")
        finally:
            browser.close()


def _try_ssr(video_id: str, kind: str, share_url: str) -> dict | None:
    """传统 SSR 解析，失败返回 None。"""
    path = "note" if kind == "note" else "video"
    url = f"https://www.douyin.com/share/{path}/{video_id}"
    try:
        r = requests.get(url, headers={"User-Agent": MOBILE_UA}, timeout=20)
        m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", r.text, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(1).strip())
        loader = data.get("loaderData") or {}
        page = loader.get("video_(id)/page") or loader.get("note_(id)/page") or {}
        item_list = ((page.get("videoInfoRes") or {}).get("item_list") or [])
        if not item_list:
            return None
        return _build(item_list[0], kind, share_url)
    except Exception:
        return None


def resolve(share_text: str, use_ssr: bool = True) -> dict:
    """分享链接/分享文案 → 帖子信息 dict"""
    video_id, kind = parse_share_url(share_text)
    source_url = f"https://www.douyin.com/{kind}/{video_id}"
    if use_ssr:
        info = _try_ssr(video_id, kind, source_url)
        if info:
            info["degraded"] = False
            return info
    return _browser_lookup(video_id, kind, source_url)


def main():
    import argparse

    ap = argparse.ArgumentParser(description="解析抖音分享链接，输出帖子信息 JSON")
    ap.add_argument("link", help="分享链接或包含链接的分享文案")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    t0 = time.time()
    info = resolve(args.link)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    print(f"类型: {'图文' if info['type'] == 'note' else '视频'}")
    print(f"ID: {info['video_id']}")
    print(f"标题: {info['title']}")
    print(f"作者: {info['author']}")
    if info["type"] == "video":
        print(f"时长: {info['duration_ms'] / 1000:.0f}s")
        print(f"直链: {info['play_url'][:100]}...")
    else:
        print(f"图片: {len(info['images'])} 张")
    print(f"解析方式: {'浏览器' if info.get('degraded') else 'SSR'}  耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
