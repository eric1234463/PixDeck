"""anniv 插件的文案计算(纯函数 render), 不碰设备。
运行: python3 -m unittest tests.test_anniv
"""
import datetime, importlib.util, json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("plugin_anniv", os.path.join(ROOT, "plugins", "anniv", "plugin.py"))
anniv = importlib.util.module_from_spec(spec); spec.loader.exec_module(anniv)
D = datetime.date


class TestScreen(unittest.TestCase):
    NAMES = "AMY & BEN"
    START = D(2019, 2, 14)

    _KEEP = object()                  # 用哨兵, 因为 start=None 本身就是"日期没填对"这个被测状态

    def spec(self, item, today, start=_KEEP):
        return anniv.screen(item, today, self.START if start is self._KEEP else start, self.NAMES)

    def text(self, item, today, start=_KEEP):
        return self.spec(item, today, start)["text"]

    def test_short_names_show_in_full(self):
        sp = anniv.screen("names", D(2026, 9, 20), self.START, "AMY & BEN")
        self.assertEqual((sp["text"], sp["right"]), ("AMY", "BEN"))

    def test_long_names_fall_back_to_initials(self):
        # ERIC + CYNTHIA = 66px + 爱心, 52px 屏放不下 -> 只留首字母
        sp = anniv.screen("names", D(2026, 9, 20), self.START, "ERIC & CYNTHIA")
        self.assertEqual((sp["text"], sp["right"]), ("E", "C"))

    def test_one_name_leaves_the_right_side_empty(self):
        self.assertEqual(anniv.two_names("KIRBY"), ("KIRBY", ""))
        sp = anniv.screen("names", D(2026, 9, 20), self.START, "KIRBY")
        self.assertEqual(len(anniv.build_frame(sp, "#FFF", 6)["text"]), 1)

    def test_both_characters_flank_the_number(self):
        f = anniv.build_frame(self.spec("days", D(2026, 9, 20)), "#FF6FB5", 6)
        xs = [(d["db"][0], d["db"][2]) for d in f["draw"]]
        self.assertEqual(xs, [(0, anniv.PAIR_W), (anniv.SCREEN_W - anniv.PAIR_W, anniv.PAIR_W)])
        # 左右留一样宽的缝: 数字贴到公仔脸上就是之前被挑出来的毛病
        left_gap = f["text"][0]["x"] - anniv.PAIR_W
        right_gap = anniv.SCREEN_W - anniv.PAIR_W - (f["text"][0]["x"] + 4 * anniv.CHAR_W)
        self.assertEqual((left_gap, right_gap), (anniv.PAIR_PAD, anniv.PAIR_PAD))

    def test_five_digit_days_drop_yukio_instead_of_overflowing(self):
        # 天数满五位(6*5=30px > 中间的 24px)时让出小人, 用右边 36px, 而不是压到脸上
        sp = anniv.screen("days", D(2046, 7, 3), self.START, self.NAMES)
        self.assertEqual((sp["layout"], sp["text"]), ("wide", "10001"))

    def test_days_together(self):
        self.assertEqual(self.text("days", D(2026, 9, 20)), "2775")

    def test_bad_date_still_shows_the_names(self):
        self.assertIsNone(anniv.parse_date("12/08/2012"))
        self.assertEqual(self.text("names", D(2026, 9, 20), None), "AMY")
        self.assertEqual(self.text("days", D(2026, 9, 20), None), "SET?")

    def test_every_screen_fits(self):
        # 每种版式各自的宽度上限: names 整屏 52px, pair 中间 24px, wide 右侧 36px
        limits = {"names": anniv.SCREEN_W, "pair": anniv.PAIR_TEXT_W, "wide": anniv.TEXT_W}
        for today in (D(2026, 9, 20), D(2026, 8, 12), D(2039, 8, 11), D(2060, 1, 1)):
            for item in anniv.ITEMS:
                sp = anniv.screen(item, today, self.START, self.NAMES)
                used = (len(sp["text"]) + len(sp["right"])) * anniv.CHAR_W
                if sp["layout"] == "names":
                    used += anniv.HEART_W + 2
                self.assertLessEqual(used, limits[sp["layout"]], f"{item} {today} {sp}")

    def test_names_frame_stays_on_screen(self):
        f = anniv.build_frame(anniv.screen("names", D(2026, 9, 20), self.START, self.NAMES),
                              "#FF6FB5", 6)
        right = f["text"][-1]["x"] + len("BEN") * anniv.CHAR_W
        self.assertLessEqual(right, anniv.SCREEN_W)
        self.assertGreaterEqual(f["text"][0]["x"], 0)

    def test_sprites_are_rectangular_and_fit(self):
        for art in (anniv.KIRBY, anniv.YUKIO, anniv.HEART):
            rows = art.strip("\n").split("\n")
            self.assertEqual(len(set(len(r) for r in rows)), 1, "行宽不一致")
            self.assertLessEqual(len(rows), anniv.SCREEN_H)

    def test_characters_are_left_right_symmetric(self):
        # 公仔自己左右不对称的话, 摆在两边看起来就是歪的(卡比的脚以前就偏左)
        for art in (anniv.KIRBY, anniv.YUKIO, anniv.HEART):
            for row in art.strip("\n").split("\n"):
                blank = lambda r: len(r) - len(r.lstrip("."))
                self.assertEqual(blank(row), blank(row[::-1]), f"{row} 左右留白不等")

    def test_db_pixel_count_matches_its_box(self):
        f = anniv.build_frame(self.spec("days", D(2026, 9, 20)), "#FF6FB5", 6)
        for d in f["draw"]:
            x, y, w, h, px = d["db"]
            self.assertEqual(len(px), w * h)                   # db 像素数必须等于 w*h
            self.assertLessEqual(x + w, anniv.SCREEN_W)
            self.assertLessEqual(y + h, anniv.SCREEN_H)


