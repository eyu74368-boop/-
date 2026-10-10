"""미국 유동성·금리 스냅샷 (QTERM N8 + N9 대체안).

출처(모두 키 불필요, 2026-10-10 이 세션에서 실제 응답 확인):
- TGA 잔고: 미 재무부 FiscalData, Daily Treasury Statement (단위: 백만 달러)
- 역레포(RRP): 뉴욕 연준 Markets API (단위: 달러)
- 실효 연방기금금리(EFFR)·목표범위: 뉴욕 연준 Markets API (%)
- 2년물 국채 금리: 미 재무부 일별 국채 수익률 곡선 CSV (%)
- 연준 총자산(WALCL): FRED 그래프 CSV (단위: 백만 달러). 이 세션에서는 접속이
  막혀 확인하지 못했다 → 실패해도 나머지 값은 그대로 낸다.

순유동성 = 연준 총자산 − TGA − 역레포. 모든 항을 십억 달러로 맞춘 뒤 뺀다.
금리 기대 방향은 "2년물 − 목표범위 상단" 차이로만 보여 주며 확률이 아니다.
"""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

from .net import FetchError, fetch_text

TGA_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/"
    "operating_cash_balance?filter=record_date:gte:{since}&sort=-record_date"
    "&page[size]=100&fields=record_date,account_type,open_today_bal,close_today_bal"
)
RRP_URL = (
    "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json"
    "?startDate={since}&endDate={until}"
)
EFFR_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/{n}.json"
TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={since}"

SOURCE_LABELS = {
    "tga": "U.S. Treasury FiscalData (Daily Treasury Statement)",
    "rrp": "Federal Reserve Bank of New York (Reverse Repo)",
    "effr": "Federal Reserve Bank of New York (EFFR)",
    "ust2y": "U.S. Treasury Daily Par Yield Curve",
    "walcl": "FRED WALCL (Federal Reserve H.4.1)",
}

# FRED 약관: FRED 데이터를 화면에 보여 줄 때 이 문구를 눈에 띄게 표시해야 한다.
FRED_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified by "
    "the Federal Reserve Bank of St. Louis."
)

TGA_CLOSING_TYPE = "Treasury General Account (TGA) Closing Balance"


