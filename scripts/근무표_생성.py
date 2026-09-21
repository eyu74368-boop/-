"""근무표 생성 스크립트.

직원 명단(CSV)을 입력받아 월간 근무표를 생성하고 엑셀 파일로 저장한다.
"""

from __future__ import annotations

import argparse
import sys
from calendar import monthrange
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


def 직원명단_불러오기(csv_경로: Path) -> list[str]:
    """직원 명단 CSV(1열: 이름)를 읽어 이름 목록을 반환한다.

    Args:
        csv_경로: 직원 명단이 담긴 CSV 파일 경로.

    Returns:
        직원 이름 문자열 목록.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
        ValueError: 파일은 있으나 유효한 이름이 하나도 없을 때.
    """
    if not csv_경로.exists():
        raise FileNotFoundError(f"직원 명단 파일을 찾을 수 없습니다: {csv_경로}")

    try:
        표 = pd.read_csv(csv_경로, header=None)
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"직원 명단 파일이 비어 있습니다: {csv_경로}") from exc

    이름목록 = [str(값).strip() for 값 in 표.iloc[:, 0] if str(값).strip()]
    if not 이름목록:
        raise ValueError(f"유효한 직원 이름이 없습니다: {csv_경로}")
    return 이름목록


def 근무표_생성(직원목록: list[str], 연도: int, 월: int) -> pd.DataFrame:
    """직원 목록을 순환 배정해 해당 월의 날짜별 근무표를 생성한다.

    같은 인원이 이틀 연속 근무하지 않도록 순환 방식으로 배정한다.

    Args:
        직원목록: 근무 배정 대상 직원 이름 목록.
        연도: 근무표를 생성할 연도.
        월: 근무표를 생성할 월(1~12).

    Returns:
        '날짜', '근무자' 컬럼을 가진 DataFrame.

    Raises:
        ValueError: 직원 목록이 비어 있거나 월 값이 범위를 벗어날 때.
    """
    if not 직원목록:
        raise ValueError("직원 목록이 비어 있어 근무표를 생성할 수 없습니다.")
    if not 1 <= 월 <= 12:
        raise ValueError(f"월 값이 올바르지 않습니다: {월}")

    _, 마지막날 = monthrange(연도, 월)
    배정결과 = [
        {"날짜": f"{연도:04d}-{월:02d}-{일:02d}", "근무자": 직원목록[(일 - 1) % len(직원목록)]}
        for 일 in range(1, 마지막날 + 1)
    ]
    return pd.DataFrame(배정결과)


def 엑셀로_저장(근무표: pd.DataFrame, 저장경로: Path) -> None:
    """근무표 DataFrame을 서식이 적용된 엑셀 파일로 저장한다.

    Args:
        근무표: '날짜', '근무자' 컬럼을 가진 DataFrame.
        저장경로: 저장할 .xlsx 파일 경로.

    Raises:
        OSError: 파일 쓰기에 실패했을 때(권한 문제, 디스크 공간 부족 등).
    """
    저장경로.parent.mkdir(parents=True, exist_ok=True)

    try:
        with pd.ExcelWriter(저장경로, engine="openpyxl") as writer:
            근무표.to_excel(writer, index=False, sheet_name="근무표")
            시트 = writer.sheets["근무표"]

            헤더서식 = Font(bold=True)
            for 열 in range(1, 근무표.shape[1] + 1):
                셀 = 시트.cell(row=1, column=열)
                셀.font = 헤더서식
                셀.alignment = Alignment(horizontal="center")
                시트.column_dimensions[get_column_letter(열)].width = 18
    except OSError as exc:
        raise OSError(f"근무표 엑셀 파일 저장에 실패했습니다: {저장경로}") from exc


def main() -> int:
    """CLI 진입점: 인자를 파싱해 근무표를 생성하고 저장한다."""
    파서 = argparse.ArgumentParser(description="직원 명단 CSV로 월간 근무표(xlsx)를 생성한다.")
    파서.add_argument("직원명단_csv", type=Path, help="직원 명단 CSV 파일 경로 (1열: 이름)")
    파서.add_argument("연도", type=int, help="근무표 연도 (예: 2026)")
    파서.add_argument("월", type=int, help="근무표 월 (1~12)")
    파서.add_argument(
        "-o", "--output", type=Path, default=Path("근무표.xlsx"), help="저장할 엑셀 파일 경로"
    )
    인자 = 파서.parse_args()

    try:
        직원목록 = 직원명단_불러오기(인자.직원명단_csv)
        근무표 = 근무표_생성(직원목록, 인자.연도, 인자.월)
        엑셀로_저장(근무표, 인자.output)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(f"근무표 생성 완료: {인자.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
