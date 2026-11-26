import os
import re
import time
import json
import requests
import traceback
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================
# [PART 1] 설정 (서버용 최적화)
# ==========================================

# 1. 데이터 보낼 주소 (대표님 CSV 주소)
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbyFm0Xu2BY3T_JcNgQqQssOhY1hJ-UH1A-GW2hhRLT-jDkgevFL74Bl1iEXUFPW1pleBw/exec"

# 2. 유튜브 API 키
YOUTUBE_API_KEY = "AIzaSyDFFZNYygA85qp5p99qUG2Mh8Kl5qoLip4"

# 3. 크롤링 대상 URL
TARGET_URLS = {
    "KR_Daily_Trending": "https://charts.youtube.com/charts/TrendingVideos/kr/RightNow",
    "US_Daily_Trending": "https://charts.youtube.com/charts/TrendingVideos/us/RightNow",
    "KR_Daily_Top_MV": "https://charts.youtube.com/charts/TopVideos/kr/daily",
    "US_Daily_Top_MV": "https://charts.youtube.com/charts/TopVideos/us/daily",
    "EG_Daily_Top_MV": "https://charts.youtube.com/charts/TopVideos/eg/daily",
    "SA_Daily_Top_MV": "https://charts.youtube.com/charts/TopVideos/sa/daily",
    "KR_Weekly_Top_MV": "https://charts.youtube.com/charts/TopVideos/kr/weekly",
    "US_Weekly_Top_MV": "https://charts.youtube.com/charts/TopVideos/us/weekly",
    "KR_Weekly_Top_Songs": "https://charts.youtube.com/charts/TopSongs/kr/weekly",
    "US_Weekly_Top_Songs": "https://charts.youtube.com/charts/TopSongs/us/weekly",
    "KR_Daily_Top_Shorts": "https://charts.youtube.com/charts/TopShortsSongs/kr/daily",
    "US_Daily_Top_Shorts": "https://charts.youtube.com/charts/TopShortsSongs/us/daily"
}

BILLBOARD_URLS = {
    "Billboard_Hot100": "https://www.billboard.com/charts/hot-100/",
    "Billboard_200": "https://www.billboard.com/charts/billboard-200/",
    "Billboard_Global200": "https://www.billboard.com/charts/billboard-global-200/"
}

EXTRA_URLS = {
    "Melon_Daily_Top100": "https://www.melon.com/chart/day/index.htm",
    "Genie_Daily_Top200": "https://www.genie.co.kr/chart/top200",
    "Spotify_Global_Daily": "https://kworb.net/spotify/country/global_daily.html",
    "Spotify_US_Daily": "https://kworb.net/spotify/country/us_daily.html",
    "Spotify_KR_Daily": "https://kworb.net/spotify/country/kr_daily.html"
}

# ================= 유틸리티 함수 =================

def parse_count_strict(text):
    """차트 표기를 정수로 변환한다.

    '739,305' / '10,546,503' / '3.9만' / '1.2천' / '<10K' / '1.5M' 형태를 모두 처리한다.
    '<10K' 처럼 상한만 표시된 값은 표시된 경계값(10000)으로 계산한다.
    """
    if not text: return 0
    t = str(text).lower().strip().replace(',', '').replace(' ', '')
    t = re.sub(r'^[<>≈~]+', '', t)

    multiplier = 1
    # 한국어 단위 → 영문 단위 순서로 확인 (긴 단위 우선)
    for token, multi in (('조', 10**12), ('억', 10**8), ('만', 10**4), ('천', 10**3),
                         ('b', 10**9), ('m', 10**6), ('k', 10**3)):
        if token in t:
            multiplier = multi
            t = t.replace(token, '')
            break

    clean = re.sub(r'[^\d.]', '', t)
    if not clean: return 0
    try:
        val = float(clean)
        return int(val * multiplier)
    except: return 0

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def get_driver():
    chrome_options = Options()
    
    # [핵심 수정] Github Actions에서는 무조건 headless 켜야 함
    chrome_options.add_argument("--headless=new") 
    
    # [핵심 수정] 유튜브 봇 탐지 우회 옵션 추가
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--lang=en-US")
    # 자동화 제어 메시지 제거 (봇 탐지 회피)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") 
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# ================= 유튜브 API & 파싱 =================

