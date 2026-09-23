# X(구 트위터) 계정 운영 및 자동화 가이드

특정 조직·분야에 종속되지 않고, 개인/기관 계정 어디에나 적용할 수 있도록 범용적인 관점에서 정리한 가이드입니다.

## 1. 전체 그림

X 운영은 크게 4단계의 반복 사이클로 구성됩니다.

1. **기획** — 계정 목적·타깃·톤앤매너 정의, 월간/주간 콘텐츠 캘린더 작성
2. **제작** — 게시글 문안, 이미지·영상(→ `AI_이미지_영상_생성_가이드.md` 참고) 준비
3. **발행** — 수동 게시 또는 API 기반 예약·자동 게시
4. **분석 및 개선** — 노출·참여 지표 확인 후 다음 기획에 반영

## 2. 계정 기본 설정

| 항목 | 권장 사항 |
|---|---|
| 프로필 이름/핸들 | 검색되기 쉬운 짧은 이름, 다른 채널과 동일한 핸들 유지 |
| 소개(Bio) | "누구를 위해 무엇을 알리는 계정인지" 한 문장 + 공식 링크 |
| 프로필·헤더 이미지 | 브랜드 로고/대표 이미지, 모바일에서 잘리지 않는 구도 |
| 고정 게시물 | 계정 소개 또는 가장 중요한 공지 1건 |
| 보안 | 2단계 인증 필수, 공용 계정은 비밀번호 공유 대신 관리 도구 권한 위임 |

## 3. 콘텐츠 운영 원칙

* **목적별 비율 정하기**: 예) 정보성 50% / 소통·참여 유도 30% / 홍보 20%
* **짧고 명확하게**: 첫 줄에 핵심을 넣고, 한 게시물에는 한 가지 메시지만 담는다
* **시각 자료 활용**: 이미지·짧은 영상이 포함된 게시물은 텍스트 단독보다 주목도가 높다
* **스레드 활용**: 긴 설명은 여러 게시물을 이어 붙인 스레드로 나눈다
* **해시태그는 1~2개**: 과도한 해시태그는 가독성을 떨어뜨린다
* **일관된 발행 주기**: 몰아서 올리기보다 정해진 요일·시간대에 꾸준히 올린다

### 콘텐츠 캘린더 예시

| 요일 | 유형 | 예시 |
|---|---|---|
| 월 | 정보성 | 이번 주 알아두면 좋은 팁 |
| 수 | 참여 유도 | 투표(Poll), 질문형 게시물 |
| 금 | 홍보/소식 | 행사·서비스 안내, 성과 공유 |

## 4. 소통 및 위기 대응

* **응대 기준 마련**: 멘션·DM 응답 목표 시간(예: 영업일 24시간 이내)과 담당자를 정한다
* **답변 템플릿**: 자주 묻는 질문은 답변 문안을 미리 준비하되, 그대로 복사하지 말고 상황에 맞게 다듬는다
* **민감 이슈 에스컬레이션**: 민원·사고·오보 등은 즉시 답하지 말고 내부 확인 → 공식 입장 순으로 대응한다
* **삭제보다 정정**: 잘못된 게시물은 조용히 삭제하기보다 정정 게시물을 남기는 것이 신뢰에 유리하다
* **스팸/악성 계정**: 차단·신고 기준을 문서화해 담당자가 바뀌어도 일관되게 처리한다

## 5. 성과 지표(KPI)

| 지표 | 의미 | 활용 |
|---|---|---|
| 노출수(Impressions) | 게시물이 화면에 표시된 횟수 | 도달 범위 파악 |
| 참여수(Engagements) | 좋아요·재게시·답글·클릭 등 합계 | 콘텐츠 반응도 |
| 참여율 | 참여수 ÷ 노출수 | 콘텐츠 유형 간 비교 |
| 팔로워 증감 | 기간별 순증 | 계정 성장 추세 |
| 링크 클릭 | 외부 링크 유입 | 홈페이지·신청 페이지 전환 |

월 1회 지표를 엑셀로 정리하고, **참여율 상위/하위 게시물의 공통점**을 찾아 다음 달 캘린더에 반영합니다.

