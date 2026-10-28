#!/usr/bin/env python3
"""広告の一覧（Adblock Plus の書き方）を、WebKit の Content Blocker の JSON に変える。

自前の道具（GPL の変換器は使わない）。変換できない書き方は、捨てて数だけ数える。
- ふつうの規則      ||ads.example.com^$third-party,script  → block
- 例外              @@||example.com^                        → ignore-previous-rules（各ファイルの最後）
- 部品を隠す        example.com##.ad-box / ##.ad-box         → css-display-none
- 捨てる物          正規表現の規則、$redirect などの拡張、:has-text などの拡張 CSS、日本語などの ASCII でない規則

WebKit の決まり（ContentExtensionParser / URLFilterParser）
- url-filter に「|」（どちらか）と {n} は使えない。ASCII だけ。
- 1つのリストは 150,000 件まで（ここでは 140,000 件で分ける）。
- if-domain と unless-domain は同じ規則に両方は書けない。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_RULES_PER_FILE = 140_000
SELECTORS_PER_RULE = 100

RESOURCE_TYPES = {
    "script": ["script"],
    "image": ["image"],
    "stylesheet": ["style-sheet"],
    "font": ["font"],
    "media": ["media"],
    "xmlhttprequest": ["raw"],
    "other": ["raw"],
    "ping": ["raw"],
    "websocket": ["raw"],
    "subdocument": ["document"],
    "popup": ["popup"],
}
ALL_TYPES = sorted({t for ts in RESOURCE_TYPES.values() for t in ts if t != "popup"})

# 意味は変わらないので無視してよい印
IGNORED_OPTIONS = {"important", "match-case", "all", "~important"}
# 例外の時だけ意味がある、ページ全体の印
PAGE_OPTIONS = {"document", "elemhide", "generichide", "ehide", "ghide", "doc"}

EXTENDED_CSS = re.compile(
    r":-abp-|:has-text|:contains|:xpath|:matches-css|:upward|:remove|:style|:nth-ancestor|:matches-path|"
    r":min-text-length|:watch-attr|:matches-attr|:matches-prop|\[-ext-|:if\(|:if-not|:others|:-ext-|:matches-media"
)


@dataclass
class Stats:
    converted: int = 0
    skipped: dict = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def pattern_to_regex(pattern: str) -> str | None:
    """Adblock の書き方の URL の形を、WebKit が読める正規表現にする。無理なら None。"""
    if not pattern or not is_ascii(pattern):
        return None
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 1:
        return None  # 正規表現の規則は、WebKit の部分集合に収まるとは限らないので捨てる
    prefix = ""
    if pattern.startswith("||"):
        prefix = r"^[a-z][a-z0-9.+-]*://([^/]*\.)?"
        pattern = pattern[2:]
    elif pattern.startswith("|"):
        prefix = "^"
        pattern = pattern[1:]
    suffix = ""
    if pattern.endswith("|"):
        suffix = "$"
        pattern = pattern[:-1]
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "^":
            out.append("[/:?&=]")
        elif ch in ".+?()[]{}\\$|":
            out.append("\\" + ch)
        else:
            out.append(ch)
    body = "".join(out)
    while body.startswith(".*"):
        body = body[2:]
    regex = prefix + body + suffix
    if not regex or regex in ("^", "$") or "|" in regex.replace("\\|", ""):
        return None
    return regex or None


def domains_to_trigger(domains: list[str]) -> dict | None:
    inc = [d for d in domains if d and not d.startswith("~")]
    exc = [d[1:] for d in domains if d.startswith("~")]
    if any(not is_ascii(d) or "*" in d or "/" in d for d in inc + exc):
        return None
    if inc:
        return {"if-domain": sorted({"*" + d.lower() for d in inc})}
    if exc:
        return {"unless-domain": sorted({"*" + d.lower() for d in exc})}
    return {}


def convert_network(line: str, stats: Stats) -> tuple[dict | None, bool]:
    """1行を変える。返り値は（規則, 例外か）"""
    exception = line.startswith("@@")
    if exception:
        line = line[2:]
    pattern, _, opts = line.partition("$") if "$" in line and not line.startswith("/") else (line, "", "")
    if "$" in line and line.startswith("/"):
        stats.skip("regex")
        return None, exception

    trigger: dict = {}
    types: set[str] = set()
    neg_types: set[str] = set()
    page_level = False
    for raw in [o.strip() for o in opts.split(",") if o.strip()]:
        o = raw.lower()
        neg = o.startswith("~")
        name = o[1:] if neg else o
        if o in IGNORED_OPTIONS:
            continue
        if name in ("third-party", "3p"):
            trigger["load-type"] = ["first-party" if neg else "third-party"]
        elif name in ("first-party", "1p"):
            trigger["load-type"] = ["third-party" if neg else "first-party"]
        elif name in RESOURCE_TYPES:
            (neg_types if neg else types).update(RESOURCE_TYPES[name])
        elif name.startswith("domain="):
            t = domains_to_trigger(raw.split("=", 1)[1].split("|"))
            if t is None:
                stats.skip("domain")
                return None, exception
            trigger.update(t)
        elif exception and name in PAGE_OPTIONS:
            page_level = True
        else:
            stats.skip("option:" + name.split("=")[0])
            return None, exception

    if page_level:
        # @@||example.com^$document など：そのサイトでは何も止めない
        host = re.match(r"^\|\|([a-z0-9.-]+)\^?$", pattern.lower())
        if not host:
            stats.skip("page-exception")
            return None, exception
        stats.converted += 1
        return {"trigger": {"url-filter": ".*", "if-domain": ["*" + host.group(1)]},
                "action": {"type": "ignore-previous-rules"}}, True

    regex = pattern_to_regex(pattern)
    if regex is None:
        stats.skip("pattern")
        return None, exception
    trigger = {"url-filter": regex, **trigger}
    if neg_types and not types:
        types = set(ALL_TYPES) - neg_types
    if types:
        trigger["resource-type"] = sorted(types)
    stats.converted += 1
    action = "ignore-previous-rules" if exception else "block"
    return {"trigger": trigger, "action": {"type": action}}, exception


def parse_cosmetic(line: str, stats: Stats) -> tuple[str, str] | None:
    """example.com,~b.com##.ad → （ドメインの並び, セレクタ）"""
    if "#@#" in line or "#?#" in line or "#$#" in line or "#%#" in line:
        stats.skip("cosmetic-ext")
        return None
    domains, _, selector = line.partition("##")
    selector = selector.strip()
    if not selector or EXTENDED_CSS.search(selector) or not is_ascii(selector) or "\\" in selector:
        stats.skip("cosmetic-ext")
        return None
    if domains_to_trigger(domains.split(",")) is None:
        stats.skip("domain")
        return None
    return domains, selector