# charts.youtube.com은 2026년 11월 `ytmc-v2-app`으로 개편되면서
# 차트 테이블(ytmc-entry-row)과 그 안의 순위/제목/아티스트/조회수가
# 전부 중첩 Shadow DOM 안으로 들어갔다.
# driver.page_source(=document.documentElement.outerHTML)에는 Shadow DOM 내부가
# 직렬화되지 않아 BeautifulSoup로는 행도 값도 읽을 수 없다.
# → Selenium execute_script로 document의 모든 shadowRoot를 재귀 순회해 직접 뽑는다.

# 모든 shadowRoot를 순회하며 ytmc-entry-row 개수만 센다 (렌더링/스크롤 대기용)
YT_ROW_COUNT_JS = """
return (function () {
  var count = 0;
  var walk = function (root) {
    if (!root.querySelectorAll) return;
    count += root.querySelectorAll('ytmc-entry-row').length;
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      if (all[i].shadowRoot) walk(all[i].shadowRoot);
    }
  };
  walk(document);
  return count;
})();
"""

# 차트 테이블 헤더(컬럼명)와 행 데이터를 통째로 뽑아 JSON으로 돌려준다.
# 반환: {row_count: int, header: [컬럼명...], rows: [{rank, title, artist, video_id, metrics:[...]}]}
# metrics는 header와 같은 순서/개수로 대응된다 (예: 발매일, 어제, 차트에 머문 기간, 주간 조회수).
YT_CHART_EXTRACT_JS = """
var collectRows = function (root) {
  var rows = [];
  var walk = function (el) {
    if (!el.querySelectorAll) return;
    var found = el.querySelectorAll('ytmc-entry-row');
    for (var i = 0; i < found.length; i++) rows.push(found[i]);
    var all = el.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      if (all[j].shadowRoot) walk(all[j].shadowRoot);
    }
  };
  walk(root);
  return rows;
};

var findHeaderMetrics = function (root) {
  var result = null;
  var walk = function (el) {
    if (result || !el.querySelectorAll) return;
    var hs = el.querySelectorAll('.table-header-metric');
    if (hs.length) {
      result = [];
      for (var i = 0; i < hs.length; i++) result.push(hs[i].textContent.trim());
      return;
    }
    var all = el.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      if (all[j].shadowRoot) {
        walk(all[j].shadowRoot);
        if (result) return;
      }
    }
  };
  walk(root);
  return result || [];
};

var videoIdOf = function (root) {
  var candidates = [
    root.querySelector('#entity-title'),
    root.querySelector('img#thumbnail'),
    root.querySelector('img.tracks-thumbnail')
  ];
  for (var i = 0; i < candidates.length; i++) {
    var el = candidates[i];
    if (!el) continue;
    var ep = el.getAttribute('endpoint');
    if (ep) {
      try {
        var j = JSON.parse(ep);
        var url = (j.urlEndpoint && j.urlEndpoint.url) || '';
        var m = url.match(/[?&]v=([A-Za-z0-9_-]{11})/);
        if (m) return m[1];
      } catch (e) {}
    }
    var src = el.getAttribute('src');
    if (src) {
      var m2 = src.match(/[/]vi(?:_webp)?[/]([A-Za-z0-9_-]{11})/);
      if (m2) return m2[1];
    }
  }
  var m3 = root.innerHTML.match(/[?&]v=([A-Za-z0-9_-]{11})/);
  return m3 ? m3[1] : '';
};

return (function () {
  var rows = collectRows(document);
  var header = findHeaderMetrics(document);
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    var light = rows[i];
    var root = light.shadowRoot || light;
    var titleEl = root.querySelector('#entity-title') || root.querySelector('.title');
    var rankEl = root.querySelector('#rank');

    var artists = [];
    var artistEls = root.querySelectorAll('span.artistName');
    for (var a = 0; a < artistEls.length; a++) artists.push(artistEls[a].textContent.trim());
    if (!artists.length) {
      var sub = root.querySelector('#artist-names') || root.querySelector('.subtitle');
      if (sub) artists.push(sub.textContent.trim());
    }

    var metrics = [];
    var metricEls = root.querySelectorAll('.metric');
    for (var k = 0; k < metricEls.length; k++) metrics.push(metricEls[k].textContent.trim());

    out.push({
      rank: rankEl ? rankEl.textContent.trim() : '',
      title: titleEl ? titleEl.textContent.trim() : '',
      artist: artists.filter(function (x) { return x; }).join(', '),
      video_id: videoIdOf(root),
      metrics: metrics
    });
  }
  return { row_count: rows.length, header: header, rows: out };
})();
"""

