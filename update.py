#!/usr/bin/env python3
"""消費者庁の特定商取引法の行政処分（通信販売業者）から、ブロックするサイトの一覧を作る。

使い方:
    python update.py              # 毎日の更新（RSS ＋ 今年度・前年度の一覧ページ）
    python update.py --backfill   # 初回だけ。過去の年度の一覧ページも読む
    python update.py --dry-run    # 書き込まずに結果だけ表示

依存: Python 3 標準ライブラリ ＋ pypdf（暗号化 PDF を読むので pypdf[crypto]）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKLIST = HERE / "blocklist.json"
CACHE_DIR = HERE / ".cache"

BASE = "https://www.caa.go.jp"
RSS_URL = BASE + "/news.rss"
LISTING_URL = BASE + "/policies/policy/consumer_transaction/release/"
USER_AGENT = (
    "MimamoriBlocklist/1.0 (blocklist for an elderly-safe browser; reads CAA disposal notices at most 1 req/sec)"
)
REQUEST_INTERVAL = 1.0  # 秒
MAX_NEW_HOSTS = 25
BACKFILL_YEARS = 4  # 年度の一覧をいくつさかのぼるか（今年度を除く）

# 売り手が店のページを持っていただけの大きな場。場ごと止めないで skipped に記録する。
# 大きな場：本体もその下（お店のページ）も止めない。お店は場の一部で、場ごと止めることになるため
PLATFORMS = {
    "amazon.co.jp", "amazon.com", "rakuten.co.jp", "rakuten.ne.jp", "yahoo.co.jp",
    "yahoo.com", "google.com", "google.co.jp", "apple.com", "instagram.com",
    "facebook.com", "fb.com", "x.com", "twitter.com", "line.me", "lin.ee",
    "youtube.com", "youtu.be", "tiktok.com", "mercari.com", "jp.mercari.com",
    "paypay.ne.jp", "paypaymall.yahoo.co.jp", "qoo10.jp", "zozo.jp",
    "wowma.jp", "au.com", "dmm.com", "note.com", "ameblo.jp", "linktr.ee",
    "threads.net", "pinterest.com", "bit.ly", "t.co",
}
# サイト作りのサービス：本体は止めないが、お店のサイト（お店.base.shop など）はそのお店だけなので止める
HOSTING = {
    "base.shop", "thebase.in", "thebase.com", "stores.jp", "shopify.com", "myshopify.com",
    "peraichi.com", "wixsite.com", "wix.com", "shop-pro.jp", "square.site", "jimdofree.com", "jimdo.com",
    "wordpress.com", "fc2.com", "hatenablog.com", "studio.site", "makeshop.jp", "ocnk.net",
}
GOV_SUFFIXES = (".go.jp", ".lg.jp")
HOST_RE = re.compile(r"^(?=.{4,253}$)(?!-)([a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,24}$")

# 通知の種類（題名で拾う）
DISPOSAL_WORDS = ("行政処分", "業務停止", "指示")
MAIL_ORDER = "通信販売"
MAYBE_WEB = ("電話勧誘販売", "特定商取引法違反事業者")


class GateError(Exception):
    """安全装置に止められた。何も書かずに終了する。"""


class FetchError(Exception):
    pass


# --------------------------------------------------------------------------
# 取得（1秒に1回・PDF はキャッシュ）
# --------------------------------------------------------------------------
_last_request = 0.0


def fetch(url: str, *, cache: bool = False, retries: int = 3) -> bytes:
    global _last_request
    cache_path = None
    if cache:
        name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1] or "index"
        digest = hashlib.sha1(url.encode()).hexdigest()[:8]
        cache_path = CACHE_DIR / "pdf" / f"{digest}_{name}"
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return cache_path.read_bytes()
    last_err: Exception | None = None
    for attempt in range(retries):
        wait = _last_request + REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as res:
                data = res.read()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    else:
        raise FetchError(f"取得できませんでした: {url} ({last_err})")
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    return data


def pdf_text(data: bytes) -> str:
    import io

    import pypdf  # 遅延 import（テストでは使わない）

    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    return "\n".join((p.extract_text() or "") for p in reader.pages)


# --------------------------------------------------------------------------
# 一覧・RSS・通知ページの読み取り
# --------------------------------------------------------------------------
def is_target_title(title: str) -> bool:
    if not any(w in title for w in DISPOSAL_WORDS):
        return False
    return MAIL_ORDER in title or any(w in title for w in MAYBE_WEB)


def normalize_notice_url(url: str) -> str:
    url = urllib.parse.urljoin(BASE + "/", url.strip())
    m = re.search(r"/notice/entry/(\d+)", url)
    if not m:
        return url
    return f"{BASE}/notice/entry/{m.group(1)}/"


def parse_rss(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        out.append({"title": title, "notice": normalize_notice_url(link) if link else ""})
    return out


_LISTING_A = re.compile(
    r'<a\s+href="(/notice/entry/\d+/?)"\s+title="([^"]*)"', re.S
)


def parse_listing(html_text: str) -> list[dict]:
    out, seen = [], set()
    for href, title in _LISTING_A.findall(html_text):
        url = normalize_notice_url(href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": html.unescape(title).strip(), "notice": url})
    return out


def parse_year_links(html_text: str) -> list[str]:
    years = sorted(
        set(re.findall(r'href="/policies/policy/consumer_transaction/release/(\d{4})/"', html_text)),
        reverse=True,
    )
    return years


_PDF_A = re.compile(r'<a\s+href="([^"]+\.pdf)\s*"[^>]*?title="([^"]*)"', re.S | re.I)


def parse_notice_page(html_text: str) -> dict:
    h1 = re.search(r"<h1>(.*?)</h1>", html_text, re.S)
    title = html.unescape(re.sub(r"<[^>]+>", "", h1.group(1))).strip() if h1 else ""
    d = re.search(r'<p class="al_right">\s*(\d{4})年(\d{1,2})月(\d{1,2})日', html_text)
    date = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else ""
    pdfs = []
    for href, link_title in _PDF_A.findall(html_text):
        link_title = html.unescape(link_title)
        if any(w in link_title for w in ("チラシ", "リーフレット", "啓発")):
            continue
        pdfs.append({"url": urllib.parse.urljoin(BASE + "/", href.strip()), "title": link_title})
    return {"title": title, "date": date, "pdfs": pdfs}


def companies_from_title(title: str) -> list[str]:
    m = re.search(r"【(.*?)】", title)
    if not m:
        return []
    inner = m.group(1).strip()
    parts = re.split(r"\s*(?:、|，|,|及び|および|並びに)\s*", inner)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------
# URL の抜き出し
# --------------------------------------------------------------------------
_URL_CHARS = r"A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%"
_URL_RE = re.compile(r"https?://[" + _URL_CHARS + r"]+")
# 行末で切れた URL のつなぎ目（改行と前後の空白）
_WRAP_RE = re.compile(r"(https?://[" + _URL_CHARS + r"]*?)[ \t]*\n[ \t]*(?=[A-Za-z0-9\-./])")


def normalize_text(text: str) -> str:
    """全角→半角などを NFKC でそろえ、URL の頭とホスト名に入った空白をつなぐ。"""
    t = unicodedata.normalize("NFKC", text)
    # 「http s://」「https : //」「https:/（改行）/」をそろえる
    t = re.sub(r"h\s*t\s*t\s*p\s*(s?)\s*:\s*/\s*/\s*", lambda m: f"http{m.group(1)}://", t)
    # 「www. caa. go. jp」のようにホスト名の「.」の後ろに入った空白を詰める
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"(https?://[A-Za-z0-9.\-]*\.)[ \t]+(?=[A-Za-z0-9])", r"\1", t)
    return t


# ホスト名がここで終わっていてよい、と言える最上位ドメイン。
# 行末でホスト名が切れたかどうかの判定だけに使う（遮断の判定には使わない）。
KNOWN_TLDS = {
    "jp", "com", "net", "org", "shop", "store", "info", "biz", "online", "site",
    "xyz", "top", "tokyo", "osaka", "life", "club", "io", "tv", "cc", "asia",
    "website", "beauty", "health", "fun", "app", "dev", "ai", "me", "us", "uk",
    "cn", "kr", "tw", "hk", "fr", "de", "link", "click", "work", "co.jp",
}


def _host_looks_complete(host: str) -> bool:
    if "." not in host or host.endswith((".", "-")):
        return False
    return host.rsplit(".", 1)[1].lower() in KNOWN_TLDS


def _join_wrapped(t: str) -> str:
    """URL のホスト名が行末で切れていたら、次の行の頭の ASCII をつなげる。

    PDF の全角の URL は行の途中で折り返される（例「https://orstar.」＋改行＋「jp/」、
    「https://item.ra」＋改行＋「kuten.co.jp/」、「https://www.caa.go.j」＋改行＋「p/」）。
    つなぐのは、ホスト名の後ろに「/」がまだ無く、ホスト名が既知の最上位ドメインで
    終わっていない時だけ。パスの折り返し（「https://ones-up.jp/」＋改行＋「shop」）は
    ホスト名に関係しないのでつながない。
    """

    def repl(m: re.Match) -> str:
        head = m.group(1)
        rest = head.split("://", 1)[1]
        if "/" in rest or _host_looks_complete(rest):
            return m.group(0)
        return head

    prev = None
    while prev != t:
        prev = t
        t = _WRAP_RE.sub(repl, t)
    return t


def extract_urls(text: str) -> list[tuple[str, int]]:
    t = _join_wrapped(normalize_text(text))
    return [(m.group(0), m.start()) for m in _URL_RE.finditer(t)]


def host_of(url: str) -> str:
    rest = url.split("://", 1)[1]
    host = re.split(r"[/?#:]", rest, maxsplit=1)[0]
    host = host.strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_gov(host: str) -> bool:
    return host.endswith(GOV_SUFFIXES) or host in ("go.jp", "lg.jp")


def platform_of(host: str) -> str | None:
    for p in PLATFORMS:
        if host == p or host.endswith("." + p):
            return p
    # サイト作りのサービスは本体だけ飛ばす（お店のサイトは止める）
    if host in HOSTING:
        return host
    return None


def classify_text(text: str, companies: list[str]) -> tuple[list[dict], list[dict]]:
    """PDF の文から (止めるホスト, 飛ばしたホスト) を返す。company 付き。"""
    t = _join_wrapped(normalize_text(text))
    names = [unicodedata.normalize("NFKC", c) for c in companies]
    aliases = {n: _aliases(n) for n in names}
    sites: dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    for m in _URL_RE.finditer(t):
        host = host_of(m.group(0))
        if not host or is_gov(host):
            continue
        company = _company_before(t, m.start(), aliases) or "、".join(names)
        p = platform_of(host)
        if p:
            skipped.setdefault(host, {"host": host, "reason": "platform", "company": company})
            continue
        if not HOST_RE.match(host):
            skipped.setdefault(host, {"host": host, "reason": "invalid", "company": company})
            continue
        sites.setdefault(host, {"host": host, "company": company})
    return list(sites.values()), list(skipped.values())


def _aliases(name: str) -> list[str]:
    core = re.sub(r"(株式会社|有限会社|合同会社|一般社団法人)", "", name).strip()
    out = [name]
    if core and core != name and len(core) >= 2:
        out.append(core)
    return out


def _company_before(t: str, pos: int, aliases: dict[str, list[str]]) -> str | None:
    if len(aliases) <= 1:
        return next(iter(aliases), None)
    best, best_pos = None, -1
    for name, al in aliases.items():
        for a in al:
            i = t.rfind(a, 0, pos)
            if i > best_pos:
                best, best_pos = name, i
    return best


# --------------------------------------------------------------------------
# 1件の通知を処理
# --------------------------------------------------------------------------
def process_notice(notice_url: str, list_title: str = "") -> dict:
    page = parse_notice_page(fetch(notice_url).decode("utf-8", "replace"))
    title = page["title"] or list_title
    companies = companies_from_title(title)
    texts = []
    for pdf in page["pdfs"]:
        try:
            texts.append(pdf_text(fetch(pdf["url"], cache=True)))
        except FetchError:
            raise
        except Exception as e:  # 壊れた PDF は1つずつ報告
            raise FetchError(f"PDF を読めませんでした: {pdf['url']} ({e})") from e
    full = "\n".join(texts)
    sites, skipped = classify_text(full, companies)
    if MAIL_ORDER in title and not sites and not skipped:
        # 通信販売なのにサイトが1つも読めない。止めはしないが目立たせる
        print(f"  注意: 通信販売の処分なのに URL が見つかりません: {notice_url}", file=sys.stderr)
    for s in sites:
        s.update({"date": page["date"], "notice": notice_url, "kind": "行政処分"})
    for s in skipped:
        s["notice"] = notice_url
    return {
        "notice": notice_url, "title": title, "date": page["date"],
        "pdfs": len(page["pdfs"]), "textLength": len(full),
        "sites": sites, "skipped": skipped,
    }


# --------------------------------------------------------------------------
# まとめ・安全装置
# --------------------------------------------------------------------------
def load_existing(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "generatedAt": "", "sources": ["caa"], "sites": [], "skipped": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise GateError(f"今の blocklist.json が読めません（壊れています）: {e}")
    if not isinstance(data.get("sites"), list):
        raise GateError("今の blocklist.json に sites がありません")
    return data


SITE_KEYS = ("host", "company", "date", "notice", "kind")
SKIP_KEYS = ("host", "reason", "notice")


def merge(existing: dict, results: list[dict], now: str, max_new: int = MAX_NEW_HOSTS) -> tuple[dict, list[str]]:
    sites = {s["host"]: {k: s.get(k, "") for k in SITE_KEYS} for s in existing.get("sites", [])}
    skipped = {(s["host"], s["notice"]): {k: s.get(k, "") for k in SKIP_KEYS} for s in existing.get("skipped", [])}
    old_count = len(existing.get("sites", []))
    new_hosts = []
    for r in results:
        for s in r["sites"]:
            if s["host"] not in sites:
                sites[s["host"]] = {k: s.get(k, "") for k in SITE_KEYS}
                new_hosts.append(s["host"])
        for s in r["skipped"]:
            key = (s["host"], s["notice"])
            if key not in skipped:
                skipped[key] = {k: s.get(k, "") for k in SKIP_KEYS}

    # 安全装置
    for h in sites:
        if is_gov(h):
            raise GateError(f"国や自治体のドメインが入りそうになりました: {h}")
        if not HOST_RE.match(h):
            raise GateError(f"ホスト名の形がおかしい物があります: {h!r}")
    if len(new_hosts) > max_new:
        raise GateError(
            f"1回で増えるサイトが多すぎます（{len(new_hosts)}件 > 上限{max_new}件）。"
            f"読み取りが壊れている可能性があります: {', '.join(sorted(new_hosts)[:10])} …"
        )
    if len(sites) < old_count:
        raise GateError(f"一覧が減ります（{old_count}件 → {len(sites)}件）")

    changed = bool(new_hosts) or len(skipped) != len(existing.get("skipped", []))
    out = {
        "version": 1,
        "generatedAt": now if changed or not existing.get("generatedAt") else existing["generatedAt"],
        "sources": ["caa"],
        "sites": sorted(sites.values(), key=lambda s: s["host"]),
        "skipped": sorted(skipped.values(), key=lambda s: (s["host"], s["notice"])),
    }
    return out, sorted(new_hosts)


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------
# 候補の通知を集める
# --------------------------------------------------------------------------
def collect_candidates(backfill: bool) -> list[dict]:
    cands: dict[str, dict] = {}

    rss = parse_rss(fetch(RSS_URL))
    if not rss:
        raise FetchError("RSS に1件も項目がありません（形が変わった可能性）")
    for it in rss:
        if it["notice"] and is_target_title(it["title"]):
            cands.setdefault(it["notice"], it)

    # RSS は直近20件しか無いので、止まっていた日があっても拾えるよう年度の一覧も読む
    top = fetch(LISTING_URL).decode("utf-8", "replace")
    listing = parse_listing(top)
    years = parse_year_links(top)
    if not listing or not years:
        raise FetchError("特定商取引法の公表資料の一覧が読めません（形が変わった可能性）")
    pages = [top]
    year_pages = years[: BACKFILL_YEARS if backfill else 1]
    for y in year_pages:
        pages.append(fetch(f"{LISTING_URL}{y}/").decode("utf-8", "replace"))
    for p in pages:
        for it in parse_listing(p):
            if is_target_title(it["title"]):
                cands.setdefault(it["notice"], it)
    return list(cands.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true", help="過去の年度の一覧も読む（初回だけ）")
    ap.add_argument("--dry-run", action="store_true", help="書き込まない")
    ap.add_argument("--max-new", type=int, default=MAX_NEW_HOSTS, help="1回で増やしてよい上限")
    ap.add_argument("--output", default=str(BLOCKLIST))
    args = ap.parse_args(argv)

    out_path = Path(args.output)
    try:
        existing = load_existing(out_path)
        known_notices = {s["notice"] for s in existing.get("sites", []) + existing.get("skipped", [])}
        cands = collect_candidates(args.backfill)
        results = []
        for c in sorted(cands, key=lambda c: c["notice"]):
            if c["notice"] in known_notices:
                continue
            r = process_notice(c["notice"], c["title"])
            mail = MAIL_ORDER in r["title"]
            if mail and r["textLength"] == 0:
                raise FetchError(f"通信販売の処分なのに PDF の文が読めません: {c['notice']}")
            print(
                f"  {r['date']} {r['title'][:50]} → サイト{len(r['sites'])}件"
                f" / 飛ばし{len(r['skipped'])}件  {c['notice']}",
                file=sys.stderr,
            )
            results.append(r)
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(microsecond=0).isoformat()
        merged, new_hosts = merge(existing, results, now, args.max_new)
    except GateError as e:
        print(f"止めました（安全装置）: {e}", file=sys.stderr)
        return 2
    except FetchError as e:
        print(f"止めました（取得の失敗）: {e}", file=sys.stderr)
        return 3

    print(f"候補の通知 {len(cands)}件 / 新しいサイト {len(new_hosts)}件 / 合計 {len(merged['sites'])}件", file=sys.stderr)
    for h in new_hosts:
        print(f"  + {h}", file=sys.stderr)
    text = dump_json(merged)
    if args.dry_run:
        print(text)
        return 0
    old_text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    if text != old_text:
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out_path)
        print(f"書きました: {out_path}", file=sys.stderr)
    else:
        print("変化なし（正常）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