class TestRemember(unittest.TestCase):
    """写回 .pixbar.json 必须是读-改-写: 同一份文件还存着 device / transport。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.tmp.close()
        self.orig = anniv.CONFIG_PATH
        anniv.CONFIG_PATH = self.tmp.name

    def tearDown(self):
        anniv.CONFIG_PATH = self.orig
        os.unlink(self.tmp.name)

    def test_keeps_other_keys(self):
        json.dump({"device": "10.0.0.5", "transport": "http"}, open(self.tmp.name, "w"))
        anniv._remember("date", "2019-02-14")
        anniv._remember("names", "ERIC & CYNTHIA")
        d = json.load(open(self.tmp.name))
        self.assertEqual(d["device"], "10.0.0.5")
        self.assertEqual(d["transport"], "http")
        self.assertEqual(d["anniv_date"], "2019-02-14")
        self.assertEqual(d["anniv_names"], "ERIC & CYNTHIA")
        self.assertEqual(anniv._saved("date"), "2019-02-14")

    def test_corrupt_file_is_not_clobbered(self):
        open(self.tmp.name, "w").write("{not json")
        anniv._remember("date", "2019-02-14")
        self.assertEqual(open(self.tmp.name).read(), "{not json")
        self.assertEqual(anniv._saved("date"), "")

    def test_missing_file_is_created(self):
        os.unlink(self.tmp.name)
        anniv._remember("date", "2019-02-14")
        self.assertEqual(json.load(open(self.tmp.name)), {"anniv_date": "2019-02-14"})

    def test_half_typed_date_is_not_saved(self):
        # 面板里一个字一个字打 "2019-02-1" 的中间态不该覆盖掉已存的好日期
        json.dump({"anniv_date": "2019-02-14"}, open(self.tmp.name, "w"))
        anniv._seen.clear()
        anniv._current({"date": "2019-02-1", "names": "ERIC & CYNTHIA"})
        self.assertEqual(json.load(open(self.tmp.name))["anniv_date"], "2019-02-14")


if __name__ == "__main__":
    unittest.main()
