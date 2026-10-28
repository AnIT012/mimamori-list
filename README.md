# 消費者庁の処分サイトの一覧

消費者庁が特定商取引法で行政処分をした通信販売業者のサイトを集めて、`blocklist.json` にします。見守りブラウザ（高齢の家族のための、買う手前で止めるブラウザ）はこの一覧のサイトを止めます。

読む URL：`https://raw.githubusercontent.com/AnIT012/mimamori-list/main/blocklist.json`毎朝 GitHub Actions が自動で更新します（`.github/workflows/update.yml`）。

## どこから集めるか

- 消費者庁の新着情報の RSS（`https://www.caa.go.jp/news.rss`）
- 特定商取引法の公表資料の一覧（`/policies/policy/consumer_transaction/release/` と年度ごとのページ）

RSS には直近の20件ほどしか載らないため、係が数日止まっても拾い直せるよう、毎日の更新でも今年度と前年度の一覧を読んでいます。

題名に「行政処分」などがあり、「通信販売」「電話勧誘販売」「特定商取引法違反事業者」のどれかを含む通知を対象にします。通知の PDF（チラシは除く）から URL を抜き出し、ホスト名にして（先頭の `www.` は外す）一覧に入れます。電話勧誘販売の通知でサイトが書かれていなければ、何も足しません。

- PDF の URL は全角で書かれています。NFKC で半角にそろえます。
- 行の途中で折り返された URL（例「https://orstar.」の次の行に「jp/」）はつなぎます。ホスト名の後ろの部分（パス）の折り返しはつなぎません。
- `*.go.jp`・`*.lg.jp` は入れません。
- Amazon・楽天・Yahoo!・SNS などの大きな場は、場ごと止めないよう `skipped`（理由 `platform`）に記録するだけにします。

## 動かし方

```sh
python3 -m venv .venv && .venv/bin/pip install "pypdf[crypto]"   # PDF が暗号化されているので crypto が要る
.venv/bin/python -m unittest test_update.py   # テスト（ネットにつながない。pypdf も不要）
.venv/bin/python update.py --dry-run          # 書き込まずに結果を見る
.venv/bin/python update.py                    # 毎日の更新
.venv/bin/python update.py --backfill         # 初回だけ。過去4年度分の一覧も読む
```

消費者庁へは1秒に1回までしか取りに行きません。取った PDF は `.cache/`（git に入れない）にためます。

## 安全装置

次のどれかに当たると、何も書かずに止まります（終了コード 2。取得の失敗は 3）。Actions では issue「消費者庁の一覧の更新が止まっています」が開きます。

- 1回で増えるサイトが25件を超える（読み取りが壊れた疑い）。本当に多い時だけ `--max-new` で上限を上げて手で流す
- `go.jp`・`lg.jp` のホストが入りそうになる
- ホスト名の形がおかしい
- 一覧の件数が減る（古い行は消しません）
- RSS や一覧ページから1件も読めない（ページの形が変わった疑い）
- 通信販売の処分なのに PDF の文が読めない

## 知らせの決まり

- 新しい処分が無い日は正常です。知らせません。
- 実行が失敗した時、または前回の成功した実行から45日を超えてあいていた時に、issue を開くかコメントを足します。次に成功した時に閉じます。

## 分かっている穴

- 係が自分の「動かなかった」を知らせることはできません。GitHub は公開リポジトリで60日間 commit が無いと定期実行を止めます。45日の確認は、次に動いた時に「あいていた」と分かるだけです。外からの見張りが必要なら、別の場所で `blocklist.json` の更新か Actions の実行日を見てください。
- 集めるのは消費者庁の処分だけです。都道府県の処分、処分後に作り直した別のドメイン、定期購入の罠でも処分されていないサイトは入りません。
- 2022年度の一覧のページには通信販売の処分が載っていませんでした（一覧から漏れている可能性あり）。2022年度より前はさかのぼっていません。
- `ec.bitela.jp` のようにサブドメインで書かれた物はそのまま入れています。アプリ側では、そのホストと配下のサブドメインを止める想定です。
- 楽天などの店のページは止めていません（`skipped` を見てください）。
- 画像だけの PDF（文字が取れない物）は読めません。

## 生存の印

公開リポジトリは、60日 commit がないと GitHub が定期実行を止めます。処分は年に数件なので、一覧が変わらない月が続きます。そこで、最後の commit から30日を超えたら `heartbeat.txt` に日時を書いて commit します。

## 出典

一覧は消費者庁の公表資料（https://www.caa.go.jp/）をもとに作っています。各行の `notice` が元の通知です。消費者庁のサイトのコンテンツは、政府標準利用規約に沿って出典を書けば使えます。

## 広告の一覧（adblock/）

見守りブラウザの広告ブロックに使う一覧です。毎朝 GitHub Actions（macOS）が、元の一覧を取ってきて WebKit の Content Blocker の形に変換し、**本物の WebKit に読ませて通った時だけ** `adblock/dist/` を更新します。アプリは `adblock/dist/manifest.json` を見て、中身（sha256）が変わった一覧だけを取りに行きます。

| ファイル | 元 | ライセンス |
|---|---|---|
| `dist/japan-*.json` | `adblock/japan.txt`（見守りブラウザの自作。日本の広告の配信元と枠） | CC0-1.0 |
| `dist/easylist-*.json` | [EasyList](https://easylist.to/)（The EasyList authors） | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)（EasyList は GPL-3.0 と CC BY-SA 3.0 の二重ライセンス。ここでは CC BY-SA 3.0 を選んでいます） |
| `dist/easyprivacy-*.json` | [EasyPrivacy](https://easylist.to/)（The EasyList authors） | CC BY-SA 3.0（同上） |

`dist/easylist-*.json` と `dist/easyprivacy-*.json` は、元の一覧を `adblock/convert.py` で変換した二次的な著作物で、CC BY-SA 3.0 で公開します。変換では、WebKit が読めない書き方（正規表現の規則・`$redirect` などの拡張・拡張 CSS・ASCII でない規則）を捨てています。

- 変換の道具：`adblock/convert.py`（自作。GPL の変換器は使っていません）。検査は `adblock/test_convert.py`
- WebKit で読めるかの確かめ：`adblock/compile_check.swift`
- 安全装置：元の一覧が小さすぎる・規則が前の半分以下に減った・WebKit が読めない、のどれかで止まり、dist を書き換えずに issue を立てます