def _to_float(value):
    """숫자 문자열을 float로 바꾼다. 비었거나 'null'·'N/A'면 None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "null", "N/A", "."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_tga(payload):
    """FiscalData 응답에서 날짜별 TGA 마감 잔고(십억 달러)를 뽑는다.

    DTS는 마감 잔고 행의 값을 close_today_bal이 아니라 open_today_bal에 넣는
    경우가 있어(2026-10 확인), close가 비면 open을 쓴다.

    Args:
        payload: JSON 문자열 또는 이미 파싱한 dict.

    Returns:
        [(date, 십억 달러)] 최신순.
    """
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    rows = []
    for row in data.get("data", []):
        if row.get("account_type") != TGA_CLOSING_TYPE:
            continue
        value = _to_float(row.get("close_today_bal"))
        if value is None:
            value = _to_float(row.get("open_today_bal"))
        if value is None:
            continue
        rows.append((date.fromisoformat(row["record_date"]), value / 1000.0))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def parse_rrp(payload):
    """뉴욕 연준 역레포 응답에서 날짜별 낙찰액(십억 달러)을 뽑는다.

    같은 날 여러 운영이 있으면 합산한다.

    Returns:
        [(date, 십억 달러)] 최신순.
    """
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    totals = {}
    for op in data.get("repo", {}).get("operations", []):
        if op.get("operationType") != "Reverse Repo":
            continue
        amount = _to_float(op.get("totalAmtAccepted"))
        if amount is None:
            continue
        day = date.fromisoformat(op["operationDate"])
        totals[day] = totals.get(day, 0.0) + amount / 1e9
    return sorted(totals.items(), key=lambda r: r[0], reverse=True)


def parse_effr(payload):
    """뉴욕 연준 EFFR 응답에서 최신 실효금리와 목표범위를 뽑는다.

    Returns:
        dict(date, effr, target_low, target_high) 또는 데이터가 없으면 None.
    """
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    rates = [r for r in data.get("refRates", []) if r.get("type") == "EFFR"]
    if not rates:
        return None
    latest = max(rates, key=lambda r: r["effectiveDate"])
    return {
        "date": date.fromisoformat(latest["effectiveDate"]),
        "effr": _to_float(latest.get("percentRate")),
        "target_low": _to_float(latest.get("targetRateFrom")),
        "target_high": _to_float(latest.get("targetRateTo")),
    }


def parse_treasury_curve(text, column="2 Yr"):
    """재무부 수익률 곡선 CSV에서 특정 만기 금리를 뽑는다.

    Returns:
        [(date, %)] 최신순. 해당 열이 없으면 빈 목록.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or column not in reader.fieldnames:
        return []
    rows = []
    for row in reader:
        value = _to_float(row.get(column))
        if value is None:
            continue
        try:
            day = datetime.strptime(row["Date"], "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        rows.append((day, value))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def parse_fred_csv(text):
    """FRED 그래프 CSV(observation_date,값)를 [(date, 값)] 최신순으로 바꾼다."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        return []
    rows = []
    for line in reader:
        if len(line) < 2:
            continue
        value = _to_float(line[1])
        if value is None:
            continue
        try:
            rows.append((date.fromisoformat(line[0]), value))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def net_liquidity(walcl_bn, tga_bn, rrp_bn):
    """순유동성(십억 달러) = 연준 총자산 − TGA − 역레포. 하나라도 없으면 None."""
    if walcl_bn is None or tga_bn is None or rrp_bn is None:
        return None
    return walcl_bn - tga_bn - rrp_bn


def rate_gap(ust2y, target_high):
    """2년물 − 목표범위 상단 차이(bp)와 중립 표현을 돌려준다.

    확률이 아니라 "시장 금리가 현재 정책금리보다 높은가/낮은가"만 나타낸다.
    매매 판단 문구는 넣지 않는다.

    Returns:
        dict(gap_bp, label) 또는 값이 없으면 None.
    """
    if ust2y is None or target_high is None:
        return None
    gap_bp = round((ust2y - target_high) * 100)
    if gap_bp >= 25:
        label = "2년물이 목표범위 상단보다 높음 (인상 쪽 가격 반영 근사)"
    elif gap_bp <= -25:
        label = "2년물이 목표범위 상단보다 낮음 (인하 쪽 가격 반영 근사)"
    else:
        label = "2년물이 목표범위 근처 (동결 근사)"
    return {"gap_bp": gap_bp, "label": label + " · 확률 아님"}


def _latest(rows):
    """[(date, 값)] 최신순 목록의 첫 항목. 비었으면 (None, None)."""
    return rows[0] if rows else (None, None)


def _change(rows, days):
    """최신값과 days일 이전 가장 가까운 값의 차이. 비교할 값이 없으면 None."""
    if not rows:
        return None
    latest_day, latest_val = rows[0]
    cutoff = latest_day - timedelta(days=days)
    for day, val in rows:
        if day <= cutoff:
            return round(latest_val - val, 3)
    return None


def build_snapshot(today=None, fetch=fetch_text):
    """모든 출처를 호출해 화면용 스냅샷 dict를 만든다.

    출처 하나가 실패해도 나머지는 채우고, 실패한 출처는 errors에 남긴다.
    화면은 asof(출처별 기준일)와 errors를 그대로 보여 주면 된다.

    Args:
        today: 기준일(date). 시험용. 기본값은 오늘(UTC).
        fetch: URL→문자열 함수. 시험에서 가짜 함수를 넣는다.

    Returns:
        dict: values, asof, errors, sources, notice, generated_at
    """
    today = today or datetime.now(timezone.utc).date()
    since = (today - timedelta(days=45)).isoformat()
    values, asof, errors = {}, {}, {}

    def run(key, func):
        try:
            func()
        except (FetchError, ValueError, KeyError, TypeError) as e:
            errors[key] = str(e)

    def load_tga():
        rows = parse_tga(fetch(TGA_URL.format(since=since)))
        asof["tga"], values["tga_bn"] = _latest(rows)
        values["tga_change_7d_bn"] = _change(rows, 7)

    def load_rrp():
        rows = parse_rrp(fetch(RRP_URL.format(since=since, until=today.isoformat())))
        asof["rrp"], values["rrp_bn"] = _latest(rows)

    def load_effr():
        info = parse_effr(fetch(EFFR_URL.format(n=5)))
        if info:
            asof["effr"] = info["date"]
            values.update(effr=info["effr"], target_low=info["target_low"],
                          target_high=info["target_high"])

    def load_ust2y():
        rows = parse_treasury_curve(fetch(TREASURY_CSV_URL.format(year=today.year)))
        if not rows:  # 연초에는 올해 자료가 없을 수 있다
            rows = parse_treasury_curve(fetch(TREASURY_CSV_URL.format(year=today.year - 1)))
        asof["ust2y"], values["ust2y"] = _latest(rows)

    def load_walcl():
        rows = parse_fred_csv(fetch(FRED_CSV_URL.format(series="WALCL", since=since)))
        day, val = _latest(rows)
        asof["walcl"] = day
        values["walcl_bn"] = val / 1000.0 if val is not None else None

    run("tga", load_tga)
    run("rrp", load_rrp)
    run("effr", load_effr)
    run("ust2y", load_ust2y)
    run("walcl", load_walcl)

    values["net_liquidity_bn"] = net_liquidity(
        values.get("walcl_bn"), values.get("tga_bn"), values.get("rrp_bn"))
    values["rate_gap"] = rate_gap(values.get("ust2y"), values.get("target_high"))

    return {
        "values": values,
        "asof": {k: v.isoformat() if v else None for k, v in asof.items()},
        "errors": errors,
        "sources": SOURCE_LABELS,
        "notice": FRED_NOTICE if values.get("walcl_bn") is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    print(json.dumps(build_snapshot(), ensure_ascii=False, indent=2, default=str))
