"""qterm_addons 단위 시험. 네트워크 없이 tests/fixtures의 실제 응답 사본으로 돈다.

실행: python -m unittest discover -s tests -v
"""

import io
import os
import sys
import unittest
import zipfile
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qterm_addons import econ_calendar as cal  # noqa: E402
from qterm_addons import liquidity as liq  # noqa: E402
from qterm_addons import sec_insider as sec  # noqa: E402
from qterm_addons.net import FetchError  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class LiquidityParseTest(unittest.TestCase):
    """2026-10-09 실제 응답 사본으로 파서를 검증한다."""

    def test_tga_uses_open_field_when_close_is_null(self):
        rows = liq.parse_tga(read("tga.json"))
        self.assertEqual(rows[0][0], date(2026, 10, 8))
        self.assertAlmostEqual(rows[0][1], 887.469)  # 887,469백만 → 887.469십억
        self.assertEqual(len(rows), 2)  # 마감 잔고 행만 남는다

    def test_rrp_converted_to_billions(self):
        rows = liq.parse_rrp(read("rrp.json"))
        self.assertEqual(rows[0], (date(2026, 10, 9), 0.3))

    def test_effr_target_range(self):
        info = liq.parse_effr(read("effr.json"))
        self.assertEqual(info["date"], date(2026, 10, 8))
        self.assertEqual((info["target_low"], info["target_high"]), (3.75, 4.0))
        self.assertAlmostEqual(info["effr"], 3.88)

    def test_treasury_2y(self):
        rows = liq.parse_treasury_curve(read("treasury_curve.csv"))
        self.assertEqual(rows[0], (date(2026, 10, 9), 4.80))

    def test_treasury_missing_column(self):
        self.assertEqual(liq.parse_treasury_curve("Date,1 Mo\n10/09/2026,4.1\n"), [])

    def test_fred_csv_skips_missing(self):
        text = "observation_date,WALCL\n2026-09-24,6700000\n2026-10-01,.\n2026-10-08,6650000\n"
        self.assertEqual(liq.parse_fred_csv(text)[0], (date(2026, 10, 8), 6650000.0))

    def test_net_liquidity_units(self):
        # 총자산 6,650십억 − TGA 887.469 − RRP 0.3
        self.assertAlmostEqual(liq.net_liquidity(6650.0, 887.469, 0.3), 5762.231)
        self.assertIsNone(liq.net_liquidity(None, 887.0, 0.3))

    def test_rate_gap_label_is_neutral(self):
        gap = liq.rate_gap(4.80, 4.00)
        self.assertEqual(gap["gap_bp"], 80)
        self.assertIn("확률 아님", gap["label"])
        self.assertIn("동결", liq.rate_gap(4.05, 4.00)["label"])
        self.assertIn("인하", liq.rate_gap(3.50, 4.00)["label"])


class LiquiditySnapshotTest(unittest.TestCase):
    """출처 하나가 죽어도 나머지가 채워지는지 본다."""

    def fake_fetch(self, url, **_):
        if "fiscaldata" in url:
            return read("tga.json")
        if "reverserepo" in url:
            return read("rrp.json")
        if "effr" in url:
            return read("effr.json")
        if "daily-treasury-rates" in url:
            return read("treasury_curve.csv")
        raise FetchError(url, "HTTP 403")  # FRED는 이 세션처럼 막혔다고 가정

    def test_partial_failure(self):
        snap = liq.build_snapshot(today=date(2026, 10, 10), fetch=self.fake_fetch)
        v = snap["values"]
        self.assertAlmostEqual(v["tga_bn"], 887.469)
        self.assertEqual(v["rrp_bn"], 0.3)
        self.assertEqual(v["rate_gap"]["gap_bp"], 80)
        self.assertIsNone(v["net_liquidity_bn"])  # WALCL 없으면 계산 안 함
        self.assertIn("walcl", snap["errors"])
        self.assertIsNone(snap["notice"])  # FRED 값이 없으면 FRED 문구도 없음
        self.assertEqual(snap["asof"]["tga"], "2026-10-08")

    def test_full_with_fred(self):
        def fetch(url, **kw):
            if "fred.stlouisfed" in url:
                return "observation_date,WALCL\n2026-10-08,6650000\n"
            return self.fake_fetch(url, **kw)
        snap = liq.build_snapshot(today=date(2026, 10, 10), fetch=fetch)
        self.assertAlmostEqual(snap["values"]["net_liquidity_bn"], 6650 - 887.469 - 0.3)
        self.assertEqual(snap["notice"], liq.FRED_NOTICE)


