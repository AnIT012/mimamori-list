"""update.py のテスト（ネットにつながない）。

fixtures/*.txt は消費者庁の実際の PDF から pypdf で取り出した文そのまま。
    python -m unittest test_update.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update as u  # noqa: E402

FX = HERE / "fixtures"


def fx(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


def hosts(text, companies=()):
    sites, skipped = u.classify_text(text, list(companies))
    return {s["host"]: s for s in sites}, {s["host"]: s for s in skipped}


class FullWidthUrl(unittest.TestCase):
    def test_full_width_url_in_real_pdf(self):
        text = fx("045480_pureas.txt")
        self.assertIn("ｈｔｔｐｓ：／／ｏｎｅｓ－ｕｐ．ｊｐ／", text)  # 元は全角
        sites, _ = hosts(text, ["株式会社ピュレアス"])
        self.assertEqual(set(sites), {"ones-up.jp"})
        self.assertEqual(sites["ones-up.jp"]["company"], "株式会社ピュレアス")

    def test_spaces_in_scheme_and_host(self):
        sites, _ = hosts("（ｈｔｔｐ ｓ：／／ｗｗｗ． ｅｘａｍｐｌｅ－ｓｈｏｐ． ｊｐ／」で")
        self.assertEqual(set(sites), {"example-shop.jp"})


class WrappedUrl(unittest.TestCase):
    def test_host_wrapped_mid_label(self):
        # 「https://shirayuk」＋改行＋「i-wakahada.com/shop」
        sites, _ = hosts(fx("040490_verify.txt"))
        self.assertEqual(set(sites), {"shirayuki-wakahada.com"})

    def test_host_wrapped_after_dot(self):
        # 「https://churumi.」＋改行＋「com/shop」など3社分
        sites, _ = hosts(fx("045634_mio_meilie_wellvy.txt"))
        self.assertEqual(set(sites), {"hakualead.com", "lumierecosme.com", "churumi.com"})

    def test_scheme_wrapped(self):
        sites, _ = hosts("ＵＲＬが「ｈｔｔｐｓ：／\n／ｅｍｅｒｉｒｅ．ｃｏｍ／で始まるもの")
        self.assertEqual(set(sites), {"emerire.com"})

    def test_path_wrap_does_not_eat_following_text(self):
        # ホスト名が終わった後の折り返し（https://ones-up.jp/ ＋ shop）は巻き込まない
        sites, _ = hosts("https://ones-up.jp\nshopで始まるもの")
        self.assertEqual(set(sites), {"ones-up.jp"})


class GovExclusion(unittest.TestCase):
    def test_go_jp_dropped(self):
        text = fx("045480_pureas.txt")
        self.assertIn("kokusen.go.jp", u.normalize_text(text))
        sites, skipped = hosts(text)
        for h in list(sites) + list(skipped):
            self.assertFalse(h.endswith("go.jp"), h)

    def test_wrapped_go_jp_dropped(self):
        # 実物：「https://www.caa.go.j」＋改行＋「p/)」。つながないと caa.go.j が残る
        sites, skipped = hosts(fx("039565_sunsiri.txt"))
        self.assertEqual(set(sites), {"suncosme.com"})
        self.assertEqual(skipped, {})

    def test_lg_jp_dropped(self):
        sites, _ = hosts("https://www.city.minoh.lg.jp/ と https://bad-shop.jp/")
        self.assertEqual(set(sites), {"bad-shop.jp"})


class PlatformSkip(unittest.TestCase):
    def test_rakuten_shop_page_skipped_not_blocked(self):
        sites, skipped = hosts(fx("037529_hal.txt"), ["株式会社HAL"])
        self.assertEqual(set(sites), {"ec.dr-stick.shop"})
        self.assertIn("item.rakuten.co.jp", skipped)
        self.assertEqual(skipped["item.rakuten.co.jp"]["reason"], "platform")

    def test_sns(self):
        sites, skipped = hosts("https://www.instagram.com/foo https://x.com/bar https://line.me/R/ti")
        self.assertEqual(sites, {})
        self.assertEqual({s["reason"] for s in skipped.values()}, {"platform"})


class MultiCompany(unittest.TestCase):
    def test_title_split(self):
        self.assertEqual(
            u.companies_from_title("通信販売業者【株式会社MIO、株式会社Meilie及び株式会社WELLVY】に対する行政処分について"),
            ["株式会社MIO", "株式会社Meilie", "株式会社WELLVY"],
        )

    def test_each_host_gets_its_company(self):
        companies = u.companies_from_title("通信販売業者【株式会社MIO、株式会社Meilie及び株式会社WELLVY】に対する行政処分について")
        sites, _ = hosts(fx("045634_mio_meilie_wellvy.txt"), companies)
        self.assertEqual(sites["hakualead.com"]["company"], "株式会社MIO")
        self.assertEqual(sites["lumierecosme.com"]["company"], "株式会社Meilie")
        self.assertEqual(sites["churumi.com"]["company"], "株式会社WELLVY")


class Titles(unittest.TestCase):
    def test_target_titles(self):
        self.assertTrue(u.is_target_title("通信販売業者【 株式会社ピュレアス 】に対する行政処分について"))
        self.assertTrue(u.is_target_title("電話勧誘販売業者【 アドネス株式会社 】に対する行政処分について"))
        self.assertFalse(u.is_target_title("訪問販売業者【株式会社イトケン】に対する行政処分について"))
        self.assertFalse(u.is_target_title("ネット通販の通信販売に関する注意喚起"))

    def test_notice_page_skips_flyers(self):
        page = u.parse_notice_page(
            '<h1>通信販売業者【 株式会社ピュレアス 】に対する行政処分について</h1>'
            '<p class="al_right">2026年03月19日</p>'
            '<a href="/notice/assets/a_01.pdf "target="_blank" title="通信販売業者【株式会社ピュレアス】に対する行政処分について[PDF]">x</a>'
            '<a href="/notice/assets/a_02.pdf "target="_blank" title="チラシ「ネット通販での購入時には」[PDF]">x</a>'
        )
        self.assertEqual(page["date"], "2026-03-19")
        self.assertEqual([p["url"] for p in page["pdfs"]], ["https://www.caa.go.jp/notice/assets/a_01.pdf"])


def result(hosts_, notice="https://www.caa.go.jp/notice/entry/000001/"):
    return {
        "notice": notice,
        "sites": [{"host": h, "company": "株式会社テスト", "date": "2026-01-01", "notice": notice, "kind": "行政処分"} for h in hosts_],
        "skipped": [],
    }


class Gates(unittest.TestCase):
    def empty(self):
        return {"version": 1, "generatedAt": "", "sources": ["caa"], "sites": [], "skipped": []}

    def test_too_many_new_hosts(self):
        with self.assertRaises(u.GateError):
            u.merge(self.empty(), [result([f"shop{i}.jp" for i in range(26)])], "now")

    def test_25_is_ok(self):
        out, new = u.merge(self.empty(), [result([f"shop{i}.jp" for i in range(25)])], "now")
        self.assertEqual(len(new), 25)

    def test_gov_host_blocked_by_gate(self):
        with self.assertRaises(u.GateError):
            u.merge(self.empty(), [result(["caa.go.jp"])], "now")
        with self.assertRaises(u.GateError):
            u.merge(self.empty(), [result(["city.osaka.lg.jp"])], "now")

    def test_bad_hostname(self):
        for bad in ["shop..jp", "-bad.jp", "nodot", "ex ample.jp", "例え.jp"]:
            with self.assertRaises(u.GateError, msg=bad):
                u.merge(self.empty(), [result([bad])], "now")

    def test_old_entries_kept_and_stable(self):
        old, _ = u.merge(self.empty(), [result(["old-shop.jp"])], "t1")
        out, new = u.merge(old, [result(["new-shop.jp"], notice="https://www.caa.go.jp/notice/entry/000002/")], "t2")
        self.assertEqual([s["host"] for s in out["sites"]], ["new-shop.jp", "old-shop.jp"])
        self.assertEqual(new, ["new-shop.jp"])
        # 何も増えなければ generatedAt も変えない（差分が出ない）
        again, new2 = u.merge(out, [], "t3")
        self.assertEqual(new2, [])
        self.assertEqual(u.dump_json(again), u.dump_json(out))

    def test_broken_existing_file_stops(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "blocklist.json"
            p.write_text("{broken", encoding="utf-8")
            with self.assertRaises(u.GateError):
                u.load_existing(p)

    def test_shrink_detected(self):
        # 今の一覧に同じホストが重複して入っている → まとめると減る → 止める
        old = self.empty()
        old["sites"] = [
            {"host": "a-shop.jp", "company": "", "date": "", "notice": "n", "kind": "行政処分"},
            {"host": "a-shop.jp", "company": "", "date": "", "notice": "n", "kind": "行政処分"},
        ]
        with self.assertRaises(u.GateError):
            u.merge(old, [], "now")


class RealBlocklist(unittest.TestCase):
    def test_committed_blocklist_is_valid(self):
        p = HERE / "blocklist.json"
        if not p.exists():
            self.skipTest("blocklist.json がまだ無い")
        data = json.loads(p.read_text(encoding="utf-8"))
        hosts_ = [s["host"] for s in data["sites"]]
        self.assertEqual(hosts_, sorted(set(hosts_)))
        for h in hosts_:
            self.assertRegex(h, u.HOST_RE)
            self.assertFalse(u.is_gov(h))
            self.assertIsNone(u.platform_of(h))


class HostingTests(unittest.TestCase):
    def test_hosting_root_skipped_but_shop_blocked(self):
        import update
        self.assertEqual(update.platform_of("peraichi.com"), "peraichi.com")
        self.assertIsNone(update.platform_of("kenko-shop.base.shop"))
        self.assertIsNone(update.platform_of("lp.wixsite.com"))
        # 大きな場は、その下も飛ばす（場ごと止めない）
        self.assertEqual(update.platform_of("item.rakuten.co.jp"), "rakuten.co.jp")


if __name__ == "__main__":
    unittest.main()
