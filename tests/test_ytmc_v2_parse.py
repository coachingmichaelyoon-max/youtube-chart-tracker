"""ytmc-v2-app Shadow DOM 파싱 회귀 테스트.

`scrape_youtube_chart`가 Selenium execute_script로 뽑는 payload(4개 차트 유형의 실제 DOM 값)를
`yt_payload_to_rows`에 그대로 넣어, 기존 출력 스키마(Date/Chart/Rank/Title/Artist/Video_ID/Views)로
정확히 변환되는지 확인한다. 브라우저/Selenium 없이 돈다.

    python tests/test_ytmc_v2_parse.py        (또는 python -m unittest)
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

# main.py는 selenium/bs4를 top-level에서 import한다. 이 환경에 없어도 테스트가 돌도록 stub을 채운다.
for _mod in (
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome",
    "selenium.webdriver.chrome.options",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.common",
    "selenium.webdriver.common.by",
    "selenium.webdriver.support",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "webdriver_manager",
    "webdriver_manager.chrome",
    "bs4",
    "requests",
):
    try:
        __import__(_mod)
    except ImportError:
        sys.modules[_mod] = mock.MagicMock()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import main  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


class ParseCountTest(unittest.TestCase):
    def test_plain_and_grouped_numbers(self):
        self.assertEqual(main.parse_count_strict("739,305"), 739305)
        self.assertEqual(main.parse_count_strict("10,546,503"), 10546503)
        self.assertEqual(main.parse_count_strict(" 1,425,843 "), 1425843)

    def test_display_bounds_and_units(self):
        # YouTube가 '1만 미만'을 '<10K'로 표기 → 표시된 경계값으로 계산
        self.assertEqual(main.parse_count_strict("<10K"), 10000)
        self.assertEqual(main.parse_count_strict("1.5K"), 1500)
        self.assertEqual(main.parse_count_strict("2.4M"), 2400000)
        # 한국어 단위 (Shorts 생성 수 표기)
        self.assertEqual(main.parse_count_strict("3.9만"), 39000)
        self.assertEqual(main.parse_count_strict("3.9만개"), 39000)
        self.assertEqual(main.parse_count_strict("1.2천"), 1200)

    def test_empty_and_placeholder(self):
        self.assertEqual(main.parse_count_strict(""), 0)
        self.assertEqual(main.parse_count_strict(None), 0)
        self.assertEqual(main.parse_count_strict("-"), 0)
        self.assertEqual(main.parse_count_strict("New"), 0)


class ChartFixtureTest(unittest.TestCase):
    """4개 차트 유형 실제 DOM payload → 기존 출력 스키마."""

    def check_schema(self, item):
        self.assertEqual(
            sorted(item.keys()),
            sorted(["Date", "Chart", "Rank", "Title", "Artist", "Video_ID", "Views"]),
        )
        self.assertEqual(len(item["Date"]), 10)  # YYYY-MM-DD
        self.assertIsInstance(item["Rank"], int)
        self.assertIsInstance(item["Views"], int)
        self.assertTrue(item["Title"])

    def test_topvideos_daily_kr(self):
        fixture = load_fixture("ytmc_v2_topvideos_kr_daily.json")
        rows = main.yt_payload_to_rows(fixture["chart"], fixture["payload"])

        self.assertEqual(len(rows), 4)
        for item in rows:
            self.check_schema(item)

        top = rows[0]
        self.assertEqual(top["Chart"], "KR_Daily_Top_MV")
        self.assertEqual(top["Rank"], 1)
        self.assertEqual(top["Title"], "꿈꾸던 어른이 되었나요?")
        self.assertEqual(top["Artist"], "머무르")
        self.assertEqual(top["Video_ID"], "GxChUrrY4bc")
        self.assertEqual(top["Views"], 739305)  # 이전 파서가 놓쳤던 값

        # 여러 아티스트는 ', '로 합쳐진다.
        self.assertEqual(rows[3]["Artist"], "A2O LITE, A2O")
        self.assertEqual(rows[3]["Views"], 147908)

    def test_topsongs_weekly_kr(self):
        fixture = load_fixture("ytmc_v2_topsongs_kr_weekly.json")
        rows = main.yt_payload_to_rows(fixture["chart"], fixture["payload"])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Views"], 10546503)
        self.assertEqual(rows[1]["Views"], 6065034)

        # 곡 앨범아트가 아닌 entity-title endpoint에서도 Video ID를 얻는다.
        self.assertEqual(rows[2]["Video_ID"], "Mmw-PpWhKFg")
        self.assertEqual(rows[2]["Artist"], "K/DA, League of Legends, i-dle (아이들), Mimi Webb")
        self.assertEqual(rows[2]["Views"], 1425843)

    def test_trending_rightnow_kr(self):
        # 헤더 가운데 두 칸이 빈 문자열이어도 '주간 조회수' 위치(3)를 찾아 매핑해야 한다.
        fixture = load_fixture("ytmc_v2_trending_kr_rightnow.json")
        rows = main.yt_payload_to_rows(fixture["chart"], fixture["payload"])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Title"], "기립 (GiiiRIP)")
        self.assertEqual(rows[0]["Video_ID"], "IQI2qaqHFlg")
        # '<10K'는 경계값 10000으로 계산된다(실제 조회수는 1만 미만).
        self.assertEqual(rows[0]["Views"], 10000)
        self.assertEqual([r["Rank"] for r in rows], [1, 2, 30])

    def test_topshorts_daily_kr(self):
        fixture = load_fixture("ytmc_v2_topshorts_kr_daily.json")
        rows = main.yt_payload_to_rows(fixture["chart"], fixture["payload"])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Views"], 10000)  # DOM은 '<10K' 상한만 제공
        self.assertEqual(rows[0]["Video_ID"], "GxChUrrY4bc")
        self.assertEqual(rows[2]["Title"], "CHOP!")


class RobustnessTest(unittest.TestCase):
    def test_skips_rows_without_title(self):
        payload = {
            "header": ["발매일", "주간 조회수"],
            "rows": [
                {"rank": "", "title": "", "artist": "x", "video_id": "", "metrics": ["-", "1,000"]},
                {"rank": "", "title": "살아있는 행", "artist": "y", "video_id": "abcdefghijk",
                 "metrics": ["-", "2,000"]},
            ],
        }
        rows = main.yt_payload_to_rows("TEST", payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Title"], "살아있는 행")
        self.assertEqual(rows[0]["Rank"], 1)  # 순위 텍스트가 없으면 순서로 채운다
        self.assertEqual(rows[0]["Views"], 2000)

    def test_views_header_missing_falls_back_to_last_metric(self):
        payload = {
            "header": [],
            "rows": [
                {"rank": "1", "title": "곡", "artist": "가수", "video_id": "",
                 "metrics": ["2026년 1월 1일", "3", "4,321"]},
            ],
        }
        rows = main.yt_payload_to_rows("TEST", payload)
        self.assertEqual(rows[0]["Views"], 4321)

    def test_empty_payload(self):
        self.assertEqual(main.yt_payload_to_rows("TEST", None), [])
        self.assertEqual(main.yt_payload_to_rows("TEST", {}), [])


class ShortsParserTest(unittest.TestCase):
    """Shorts 생성 수 표기(로케일별) 파싱."""

    def test_korean_label(self):
        body = "꿈꾸던 어른이 되었나요?\n@meomuru_official\n•\nShorts 동영상 3.9만개\n조회수 194만회"
        self.assertEqual(main.parse_shorts_creation_count(body), 39000)

    def test_english_label(self):
        body = "Some title\n39K Shorts\n"
        self.assertEqual(main.parse_shorts_creation_count(body), 39000)

    def test_missing_label(self):
        self.assertEqual(main.parse_shorts_creation_count(""), 0)
        self.assertEqual(main.parse_shorts_creation_count("그냥 영상 페이지"), 0)


class FakeDriver:
    """Selenium 드라이버 대신 execute_script 결과만 돌려주는 테스트용 객체."""

    def __init__(self, payload, row_count=100):
        self.payload = payload
        self.row_count = row_count
        self.title = "YouTube Music Charts"
        self.current_url = "https://charts.youtube.com/..."
        self.scripts = []

    def get(self, url):
        self.current_url = url

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if script is main.YT_ROW_COUNT_JS:
            return self.row_count
        if script is main.YT_CHART_EXTRACT_JS:
            return self.payload
        return None

    def find_element(self, *args, **kwargs):
        raise RuntimeError("no element in fake driver")


class FastTime:
    """대기 루프를 실제로 기다리지 않도록 시간을 빠르게 흐르는 대체 모듈."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ScrapeYoutubeChartTest(unittest.TestCase):
    """브라우저 대신 FakeDriver로 scrape_youtube_chart 오케스트레이션을 검증한다."""

    def run_chart(self, chart_name, fixture_name, **kwargs):
        fixture = load_fixture(fixture_name)
        driver = FakeDriver(fixture["payload"], **kwargs)
        with mock.patch.object(main, "time", FastTime()):
            return main.scrape_youtube_chart(chart_name, "https://charts.youtube.com/x", driver)

    def test_daily_mv_end_to_end(self):
        rows = self.run_chart("KR_Daily_Top_MV", "ytmc_v2_topvideos_kr_daily.json")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["Views"], 739305)
        self.assertEqual(rows[0]["Chart"], "KR_Daily_Top_MV")

    def test_weekly_songs_end_to_end(self):
        rows = self.run_chart("KR_Weekly_Top_Songs", "ytmc_v2_topsongs_kr_weekly.json")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Views"], 10546503)

    def test_shorts_chart_prefers_creation_count(self):
        # 영상 페이지 조회를 실제로 하지 않도록 생성 수 함수만 대체한다.
        with mock.patch.object(main, "get_shorts_creation_count", lambda driver, vid: 39000):
            rows = self.run_chart("KR_Daily_Top_Shorts", "ytmc_v2_topshorts_kr_daily.json")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["Views"] for r in rows], [39000, 39000, 39000])

    def test_shorts_falls_back_to_dom_views_when_lookup_fails(self):
        with mock.patch.object(main, "get_shorts_creation_count", lambda driver, vid: 0):
            rows = self.run_chart("KR_Daily_Top_Shorts", "ytmc_v2_topshorts_kr_daily.json")
        self.assertEqual(rows[0]["Views"], 10000)  # DOM의 '<10K'

    def test_zero_rows_returns_empty(self):
        rows = self.run_chart("KR_Daily_Top_MV", "ytmc_v2_topvideos_kr_daily.json", row_count=0)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
