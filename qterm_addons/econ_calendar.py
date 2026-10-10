"""미국 경제지표·FOMC 일정 (QTERM N1).

출처:
- BEA 공식 ICS: GDP, 개인소득·지출(PCE) 등 (2026-10-10 이 세션에서 응답 확인)
- BLS 공식 ICS: 고용, CPI, PPI 등 (이 세션에서는 403 — 연락처가 담긴
  User-Agent가 필요한 것으로 보임. PC에서 QTERM_USER_AGENT 설정 후 확인 대기)
- FOMC: 연준 공식 페이지에 ICS가 없어 날짜를 직접 관리한다(2026-10-09 원문 확인).

규약(엑셀코스피의 지난 회의 표시 오류에서 얻은 교훈):
- 지난 일정은 자동으로 숨기고, 다음 일정을 자동으로 고른다.
- 모든 항목에 출처와 "공식 일정 기준, 변경 가능" 문구를 붙인다.
"""

import re
from datetime import date, datetime, timedelta, timezone

from .net import FetchError, fetch_text

BEA_ICS_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"

KST = timezone(timedelta(hours=9), "KST")

# 연준 공식 일정(federalreserve.gov/monetarypolicy/fomccalendars.htm, 2026-10-09 확인).
# (회의 시작일, 회의 종료일=결정 발표일, SEP 발표 여부). 성명 발표는 종료일 14:00 ET.
FOMC_2026 = [
    (date(2026, 1, 27), date(2026, 1, 28), False),
    (date(2026, 3, 17), date(2026, 3, 18), True),
    (date(2026, 4, 28), date(2026, 4, 29), False),
    (date(2026, 6, 16), date(2026, 6, 17), True),
    (date(2026, 7, 28), date(2026, 7, 29), False),
    (date(2026, 9, 15), date(2026, 9, 16), True),
    (date(2026, 10, 27), date(2026, 10, 28), False),
    (date(2026, 12, 8), date(2026, 12, 9), True),
]
FOMC_SOURCE = "Federal Reserve Board (FOMC meeting calendar)"

# 제목 키워드 → (짧은 이름, 중요도 1~3). 위에서부터 먼저 맞는 것을 쓴다.
IMPORTANCE_RULES = [
    ("Employment Situation", "미국 고용보고서", 3),
    ("Consumer Price Index", "미국 CPI", 3),
    ("Personal Income and Outlays", "미국 PCE·개인소득", 3),
    ("GDP (", "미국 GDP", 2),  # 2026년 BEA 제목 형식: "GDP (Advance Estimate), ..."
    ("Gross Domestic Product,", "미국 GDP", 2),
    ("Gross Domestic Product (", "미국 GDP", 2),
    ("Producer Price Index", "미국 PPI", 2),
    ("Job Openings and Labor Turnover", "미국 JOLTS", 2),
    ("Employment Cost Index", "미국 고용비용지수", 2),
    ("Real Earnings", "미국 실질임금", 1),
    ("International Trade in Goods and Services", "미국 무역수지", 1),
    ("Import and Export Price", "미국 수출입물가", 1),
]


def _eastern_offset(utc_dt):
    """UTC 시각에 해당하는 미국 동부 시간 오프셋(EDT -4 / EST -5).

    tzdata가 없는 Windows 파이썬에서도 돌도록 2007년 이후 규칙을 직접 계산한다:
    3월 둘째 일요일 02:00 ~ 11월 첫째 일요일 02:00 (현지 시각) 서머타임.
    """
    year = utc_dt.year
    march = date(year, 3, 8)
    dst_start_day = march + timedelta(days=(6 - march.weekday()) % 7)
    nov = date(year, 11, 1)
    dst_end_day = nov + timedelta(days=(6 - nov.weekday()) % 7)
    dst_start = datetime(year, 3, dst_start_day.day, 7, tzinfo=timezone.utc)  # 02:00 EST
    dst_end = datetime(year, 11, dst_end_day.day, 6, tzinfo=timezone.utc)  # 02:00 EDT
    return timedelta(hours=-4) if dst_start <= utc_dt < dst_end else timedelta(hours=-5)


def eastern_to_utc(local_naive):
    """미국 동부 현지 시각(naive) → UTC 시각."""
    guess = local_naive.replace(tzinfo=timezone.utc) + timedelta(hours=5)
    offset = _eastern_offset(guess)
    return (local_naive - offset).replace(tzinfo=timezone.utc)


