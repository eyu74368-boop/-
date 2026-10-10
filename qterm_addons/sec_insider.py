"""SEC 분기 내부자 거래 데이터셋에서 장내 매수·매도만 추출 (블룸버그 OWN 대응).

출처: SEC Insider Transactions Data Sets (Form 3/4/5, 분기 갱신, 키 불필요).
- 형식: zip 안에 탭 구분 UTF-8 파일 8개 (SEC readme 원문, 2026-10-09 확인)
- 쓰는 표: SUBMISSION(종목·제출일) · REPORTINGOWNER(보고자·관계) · NONDERIV_TRANS(거래)
- 거래코드 P(장내·사적 매수)와 S(장내·사적 매도)만 남긴다. A(보상)·M(옵션 행사)·
  F(세금 원천징수)·G(증여) 등은 의사결정 신호가 아니라서 뺀다.
- 분기 데이터라 최대 3개월 늦다. 화면에 "분기 자료 · 제출일 기준"을 표시할 것.

zip 다운로드 주소 형식(DATASET_URL)은 이 세션에서 직접 받아 보지 못했다
(SEC가 연락처 있는 User-Agent를 요구). PC에서 QTERM_USER_AGENT를 설정하고 확인 대기.
"""

import csv
import io
import zipfile
from collections import defaultdict
from datetime import datetime

from .net import fetch_bytes

DATASET_URL = ("https://www.sec.gov/files/structureddata/data/"
               "insider-transactions-data-sets/{year}q{quarter}_form345.zip")
SOURCE = "U.S. SEC Insider Transactions Data Sets (quarterly, as filed)"
SIGNAL_CODES = {"P": "매수", "S": "매도"}

# 탭 구분 파일의 필드 하나가 매우 길 수 있다(REMARKS 등).
csv.field_size_limit(10 * 1024 * 1024)


def _read_table(zf, table):
    """zip 안에서 이름이 table로 시작하는 파일을 찾아 dict 행으로 읽는다."""
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1].upper()
        if base.startswith(table + ".") or base == table:
            with zf.open(name) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
                yield from csv.DictReader(text, delimiter="\t")
            return
    raise ValueError(f"zip 안에 {table} 파일이 없음")


def _num(value):
    """숫자 문자열 → float. 비었으면 None."""
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _date(value):
    """'DD-MON-YYYY' → 'YYYY-MM-DD'. 형식이 다르면 원문 그대로."""
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date().isoformat()
    except (ValueError, AttributeError):
        return value


def parse_dataset(data, symbols=None):
    """분기 zip(bytes 또는 파일 경로)에서 P/S 거래 목록을 만든다.

    Args:
        data: zip bytes 또는 파일 경로.
        symbols: 남길 티커 집합(대문자). None이면 전부.

    Returns:
        [dict] 거래일 최신순. 각 항목: symbol, issuer, owner, relationship, title,
        trade_date, filing_date, code, side, shares, price, value_usd, owned_after,
        accession.
    """
    source = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    wanted = {s.upper() for s in symbols} if symbols else None
    with zipfile.ZipFile(source) as zf:
        subs = {}
        for row in _read_table(zf, "SUBMISSION"):
            symbol = (row.get("ISSUERTRADINGSYMBOL") or "").strip().upper()
            if wanted and symbol not in wanted:
                continue
            subs[row["ACCESSION_NUMBER"]] = {
                "symbol": symbol,
                "issuer": (row.get("ISSUERNAME") or "").strip(),
                "filing_date": _date(row.get("FILING_DATE", "")),
            }
        owners = defaultdict(list)
        for row in _read_table(zf, "REPORTINGOWNER"):
            if row["ACCESSION_NUMBER"] in subs:
                owners[row["ACCESSION_NUMBER"]].append(row)
        trades = []
        for row in _read_table(zf, "NONDERIV_TRANS"):
            acc = row["ACCESSION_NUMBER"]
            code = (row.get("TRANS_CODE") or "").strip().upper()
            if acc not in subs or code not in SIGNAL_CODES:
                continue
            shares = _num(row.get("TRANS_SHARES"))
            price = _num(row.get("TRANS_PRICEPERSHARE"))
            owner_rows = owners.get(acc) or [{}]
            trades.append({
                **subs[acc],
                "owner": "; ".join(o.get("RPTOWNERNAME", "") for o in owner_rows).strip("; "),
                "relationship": owner_rows[0].get("RPTOWNER_RELATIONSHIP", ""),
                "title": owner_rows[0].get("RPTOWNER_TITLE", ""),
                "trade_date": _date(row.get("TRANS_DATE", "")),
                "code": code,
                "side": SIGNAL_CODES[code],
                "shares": shares,
                "price": price,
                "value_usd": shares * price if shares is not None and price else None,
                "owned_after": _num(row.get("SHRS_OWND_FOLWNG_TRANS")),
                "accession": acc,
            })
    trades.sort(key=lambda t: t["trade_date"], reverse=True)
    return trades


def summarize(trades):
    """종목별 매수·매도 합계(달러)와 매수자 수를 낸다. 순매수액 큰 순."""
    agg = {}
    for t in trades:
        s = agg.setdefault(t["symbol"], {"symbol": t["symbol"], "issuer": t["issuer"],
                                         "buy_usd": 0.0, "sell_usd": 0.0,
                                         "buyers": set(), "sellers": set(), "trades": 0})
        value = t["value_usd"] or 0.0
        if t["code"] == "P":
            s["buy_usd"] += value
            s["buyers"].add(t["owner"])
        else:
            s["sell_usd"] += value
            s["sellers"].add(t["owner"])
        s["trades"] += 1
    rows = []
    for s in agg.values():
        rows.append({**s, "buyers": len(s["buyers"]), "sellers": len(s["sellers"]),
                     "net_usd": s["buy_usd"] - s["sell_usd"]})
    rows.sort(key=lambda r: r["net_usd"], reverse=True)
    return rows


def download_dataset(year, quarter, **kwargs):
    """분기 zip을 내려받아 bytes로 돌려준다. QTERM_USER_AGENT(연락처 포함) 필수."""
    return fetch_bytes(DATASET_URL.format(year=year, quarter=quarter), timeout=60, **kwargs)