def convert(lines: list[str], stats: Stats) -> list[dict]:
    blocks: list[dict] = []
    exceptions: list[dict] = []
    cosmetics: dict[str, list[str]] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("!") or line.startswith("["):
            continue
        if "##" in line or "#@#" in line or "#?#" in line or "#$#" in line or "#%#" in line:
            parsed = parse_cosmetic(line, stats)
            if parsed:
                d, s = parsed
                cosmetics.setdefault(",".join(sorted(x for x in d.split(",") if x)), []).append(s)
                stats.converted += 1
            continue
        rule, exc = convert_network(line, stats)
        if rule:
            (exceptions if exc else blocks).append(rule)

    css_rules: list[dict] = []
    for domains, selectors in sorted(cosmetics.items()):
        trig = {"url-filter": ".*", **(domains_to_trigger(domains.split(",")) or {})} if domains else {"url-filter": ".*"}
        uniq = sorted(set(selectors))
        for i in range(0, len(uniq), SELECTORS_PER_RULE):
            css_rules.append({"trigger": dict(trig), "action": {"type": "css-display-none", "selector": ", ".join(uniq[i:i + SELECTORS_PER_RULE])}})
    return blocks + css_rules + exceptions


def split(rules: list[dict], exceptions_at_end: bool = True) -> list[list[dict]]:
    """15万件の上限に合わせて分ける。例外は各ファイルの最後に付け直す（WebKit の例外は同じリストの中だけで効く）"""
    exc = [r for r in rules if r["action"]["type"] == "ignore-previous-rules"]
    body = [r for r in rules if r["action"]["type"] != "ignore-previous-rules"]
    size = MAX_RULES_PER_FILE - len(exc)
    chunks = [body[i:i + size] for i in range(0, len(body), size)] or [[]]
    return [c + exc for c in chunks]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("使い方: convert.py <出力の名前> <一覧.txt>...", file=sys.stderr)
        return 2
    name, inputs = argv[1], argv[2:]
    stats = Stats()
    lines: list[str] = []
    for p in inputs:
        lines += Path(p).read_text(encoding="utf-8").splitlines()
    rules = convert(lines, stats)
    out = []
    for i, chunk in enumerate(split(rules), 1):
        path = Path(f"{name}-{i}.json")
        path.write_text(json.dumps(chunk, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        out.append({"file": path.name, "rules": len(chunk)})
    print(json.dumps({"name": name, "converted": stats.converted, "skipped": dict(sorted(stats.skipped.items(), key=lambda x: -x[1])), "files": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