def _unfold(text):
    """ICS 줄 접기(다음 줄이 공백으로 시작)를 풀어 논리 줄 목록으로 만든다."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value):
    """ICS 문자열 이스케이프(\\, \\; \\n)를 푼다."""
    return (value.replace("\\n", " ").replace("\\N", " ")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _parse_dt(params, value):
    """DTSTART 값을 UTC datetime으로 바꾼다. 날짜만 있으면 동부 00:00으로 본다."""
    value = value.strip()
    date_only = "VALUE=DATE" in params and "DATE-TIME" not in params
    if date_only or re.fullmatch(r"\d{8}", value):
        return eastern_to_utc(datetime.strptime(value[:8], "%Y%m%d"))
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    local = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    # TZID가 없거나 동부 시간이면 동부로 해석(두 기관 모두 워싱턴 기준).
    return eastern_to_utc(local)


def parse_ics(text, source):
    """ICS 문자열에서 일정 목록을 뽑는다.

    Args:
        text: ICS 본문.
        source: 출처 표기 문자열.

    Returns:
        [dict(title, start_utc, source)] 시간순.
    """
    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current and current.get("title") and current.get("start_utc"):
                current["source"] = source
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, params = head.partition(";")
            if name == "SUMMARY":
                current["title"] = _unescape(value).strip()
            elif name == "DTSTART":
                try:
                    current["start_utc"] = _parse_dt(params, value)
                except ValueError:
                    current = None  # 형식이 깨진 항목은 버린다
    events.sort(key=lambda e: e["start_utc"])
    return events


def classify(title):
    """제목으로 (짧은 이름, 중요도)를 정한다. 규칙에 없으면 (원제목, 1)."""
    for keyword, short, stars in IMPORTANCE_RULES:
        if keyword in title:
            return short, stars
    return title, 1


def fomc_events(meetings=FOMC_2026):
    """FOMC 회의 목록을 일정 dict 목록으로 바꾼다(결정 발표 14:00 ET 기준)."""
    events = []
    for start, end, sep in meetings:
        events.append({
            "title": f"FOMC 금리 결정 ({start.month}/{start.day}~{end.month}/{end.day})"
                     + (" · 경제전망(SEP)" if sep else ""),
            "start_utc": eastern_to_utc(datetime(end.year, end.month, end.day, 14, 0)),
            "source": FOMC_SOURCE,
            "short": "FOMC",
            "stars": 3,
        })
    return events


def upcoming(events, now=None, days=30, min_stars=1):
    """지난 일정을 빼고 앞으로 days일 이내 일정만 시간순으로 돌려준다.

    각 항목에 short·stars·start_kst·d_day·note를 채운다.
    """
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    result = []
    for ev in events:
        if not (now <= ev["start_utc"] <= horizon):
            continue
        short, stars = (ev["short"], ev["stars"]) if "short" in ev else classify(ev["title"])
        if stars < min_stars:
            continue
        kst = ev["start_utc"].astimezone(KST)
        item = dict(ev)
        item.update(
            short=short,
            stars=stars,
            start_kst=kst.strftime("%Y-%m-%d %H:%M KST"),
            d_day=(kst.date() - now.astimezone(KST).date()).days,
            note="공식 일정 기준 · 변경 가능",
        )
        result.append(item)
    result.sort(key=lambda e: e["start_utc"])
    return result


def next_fomc(now=None, meetings=FOMC_2026):
    """아직 발표되지 않은 가장 가까운 FOMC. 목록이 끝났으면 None(날짜 갱신 필요)."""
    items = upcoming(fomc_events(meetings), now=now, days=400)
    return items[0] if items else None


def build_calendar(now=None, days=30, min_stars=2, fetch=fetch_text):
    """BEA·BLS·FOMC 일정을 합쳐 화면용 dict를 만든다.

    출처 하나가 실패해도 나머지로 채우고 errors에 남긴다.
    """
    now = now or datetime.now(timezone.utc)
    events, errors = fomc_events(), {}
    for key, url, label in (("bea", BEA_ICS_URL, "U.S. Bureau of Economic Analysis"),
                            ("bls", BLS_ICS_URL, "U.S. Bureau of Labor Statistics")):
        try:
            events.extend(parse_ics(fetch(url), label))
        except (FetchError, ValueError) as e:
            errors[key] = str(e)
    items = upcoming(events, now=now, days=days, min_stars=min_stars)
    nxt = next_fomc(now)
    return {
        "items": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in it.items()}
                  for it in items],
        "next_fomc": nxt and {"title": nxt["title"], "start_kst": nxt["start_kst"],
                              "d_day": nxt["d_day"]},
        "fomc_list_expired": nxt is None,
        "errors": errors,
        "generated_at": now.isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_calendar(), ensure_ascii=False, indent=2))