## 6. API 기반 자동 게시 (Python 예시)

X API를 사용하려면 [X 개발자 포털](https://developer.x.com)에서 앱을 만들고 키를 발급받아야 합니다. 요금제·호출 한도는 자주 바뀌므로 도입 전에 반드시 현재 정책을 확인하세요.

**패키지 설치**

```bash
# X API v2 호출용 공식 권장 Python 클라이언트, 예약 목록(엑셀) 처리를 위한 pandas/openpyxl
pip install tweepy pandas openpyxl
```

**API 키는 환경 변수로 관리**

```bash
export X_API_KEY="..."
export X_API_SECRET="..."
export X_ACCESS_TOKEN="..."
export X_ACCESS_TOKEN_SECRET="..."
```

아래 예시는 엑셀 파일(`schedule.xlsx`)에 정리된 게시 예정 목록을 읽어, 발행 시각이 지난 미게시 건을 게시하고 결과를 다시 엑셀에 기록합니다.

| post_at | text | image_path | status | tweet_id |
|---|---|---|---|---|
| 2026-10-01 09:00 | 이번 주 공지입니다. | images/notice.png | | |

```python
"""X(구 트위터) 예약 게시 자동화 예시.

엑셀로 관리하는 게시 예정 목록을 읽어, 발행 시각이 도래한 게시물을
X API로 게시하고 결과(상태, 게시물 ID)를 다시 엑셀에 기록한다.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import tweepy

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCHEDULE_FILE = Path("schedule.xlsx")
MAX_TEXT_LENGTH = 280  # 일반 계정 기준 글자 수 제한 (한글은 가중치가 달라 실제로는 더 짧을 수 있음)


def load_credentials() -> dict[str, str]:
    """환경 변수에서 X API 자격 증명을 읽는다.

    Raises:
        RuntimeError: 필수 환경 변수가 하나라도 없을 때.
    """
    keys = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"환경 변수 누락: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


class XPublisher:
    """X API 게시 기능을 감싼 클래스.

    텍스트 게시는 API v2(Client), 미디어 업로드는 v1.1(API)을 사용한다.
    """

    def __init__(self, creds: dict[str, str]) -> None:
        """자격 증명으로 v2 클라이언트와 v1.1 미디어 업로드용 API 객체를 생성한다."""
        self.client = tweepy.Client(
            consumer_key=creds["X_API_KEY"],
            consumer_secret=creds["X_API_SECRET"],
            access_token=creds["X_ACCESS_TOKEN"],
            access_token_secret=creds["X_ACCESS_TOKEN_SECRET"],
        )
        auth = tweepy.OAuth1UserHandler(
            creds["X_API_KEY"],
            creds["X_API_SECRET"],
            creds["X_ACCESS_TOKEN"],
            creds["X_ACCESS_TOKEN_SECRET"],
        )
        self.api_v1 = tweepy.API(auth)

    def post(self, text: str, image_path: Path | None = None) -> str:
        """게시물을 올리고 게시물 ID를 반환한다.

        Args:
            text: 게시할 본문.
            image_path: 첨부 이미지 경로(없으면 텍스트만 게시).

        Raises:
            ValueError: 본문이 비었거나 길이 제한을 넘을 때.
            FileNotFoundError: 첨부 이미지 파일이 없을 때.
        """
        text = text.strip()
        if not text:
            raise ValueError("본문이 비어 있습니다.")
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"본문 길이 초과({len(text)}자 > {MAX_TEXT_LENGTH}자)")

        media_ids = None
        if image_path is not None:
            if not image_path.is_file():
                raise FileNotFoundError(f"이미지 파일 없음: {image_path}")
            media = self.api_v1.media_upload(filename=str(image_path))
            media_ids = [media.media_id]

        response = self.client.create_tweet(text=text, media_ids=media_ids)
        return str(response.data["id"])


def run_schedule(publisher: XPublisher, schedule_file: Path = SCHEDULE_FILE) -> None:
    """예약 목록 중 발행 시각이 지난 미게시 건을 게시하고 결과를 엑셀에 저장한다.

    개별 게시 실패가 전체 작업을 중단시키지 않도록 건별로 예외를 격리하며,
    요율 제한(429)에 걸리면 남은 건은 다음 실행으로 미룬다.
    """
    try:
        df = pd.read_excel(schedule_file, dtype={"tweet_id": str})
    except FileNotFoundError:
        logger.error("예약 파일이 없습니다: %s", schedule_file)
        return
    except Exception as exc:  # noqa: BLE001 - 손상된 엑셀 등 읽기 오류 전반을 기록하기 위함
        logger.error("예약 파일 읽기 실패: %s", exc)
        return

    for col in ("status", "tweet_id"):
        if col not in df.columns:
            df[col] = ""
    df["status"] = df["status"].fillna("").astype(str)
    df["post_at"] = pd.to_datetime(df["post_at"], errors="coerce")

    now = datetime.now()
    for idx, row in df.iterrows():
        if row["status"] == "posted" or pd.isna(row["post_at"]) or row["post_at"] > now:
            continue

        image = row.get("image_path")
        image_path = Path(image) if isinstance(image, str) and image.strip() else None
        try:
            tweet_id = publisher.post(str(row["text"]), image_path)
            df.at[idx, "status"] = "posted"
            df.at[idx, "tweet_id"] = tweet_id
            logger.info("게시 완료 (행 %s): id=%s", idx, tweet_id)
        except tweepy.TooManyRequests:
            logger.warning("요율 제한에 도달했습니다. 남은 건은 다음 실행에서 처리합니다.")
            break
        except (ValueError, FileNotFoundError, tweepy.TweepyException) as exc:
            df.at[idx, "status"] = f"error: {exc}"
            logger.error("게시 실패 (행 %s): %s", idx, exc)

    try:
        df.to_excel(schedule_file, index=False)
    except PermissionError:
        logger.error("엑셀 파일이 열려 있어 저장하지 못했습니다. 파일을 닫고 다시 실행하세요.")


if __name__ == "__main__":
    run_schedule(XPublisher(load_credentials()))
```

### 운영 팁

1. 위 스크립트를 작업 스케줄러(Windows) 또는 `cron`(Linux)으로 10~30분 간격 실행하면 예약 게시가 된다.
2. 게시 실패 건은 `status` 열에 오류 메시지가 남으므로, 문구를 고친 뒤 `status`를 비우면 다음 실행 때 재시도된다.
3. 처음에는 테스트 계정으로 동작을 확인한 뒤 실제 계정에 적용한다.

## 7. 자동화 시 주의 사항 (정책 준수)

* **스팸성 자동화 금지**: 동일·유사 문구 반복 게시, 자동 팔로우/언팔로우, 무차별 자동 멘션·DM은 X 자동화 규칙 위반으로 계정 정지 사유가 될 수 있다
* **자동 계정 표시**: 봇 성격의 계정이라면 프로필에 자동화 계정 라벨을 설정한다
* **개인정보 보호**: 민원인 정보·연락처 등 개인정보가 게시물이나 이미지에 노출되지 않도록 발행 전 검수한다
* **사람의 검수 유지**: 문안 작성까지 AI로 자동화하더라도, 최종 발행 전 담당자 확인 단계를 둔다

## 8. 도입 시 체크리스트

- [ ] 계정 목적·타깃·톤앤매너를 문서로 정리했는가
- [ ] 2단계 인증을 설정하고, 관리 권한자를 명확히 했는가
- [ ] 월간 콘텐츠 캘린더와 발행 주기를 정했는가
- [ ] 멘션·DM 응대 기준과 위기 대응(에스컬레이션) 절차가 있는가
- [ ] API 키를 코드에 하드코딩하지 않고 환경 변수로 관리하는가
- [ ] 자동 게시 실패 시 로그와 엑셀에 기록이 남는가
- [ ] X 자동화 규칙과 개인정보 보호 기준을 확인했는가
- [ ] 월 1회 KPI를 정리하고 다음 기획에 반영하는가
