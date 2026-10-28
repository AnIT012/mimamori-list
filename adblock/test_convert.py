"""convert.py の検査（ネットにつながない）。python3 -m unittest test_convert.py"""
import re
import unittest

from convert import Stats, convert, convert_network, parse_cosmetic, pattern_to_regex, split


def matches(regex: str, url: str) -> bool:
    return re.search(regex, url, re.IGNORECASE) is not None


class PatternTests(unittest.TestCase):
    def test_host_anchor(self):
        r = pattern_to_regex("||ads.example.com^")
        self.assertTrue(matches(r, "https://ads.example.com/x.js"))
        self.assertTrue(matches(r, "https://cdn.ads.example.com/x.js"))
        self.assertFalse(matches(r, "https://notads.example.com.evil.jp/"))
        self.assertFalse(matches(r, "https://example.jp/?u=ads.example.com/"))
        # 区切り（^）が無いと、名前の続きに別の名前がつく所まで当たってしまう
        self.assertFalse(matches(r, "https://ads.example.com.evil.jp/"))

    def test_wildcard_and_separator(self):
        r = pattern_to_regex("/banner/*/ad_")
        self.assertTrue(matches(r, "https://x.jp/banner/300/ad_1.png"))

    def test_no_alternation(self):
        # WebKit は「|」を受け付けない。最後の | は「ここで終わり」の印として $ にする
        self.assertEqual(pattern_to_regex("swf|"), "swf$")
        self.assertNotIn("|", pattern_to_regex("||a.jp^"))

    def test_regex_and_non_ascii_skipped(self):
        self.assertIsNone(pattern_to_regex("/ad[0-9]+\\.js/"))
        self.assertIsNone(pattern_to_regex("||広告.jp^"))


class NetworkTests(unittest.TestCase):
    def test_options(self):
        rule, exc = convert_network("||ads.jp^$third-party,script,domain=a.jp|b.jp", Stats())
        self.assertFalse(exc)
        t = rule["trigger"]
        self.assertEqual(t["load-type"], ["third-party"])
        self.assertEqual(t["resource-type"], ["script"])
        self.assertEqual(t["if-domain"], ["*a.jp", "*b.jp"])
        self.assertEqual(rule["action"]["type"], "block")

    def test_unknown_option_skipped(self):
        s = Stats()
        rule, _ = convert_network("||ads.jp^$redirect=noop.js", s)
        self.assertIsNone(rule)
        self.assertEqual(s.skipped, {"option:redirect": 1})

    def test_document_exception(self):
        rule, exc = convert_network("@@||example.co.jp^$document", Stats())
        self.assertTrue(exc)
        self.assertEqual(rule["trigger"], {"url-filter": ".*", "if-domain": ["*example.co.jp"]})
        self.assertEqual(rule["action"]["type"], "ignore-previous-rules")

    def test_negated_types(self):
        rule, _ = convert_network("||ads.jp^$~script", Stats())
        self.assertNotIn("script", rule["trigger"]["resource-type"])
        self.assertIn("image", rule["trigger"]["resource-type"])


class CosmeticTests(unittest.TestCase):
    def test_extended_css_skipped(self):
        self.assertIsNone(parse_cosmetic('##div:has-text(Sponsored)', Stats()))
        self.assertIsNone(parse_cosmetic("##.a:-abp-has(.b)", Stats()))
        self.assertEqual(parse_cosmetic("example.jp##.ad-box", Stats()), ("example.jp", ".ad-box"))

    def test_batched_and_exceptions_last(self):
        rules = convert(["##.a", "##.b", "x.jp##.c", "||ads.jp^", "@@||ok.jp^$document", "! comment"], Stats())
        types = [r["action"]["type"] for r in rules]
        self.assertEqual(types[-1], "ignore-previous-rules")
        css = [r for r in rules if r["action"]["type"] == "css-display-none"]
        self.assertEqual(css[0]["action"]["selector"], ".a, .b")
        self.assertEqual(css[1]["trigger"]["if-domain"], ["*x.jp"])

    def test_split_keeps_exceptions_in_every_file(self):
        import convert as c
        old = c.MAX_RULES_PER_FILE
        c.MAX_RULES_PER_FILE = 3
        try:
            rules = [{"trigger": {"url-filter": str(i)}, "action": {"type": "block"}} for i in range(5)]
            rules.append({"trigger": {"url-filter": "x"}, "action": {"type": "ignore-previous-rules"}})
            chunks = split(rules)
            self.assertEqual(len(chunks), 3)
            for ch in chunks:
                self.assertEqual(ch[-1]["action"]["type"], "ignore-previous-rules")
                self.assertLessEqual(len(ch), 3)
        finally:
            c.MAX_RULES_PER_FILE = old


class AuditFixTests(unittest.TestCase):
    def test_slash_rule_with_options_is_not_regex(self):
        # 総点検 C10：/aclib.js$script は正規表現ではない普通の規則
        s = Stats()
        rule, _ = convert_network("/aclib.js$script", s)
        self.assertIsNotNone(rule)
        self.assertEqual(rule["trigger"]["resource-type"], ["script"])
        self.assertNotIn("regex", s.skipped)
        # 本当の正規表現は捨てる
        self.assertIsNone(convert_network("/ad[0-9]+\\.js/$script", Stats())[0])

    def test_hide_only_exception_does_not_lift_network_blocks(self):
        # 総点検 C15：@@||site^$generichide はサイト全体の ignore-previous-rules にしない
        s = Stats()
        rule, _ = convert_network("@@||jetzt.de^$generichide", s)
        self.assertIsNone(rule)
        self.assertEqual(s.skipped, {"hide-only-exception": 1})
        rule, _ = convert_network("@@||ok.jp^$document", Stats())
        self.assertEqual(rule["action"]["type"], "ignore-previous-rules")

    def test_mixed_domains_skipped(self):
        # 総点検 C15：domain=a.com|~b.a.com の除外を黙って落とさない
        self.assertIsNone(convert_network("||ads.jp^$domain=a.com|~b.a.com", Stats())[0])


if __name__ == "__main__":
    unittest.main()
