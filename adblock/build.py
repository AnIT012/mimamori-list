#!/usr/bin/env python3
"""広告の一覧を取ってきて変換し、dist/ と manifest.json を作る。毎朝 GitHub Actions（macOS）が動かす。

安全装置（どれかに当たると何も書かずに終了コード 2）
- 取ってきた一覧が小さすぎる（取り違え・壊れた疑い）
- 変換できた規則が、前の manifest より半分以下に減った
WebKit に読めるかは、この後 compile_check.swift が確かめる（読めなければ commit しない）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
MANIFEST = DIST / "manifest.json"
UA = "mimamori-list/1.0 (+https://github.com/AnIT012/mimamori-list)"

SOURCES = [
    # id, 取ってくる所（None は自作）, 出典, ライセンス, 最低の行数
    ("japan", None, "見守りブラウザ（自作）", "CC0-1.0", 10),
    ("easylist", "https://easylist.to/easylist/easylist.txt", "EasyList (https://easylist.to/)", "CC-BY-SA-3.0", 30_000),
    ("easyprivacy", "https://easylist.to/easylist/easyprivacy.txt", "EasyPrivacy (https://easylist.to/)", "CC-BY-SA-3.0", 30_000),
]


class GateError(Exception):
    pass


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def main() -> int:
    DIST.mkdir(exist_ok=True)
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"lists": []}
    prev_rules = {l["id"]: l["rules"] for l in previous.get("lists", [])}
    work = HERE / ".work"
    work.mkdir(exist_ok=True)

    lists = []
    try:
        for list_id, url, source, license_, min_lines in SOURCES:
            src = HERE / f"{list_id}.txt" if url is None else work / f"{list_id}.txt"
            if url:
                src.write_text(fetch(url), encoding="utf-8")
            lines = src.read_text(encoding="utf-8").count("\n")
            if lines < min_lines:
                raise GateError(f"{list_id} が {lines} 行しかありません（{min_lines} 行以上のはず）")
            out = subprocess.run([sys.executable, str(HERE / "convert.py"), str(work / list_id), str(src)],
                                 check=True, capture_output=True, text=True)
            info = json.loads(out.stdout)
            for f in info["files"]:
                data = (work / f["file"]).read_bytes()
                lists.append({"id": f["file"].removesuffix(".json"), "file": f["file"], "rules": f["rules"],
                              "sha256": hashlib.sha256(data).hexdigest(), "source": source, "license": license_})
            print(f"{list_id}: {lines} 行 → 規則 {sum(f['rules'] for f in info['files'])} 件（捨てた {sum(info['skipped'].values())} 件）")
        for l in lists:
            before = prev_rules.get(l["id"])
            if before and l["rules"] < before // 2:
                raise GateError(f"{l['id']} の規則が {before} → {l['rules']} 件に減りました")
    except GateError as e:
        print(f"止めました：{e}", file=sys.stderr)
        return 2

    changed = [l["id"] for l in lists if l["sha256"] not in {p.get("sha256") for p in previous.get("lists", [])}]
    if not changed and len(lists) == len(previous.get("lists", [])):
        print("変化なし（正常）")
        return 0
    for l in lists:
        (DIST / l["file"]).write_bytes((work / l["file"]).read_bytes())
    for old in DIST.glob("*.json"):
        if old.name != "manifest.json" and old.name not in {l["file"] for l in lists}:
            old.unlink()
    manifest = {"version": 1, "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "lists": lists}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("更新：" + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