def _yt_row_count(driver):
    try:
        return int(driver.execute_script(YT_ROW_COUNT_JS) or 0)
    except Exception:
        return 0

def _wait_for_yt_rows(driver, timeout=25):
    """차트 테이블(Shadow DOM 안의 ytmc-entry-row)이 렌더링될 때까지 기다린다."""
    end = time.time() + timeout
    while time.time() < end:
        count = _yt_row_count(driver)
        if count:
            return count
        time.sleep(0.5)
    return 0

def _scroll_yt_chart(driver, max_rounds=12, pause=0.4):
    """행이 지연 렌더링되는 경우를 대비해 아래로 내리며 행 수가 늘어나는지 확인한다."""
    last = _yt_row_count(driver)
    for _ in range(max_rounds):
        driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
        time.sleep(pause)
        current = _yt_row_count(driver)
        at_bottom = driver.execute_script(
            "return (window.innerHeight + window.pageYOffset) >= (document.body.scrollHeight - 5);"
        )
        if at_bottom and current == last:
            break
        last = current
    driver.execute_script("window.scrollTo(0, 0);")

def yt_payload_to_rows(chart_name, payload):
    """execute_script로 뽑은 ytmc-v2-app payload를 기존 출력 스키마로 변환한다.

    출력 스키마는 기존과 동일: Date, Chart, Rank, Title, Artist, Video_ID, Views.
    조회수는 헤더에서 '조회수'(예: 주간 조회수) 컬럼 위치를 찾아 그 위치의 metric을 쓴다.
    브라우저 없이 payload만으로 동작하므로 단위 테스트 대상이다.
    """
    payload = payload or {}
    rows = payload.get("rows") or []
    header = payload.get("header") or []

    views_idx = -1
    for i, name in enumerate(header):
        if "조회수" in str(name):
            views_idx = i
            break

    today = datetime.now().strftime("%Y-%m-%d")
    data_list = []

    for row in rows:
        title = clean_text(row.get("title") or "")
        if not title:
            continue

        metrics = [clean_text(m) for m in (row.get("metrics") or [])]
        if 0 <= views_idx < len(metrics):
            views_raw = metrics[views_idx]
        elif metrics:
            views_raw = metrics[-1]
        else:
            views_raw = ""

        rank_txt = str(row.get("rank") or "").strip()
        data_list.append({
            "Date": today,
            "Chart": chart_name,
            "Rank": int(rank_txt) if rank_txt.isdigit() else len(data_list) + 1,
            "Title": title,
            "Artist": clean_text(row.get("artist") or ""),
            "Video_ID": row.get("video_id") or "",
            "Views": parse_count_strict(views_raw)
        })

    return data_list

# 영상 페이지의 'Shorts 생성 수' 표기 (로케일별)
SHORTS_KO_PATTERN = re.compile(r'Shorts\s*동영상\s*([\d.,]+)\s*(만|천|억|조)?')
SHORTS_EN_PATTERN = re.compile(r'([\d.,]+)\s*([KMB])\s*Shorts', re.IGNORECASE)
SHORTS_LOOSE_PATTERN = re.compile(r'Shorts\s*([\d.,]+)\s*(만|천|억|조|K|M|B)?', re.IGNORECASE)

def parse_shorts_creation_count(body_text):
    """영상 페이지 텍스트에서 Shorts 생성 수를 뽑는다.

    'Shorts 동영상 3.9만개'(ko) / '39K Shorts'(en) 둘 다 처리한다. 못 찾으면 0.
    """
    if not body_text:
        return 0
    match = SHORTS_KO_PATTERN.search(body_text)
    if match:
        return parse_count_strict(match.group(1) + (match.group(2) or ""))
    match = SHORTS_EN_PATTERN.search(body_text)
    if match:
        return parse_count_strict(match.group(1) + match.group(2))
    match = SHORTS_LOOSE_PATTERN.search(body_text)
    if match:
        return parse_count_strict(match.group(1) + (match.group(2) or ""))
    return 0

