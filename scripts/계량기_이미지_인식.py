"""수도계량기 이미지 전처리 및 숫자 영역 추출 스크립트.

계량기 사진을 입력받아 인식 정확도를 높이기 위한 전처리(그레이스케일, 이진화,
숫자판 영역 검출)를 수행하는 파이프라인의 기본 골격을 제공한다.
실제 숫자 인식(OCR/분류 모델)은 별도 모듈에서 이 결과를 입력으로 사용한다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class 전처리결과:
    """전처리 파이프라인의 중간/최종 산출물을 담는 컨테이너."""

    원본: np.ndarray
    이진화: np.ndarray
    숫자판_영역: np.ndarray | None


def 이미지_불러오기(이미지경로: Path) -> np.ndarray:
    """이미지 파일을 읽어 BGR 배열로 반환한다.

    Args:
        이미지경로: 계량기 사진 파일 경로.

    Returns:
        OpenCV BGR 이미지 배열.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
        ValueError: 파일은 있으나 이미지로 디코딩할 수 없을 때(손상/미지원 포맷).
    """
    if not 이미지경로.exists():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {이미지경로}")

    이미지 = cv2.imread(str(이미지경로), cv2.IMREAD_COLOR)
    if 이미지 is None:
        raise ValueError(f"이미지를 읽을 수 없습니다(손상되었거나 지원하지 않는 형식): {이미지경로}")
    return 이미지


def 전처리(이미지: np.ndarray) -> 전처리결과:
    """그레이스케일 변환과 적응형 이진화로 숫자 인식 전 단계 전처리를 수행한다.

    조명 불균일 환경(현장 사진)에 강인하도록 적응형 임계값(adaptive threshold)을
    사용한다.

    Args:
        이미지: 원본 BGR 이미지.

    Returns:
        원본, 이진화 이미지, (검출 시) 숫자판 후보 영역을 담은 전처리결과.

    Raises:
        ValueError: 입력 이미지가 비어 있을 때.
    """
    if 이미지 is None or 이미지.size == 0:
        raise ValueError("전처리할 이미지가 비어 있습니다.")

    그레이 = cv2.cvtColor(이미지, cv2.COLOR_BGR2GRAY)
    흐림제거 = cv2.GaussianBlur(그레이, (5, 5), 0)
    이진화 = cv2.adaptiveThreshold(
        흐림제거,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11,
        2,
    )

    숫자판_영역 = 숫자판_영역_검출(이진화, 이미지)
    return 전처리결과(원본=이미지, 이진화=이진화, 숫자판_영역=숫자판_영역)


def 숫자판_영역_검출(이진화이미지: np.ndarray, 원본이미지: np.ndarray) -> np.ndarray | None:
    """이진화 이미지에서 가장 큰 사각형 윤곽선을 계량기 숫자판 후보로 추출한다.

    실제 계량기 형태에 맞춘 정교한 검출 로직은 현장 데이터로 추가 보정이 필요하다.

    Args:
        이진화이미지: 이진화된 단일 채널 이미지.
        원본이미지: 크롭에 사용할 원본 BGR 이미지.

    Returns:
        검출된 숫자판 후보 영역(BGR), 검출 실패 시 None.
    """
    윤곽선목록, _ = cv2.findContours(이진화이미지, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not 윤곽선목록:
        return None

    최대윤곽선 = max(윤곽선목록, key=cv2.contourArea)
    if cv2.contourArea(최대윤곽선) < 500:
        return None

    x, y, 너비, 높이 = cv2.boundingRect(최대윤곽선)
    return 원본이미지[y : y + 높이, x : x + 너비]


def main() -> int:
    """CLI 진입점: 이미지 경로를 받아 전처리 결과를 저장한다."""
    파서 = argparse.ArgumentParser(description="수도계량기 이미지 전처리 파이프라인")
    파서.add_argument("이미지_경로", type=Path, help="계량기 사진 파일 경로")
    파서.add_argument(
        "-o", "--output-dir", type=Path, default=Path("전처리결과"), help="결과 저장 폴더"
    )
    인자 = 파서.parse_args()

    try:
        이미지 = 이미지_불러오기(인자.이미지_경로)
        결과 = 전처리(이미지)
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    인자.output_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(인자.output_dir / "이진화.png"), 결과.이진화)
    if 결과.숫자판_영역 is not None:
        cv2.imwrite(str(인자.output_dir / "숫자판_영역.png"), 결과.숫자판_영역)
        print(f"전처리 완료 (숫자판 영역 검출됨): {인자.output_dir}")
    else:
        print(f"전처리 완료 (숫자판 영역 검출 실패 — 이진화 결과만 저장됨): {인자.output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