class CalendarTest(unittest.TestCase):

    def test_eastern_dst(self):
        # 10/29 08:30 EDT = 12:30 UTC, 11/25 08:30 EST = 13:30 UTC (BEA 원문과 일치해야 함)
        self.assertEqual(cal.eastern_to_utc(datetime(2026, 10, 29, 8, 30)).hour, 12)
        self.assertEqual(cal.eastern_to_utc(datetime(2026, 11, 25, 8, 30)).hour, 13)

    def test_parse_bea_ics_both_dtstart_forms(self):
        events = cal.parse_ics(read("bea.ics"), "BEA")
        titles = [e["title"] for e in events]
        self.assertIn("GDP (Advance Estimate), 3rd Quarter 2026", titles)
        self.assertTrue(any(e["start_utc"].year == 2025 for e in events))  # VALUE=DATE-TIME 형식
        adv = next(e for e in events if e["title"].startswith("GDP (Advance"))
        self.assertEqual(adv["start_utc"], datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc))

    def test_folded_title_unescaped(self):
        events = cal.parse_ics(read("bea.ics"), "BEA")
        self.assertTrue(any("Corporate Profits, State GDP" in e["title"] for e in events))

    def test_upcoming_hides_past_and_ranks(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        events = cal.parse_ics(read("bea.ics"), "BEA") + cal.fomc_events()
        items = cal.upcoming(events, now=now, days=30, min_stars=2)
        self.assertTrue(all(i["start_utc"] >= now for i in items))
        shorts = [i["short"] for i in items]
        self.assertEqual(shorts[:3], ["FOMC", "미국 GDP", "미국 PCE·개인소득"])
        fomc = items[0]
        self.assertEqual(fomc["start_kst"], "2026-10-29 03:00 KST")  # 10/28 14:00 EDT
        self.assertEqual(fomc["d_day"], 19)

    def test_next_fomc_and_expiry(self):
        nxt = cal.next_fomc(datetime(2026, 10, 29, tzinfo=timezone.utc))
        self.assertIn("12/8~12/9", nxt["title"])
        self.assertIsNone(cal.next_fomc(datetime(2027, 1, 1, tzinfo=timezone.utc)))

    def test_build_calendar_records_bls_failure(self):
        def fetch(url, **_):
            if "bea.gov" in url:
                return read("bea.ics")
            raise FetchError(url, "HTTP 403")
        out = cal.build_calendar(now=datetime(2026, 10, 10, tzinfo=timezone.utc), fetch=fetch)
        self.assertIn("bls", out["errors"])
        self.assertEqual(out["next_fomc"]["d_day"], 19)
        self.assertFalse(out["fomc_list_expired"])


def make_sec_zip():
    """SEC readme 표 정의대로 만든 가짜 분기 zip."""
    sub = ("ACCESSION_NUMBER\tFILING_DATE\tISSUERNAME\tISSUERTRADINGSYMBOL\n"
           "A1\t02-OCT-2026\tNVIDIA CORP\tNVDA\n"
           "A2\t03-OCT-2026\tTESLA, INC.\tTSLA\n")
    own = ("ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
           "A1\t1\tDOE JOHN\tDirector\t\n"
           "A2\t2\tROE JANE\tOfficer\tCFO\n")
    trans = ("ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\t"
             "TRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS\n"
             "A1\t1\t30-SEP-2026\tP\t1000\t150.5\t5000\n"
             "A1\t2\t30-SEP-2026\tM\t9999\t0\t9999\n"
             "A2\t3\t01-OCT-2026\tS\t200\t250\t800\n"
             "A2\t4\t01-OCT-2026\tF\t50\t250\t750\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv", sub)
        zf.writestr("REPORTINGOWNER.tsv", own)
        zf.writestr("NONDERIV_TRANS.tsv", trans)
    return buf.getvalue()


class SecInsiderTest(unittest.TestCase):

    def test_only_p_and_s(self):
        trades = sec.parse_dataset(make_sec_zip())
        self.assertEqual([t["code"] for t in trades], ["S", "P"])  # 최신순, M·F 제외
        buy = trades[1]
        self.assertEqual(buy["symbol"], "NVDA")
        self.assertEqual(buy["trade_date"], "2026-09-30")
        self.assertAlmostEqual(buy["value_usd"], 150500.0)
        self.assertEqual(buy["relationship"], "Director")

    def test_symbol_filter_and_summary(self):
        trades = sec.parse_dataset(make_sec_zip(), symbols={"tsla"})
        self.assertEqual(len(trades), 1)
        summary = sec.summarize(sec.parse_dataset(make_sec_zip()))
        self.assertEqual(summary[0]["symbol"], "NVDA")
        self.assertEqual(summary[1]["net_usd"], -50000.0)

    def test_missing_table_raises(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\n")
        with self.assertRaises(ValueError):
            sec.parse_dataset(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