def get_shorts_creation_count(driver, video_id):
    """Shorts 차트용 'Shorts 생성 수'를 영상의 source 페이지에서 읽는다."""
    if not video_id: return 0
    url = f"https://www.youtube.com/source/{video_id}/shorts"
    try:
        driver.get(url)

        # 생성 수 문구가 그려질 때까지 짧게 기다린다 (최대 8초)
        body_text = ""
        end = time.time() + 8
        while time.time() < end:
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                body_text = ""
            if (SHORTS_KO_PATTERN.search(body_text)
                    or SHORTS_EN_PATTERN.search(body_text)
                    or SHORTS_LOOSE_PATTERN.search(body_text)):
                break
            time.sleep(0.5)

        return parse_shorts_creation_count(body_text)
    except: return 0

def get_views_from_api(video_ids):
    if not video_ids: return {}
    url = "https://www.googleapis.com/youtube/v3/videos"
    stats_map = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        params = {"part": "statistics", "id": ",".join(chunk), "key": YOUTUBE_API_KEY}
        try:
            res = requests.get(url, params=params).json()
            if "items" in res:
                for item in res["items"]:
                    vid = item["id"]
                    view_count = int(item["statistics"].get("viewCount", 0))
                    stats_map[vid] = view_count
        except: pass
    return stats_map

# ================= 크롤러 메인 로직 =================

# 1. 유튜브 차트 크롤러 (ytmc-v2-app Shadow DOM 대응)
def scrape_youtube_chart(chart_name, url, driver):
    print(f"🚀 Scraping YouTube: {chart_name}...")
    try:
        driver.get(url)

        row_count = _wait_for_yt_rows(driver)
        if row_count == 0:
            print(f"   ⚠️ Warning: ytmc-entry-row 0개. Page Title: {driver.title} / URL: {driver.current_url}")
            return []

        _scroll_yt_chart(driver)
        payload = driver.execute_script(YT_CHART_EXTRACT_JS) or {}
        rows = payload.get("rows") or []
        header = payload.get("header") or []
        print(f"   -> DOM rows: {len(rows)} / columns: {header}")

        data_list = yt_payload_to_rows(chart_name, payload)
        if data_list:
            sample = data_list[0]
            print(f"   -> 예시: #{sample['Rank']} {sample['Title'][:40]} / {sample['Artist'][:20]} / {sample['Video_ID']} / Views={sample['Views']}")

        # '<10K' 처럼 상한만 표시된 조회수는 경계값으로 계산된다는 점을 로그로 남긴다.
        if header:
            views_idx = next((i for i, h in enumerate(header) if '조회수' in str(h)), -1)
            if views_idx >= 0:
                bounded = sum(
                    1 for row in (payload.get('rows') or [])
                    if views_idx < len(row.get('metrics') or []) and '<' in str(row['metrics'][views_idx])
                )
                if bounded:
                    print(f"   -> 상한 표기('<10K') 행: {bounded}개 → 표시된 경계값으로 계산")

        # Shorts 차트는 기존과 동일하게 'Shorts 생성 수'를 우선 사용한다.
        # (실패하면 DOM의 주간 조회수 값이 그대로 남는다)
        if "Shorts" in chart_name:
            filled = 0
            for item in data_list:
                creations = get_shorts_creation_count(driver, item["Video_ID"])
                if creations:
                    item["Views"] = creations
                    filled += 1
            print(f"   -> Shorts 생성 수 확보: {filled}/{len(data_list)}")

        print(f"   -> Captured {len(data_list)} items.")
        return data_list

    except Exception as e:
        print(f"❌ YouTube Error ({chart_name}): {e}")
        print(traceback.format_exc())
        return []

# 2. 빌보드 크롤러 (안전장치 강화판 + 날짜는 오늘 날짜로 원상복구)
def scrape_billboard_official(driver, chart_key, url):
    print(f"🇺🇸 [SafeMode] Scraping {chart_key}...")
    max_retries = 2
    for attempt in range(max_retries):
        try:
            driver.get(url)
            # 천천히 스크롤 (빌보드 필수)
            for i in range(1, 11):
                driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {i/10});")
                time.sleep(0.5)
            time.sleep(3)

            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "o-chart-results-list-row-container"))
                )
            except:
                print("   ⚠️ Timeout: Container not found. Retrying...")
                if attempt < max_retries - 1: continue
                return []

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # [원상복구] 빌보드는 그냥 오늘 날짜로 저장
            today = datetime.now().strftime("%Y-%m-%d")

            rows = soup.select('div.o-chart-results-list-row-container')
            print(f"   -> Found {len(rows)} row containers.")

            if len(rows) == 0:
                print("   ⚠️ 0 rows. Refreshing page...")
                continue 

            data = []
            
            for idx, row in enumerate(rows):
                try:
                    rank = idx + 1
                    try:
                        rank_tag = row.select_one('span.c-label.a-font-primary-bold-l')
                        if rank_tag and rank_tag.text.strip().isdigit():
                            rank = int(rank_tag.text.strip())
                    except: pass

                    title = "Unknown"
                    title_tag = row.select_one('h3.c-title') 
                    if not title_tag: title_tag = row.select_one('h3#title-of-a-story') 
                    if not title_tag: title_tag = row.select_one('h3') 
                    
                    if title_tag: title = title_tag.get_text(strip=True)

                    artist = "Unknown"
                    if title_tag:
                        parent_li = title_tag.find_parent('li')
                        if parent_li:
                            artist_tag = parent_li.select_one('span.c-label.a-no-trucate')
                            if artist_tag:
                                artist = artist_tag.get_text(strip=True)
                            else:
                                full_text = parent_li.get_text(strip=True)
                                if title in full_text:
                                    remain = full_text.replace(title, "").strip()
                                    if len(remain) > 1: artist = remain

                    if title == "Unknown": continue

                    data.append({
                        "Date": today, "Chart": chart_key, "Rank": rank,
                        "Title": title, "Artist": artist, "Video_ID": "", "Views": 0
                    })
                except: continue
            
            if len(data) > 0:
                print(f"✅ {chart_key}: Captured {len(data)} rows ({today}).")
                return data

        except Exception as e:
            print(f"❌ Error on attempt {attempt+1}: {e}")
            time.sleep(3)

    return []

# 3. 멜론
def scrape_melon():
    print("🍈 Scraping Melon Daily...")
    url = EXTRA_URLS["Melon_Daily_Top100"]
    headers = {'User-Agent': 'Mozilla/5.0'}
    data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.select('tr.lst50, tr.lst100')
        for row in rows:
            try:
                rank = int(row.select_one('span.rank').text)
                title = row.select_one('div.ellipsis.rank01 > span > a').text.strip()
                artist = row.select_one('div.ellipsis.rank02 > a').text.strip()
                data.append({
                    "Date": today, "Chart": "Melon_Daily_Top100", "Rank": rank,
                    "Title": title, "Artist": artist, "Video_ID": "", "Views": 0
                })
            except: continue
        print(f"✅ Melon: {len(data)} rows")
    except Exception as e: print(f"❌ Melon Error: {e}")
    return data

# 4. 지니
def scrape_genie():
    print("🧞 Scraping Genie Daily...")
    headers = {'User-Agent': 'Mozilla/5.0'}
    data = []
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        for page in range(1, 3):
            res = requests.get(f"{EXTRA_URLS['Genie_Daily_Top200']}?pg={page}", headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.select('tbody > tr.list')
            for row in rows:
                try:
                    rank = int(row.select_one('td.number').text.split()[0])
                    title = row.select_one('a.title').text.strip()
                    artist = row.select_one('a.artist').text.strip()
                    data.append({
                        "Date": today, "Chart": "Genie_Daily_Top200", "Rank": rank,
                        "Title": title, "Artist": artist, "Video_ID": "", "Views": 0
                    })
                except: continue
        print(f"✅ Genie: {len(data)} rows")
    except Exception as e: print(f"❌ Genie Error: {e}")
    return data


# 5. Kworb (HTML 날짜 파싱 유지 - 스크린샷 요청 반영)
def scrape_kworb(chart_key, url):
    print(f"🟢 Scraping {chart_key}...")
    data = []
    
    # 기본값은 오늘이지만, HTML에서 날짜를 찾으면 덮어씌움
    chart_date = datetime.now().strftime("%Y-%m-%d")
    TARGET_HEADER_KEYWORD = "Streams"

    try:
        res = requests.get(url)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # --- [날짜 추출 로직 유지] ---
        try:
            # 스크린샷에 있는 .pagetitle 클래스 탐색
            pagetitle = soup.select_one('.pagetitle')
            if pagetitle:
                title_text = pagetitle.get_text()
                # 예: "Spotify Daily Chart - Global - 2025/12/03 |" 에서 날짜 추출
                match = re.search(r'(\d{4})/(\d{2})/(\d{2})', title_text)
                if match:
                    # YYYY/MM/DD -> YYYY-MM-DD 변환
                    chart_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                    print(f"   📅 Detected Date from HTML: {chart_date}")
        except Exception as de:
            print(f"   ⚠️ Date parse failed: {de}")
        # ---------------------------

        table = soup.find('table')
        if not table: return []

        headers = []
        thead = table.find('thead')
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all('th')]
        else:
            first_row = table.find('tr')
            headers = [td.get_text(strip=True) for td in first_row.find_all(['td', 'th'])]

        target_idx = -1
        title_idx = -1
        for i, h in enumerate(headers):
            if "Artist" in h or "Title" in h: title_idx = i
            if TARGET_HEADER_KEYWORD in h and "+" not in h:
                target_idx = i
                break
        
        if target_idx == -1: target_idx = 6 
        if title_idx == -1: title_idx = 2

        rows = soup.select('tbody > tr')
        for row in rows:
            cols = row.find_all('td')
            if not cols: continue
            try:
                rank_txt = cols[0].get_text(strip=True)
                if not rank_txt.isdigit(): continue
                rank = int(rank_txt)

                full_text = clean_text(cols[title_idx].get_text())
                if " - " in full_text:
                    parts = full_text.split(" - ", 1)
                    artist = parts[0].strip()
                    title = parts[1].strip()
                else:
                    artist = "Unknown"
                    title = full_text

                val_raw = cols[target_idx].get_text(strip=True)
                val_clean = re.sub(r'[^\d]', '', val_raw)
                final_val = int(val_clean) if val_clean else 0

                data.append({
                    "Date": chart_date, # 추출한 날짜 사용
                    "Chart": chart_key,
                    "Rank": rank,
                    "Title": title,
                    "Artist": artist,
                    "Video_ID": "",
                    "Views": final_val
                })
            except: continue
        print(f"✅ {chart_key}: {len(data)} rows ({chart_date})")
    except Exception as e: print(f"❌ Kworb Error ({chart_key}): {e}")
    return data

# ================= 메인 실행 =================
if __name__ == "__main__":
    driver = None
    final_data = [] 

    try:
        print("=== [MusicDeal] Github Actions Crawler ===")
        
        # 1. 브라우저 크롤링 (YouTube / Billboard)
        try:
            driver = get_driver()
            
            # YouTube
            for name, url in TARGET_URLS.items():
                chart_data = scrape_youtube_chart(name, url, driver)
                # Trending 보정: API 키가 만료(API key expired) 상태라 DOM 조회수를 우선하고,
                # DOM에서 값을 못 얻은 경우(Views=0)에만 API 값을 보충으로 쓴다.
                if "Trending" in name:
                    ids = [d["Video_ID"] for d in chart_data if d["Video_ID"]]
                    if ids:
                        api_stats = get_views_from_api(ids)
                        for item in chart_data:
                            if not item["Views"] and item["Video_ID"] in api_stats:
                                item["Views"] = api_stats[item["Video_ID"]]
                final_data.extend(chart_data)
            
            # Billboard
            print("\n>>> Billboard...")
            for b_name, b_url in BILLBOARD_URLS.items():
                b_data = scrape_billboard_official(driver, b_name, b_url)
                final_data.extend(b_data)

        except Exception as sel_e:
            print(f"🔥 Selenium Fatal Error: {sel_e}")
            print(traceback.format_exc())
        finally:
            if driver: driver.quit()

        # 2. Requests 크롤링
        print("\n>>> Domestic & Kworb...")
        final_data.extend(scrape_melon())
        final_data.extend(scrape_genie())
        for key, url in EXTRA_URLS.items():
            final_data.extend(scrape_kworb(key, url))

        # 3. 데이터 전송
        print(f"\n📊 Total Data: {len(final_data)} rows")
        
        if len(final_data) > 0:
            print(f"🚀 Sending to: {WEBHOOK_URL[:40]}...")
            chunk_size = 3000
            for i in range(0, len(final_data), chunk_size):
                chunk = final_data[i:i+chunk_size]
                try:
                    response = requests.post(WEBHOOK_URL, json=chunk)
                    print(f"   -> Chunk {i//chunk_size + 1}: {response.status_code}")
                except Exception as e:
                    print(f"❌ Send Error: {e}")
                time.sleep(1)
            print("✨ Success.")
        else:
            print("⚠️ 0 rows collected. Check logs above.")

    except Exception as main_e:
        print("🔥 Fatal Script Error.")
        print(traceback.format_exc())


