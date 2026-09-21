# AI 이미지·영상 생성 도구 및 자동화 가이드

특정 서비스에 종속되지 않고, 상황에 맞게 골라 쓸 수 있도록 범용적인 관점에서 정리한 가이드입니다.

## 1. 전체 그림

AI를 활용한 이미지·영상 제작 자동화는 크게 3단계로 구성됩니다.

1. **입력 준비** — 프롬프트(텍스트), 참고 이미지, 스타일 가이드 등을 정리
2. **생성** — AI 서비스/모델 API를 호출해 이미지·영상 산출물 생성
3. **후처리 및 배포** — 리사이즈·워터마크·포맷 변환 후 저장소나 채널에 자동 업로드

이 문서는 위 각 단계에서 무엇을 고려해야 하는지, 어떤 종류의 도구가 있는지, 자동화 파이프라인을 어떻게 구성하면 좋은지를 다룹니다.

## 2. 이미지 생성 도구 유형

| 유형 | 특징 | 예시 성격 |
|---|---|---|
| 텍스트→이미지 API | 프롬프트만으로 이미지 생성, API 호출 기반 자동화에 적합 | Stable Diffusion 계열, DALL-E 계열 |
| 디자인 플랫폼 연동형 | 템플릿/브랜드 요소와 결합해 완성도 높은 결과물 생성 | Canva류 디자인 자동화 도구 |
| 로컬 실행형 모델 | 자체 서버/GPU에서 구동, 외부 API 의존 없음 | 오픈소스 확산모델 |

**선택 기준**
- 반복 대량 생성이 목적이면 → API 기반 텍스트→이미지 도구
- 브랜드 템플릿을 지켜야 하면 → 디자인 플랫폼 연동형
- 데이터 외부 유출이 민감하면 → 로컬 실행형 모델

## 3. 영상 생성 도구 유형

| 유형 | 특징 |
|---|---|
| 텍스트→영상 API | 짧은 클립 생성에 적합, 프롬프트 기반 |
| 이미지→영상(모션) API | 정적 이미지에 모션을 부여, 슬라이드/배너 영상화에 적합 |
| 편집 자동화 도구 | 여러 소스 클립을 규칙 기반으로 합성·자막 삽입 |

## 4. 자동화 파이프라인 설계 원칙

* **입력과 출력을 분리**: 프롬프트/설정 파일(YAML, JSON)과 실행 스크립트를 분리하면 재사용이 쉬움
* **재시도 및 예외 처리**: 외부 API 호출은 네트워크 오류, 요율 제한(rate limit)이 흔하므로 재시도 로직 필수
* **결과물 검증 단계 포함**: 생성된 이미지·영상의 해상도/포맷/파일 크기를 자동 검증 후 다음 단계로 전달
* **비용/사용량 로깅**: API 호출 건수와 비용을 기록해 예산 초과를 방지

## 5. 프롬프트 작성 팁 — 요소 분해 후 단계적 합성

복잡한 장면(여러 캐릭터·스타일·동작이 섞인 상황)을 한 번에 통째로 프롬프트에 넣고 생성을 요청하면, AI가 너무 많은 요소를 동시에 계산해야 해서 처리 시간이 길어지고, 요소들의 데이터·세계관이 서로 겹쳐 엉뚱한 결과물이 나오기 쉽습니다.

**해결 방법: 요소를 하나씩 나눠서 만든 뒤 단계적으로 합성**

예를 들어 "아이언맨 슈트를 입은 슈퍼마리오가 레고 질감 도로를 걷다가 점프해서 식인 괴물을 밟고 기뻐하는 장면"처럼 복합적인 이미지가 목표라면:

1. 요소를 개별적으로 나눈다
   - 아이언맨 슈트를 입은 슈퍼마리오를 그려줘
   - 레고 질감으로 만들어진 도로를 아케이드 스타일로 그려줘
   - 파이프 아래에서 위로 나오는 식인괴물을 그려줘
2. 각 요소를 개별 프롬프트로 먼저 생성한다.
3. 생성된 요소들을 한 번에 합성해달라고 요청한다.
4. 합성된 결과물을 기준으로 동작·표정 등 디테일을 순차적으로 덧붙여 수정한다.
   - 예: "슈퍼마리오가 식인괴물 위에서 점프하는 모습을 그려줘" → "슈퍼마리오가 웃는 표정을 짓도록 변경해줘"

이 방식은 이미지뿐 아니라 영상 제작의 씬(scene) 구성에도 동일하게 적용됩니다. 오히려 영상을 만들 때만 씬을 나누는 것이 아니라, **이미지 작업 단계에서부터 요소 하나하나의 디테일을 따로 살려낸 뒤 합치는 습관**을 들이는 것이 결과물 품질을 높이는 핵심입니다.

이 접근을 자동화 파이프라인에 반영하면, 6장의 `GenerationRequest`를 "개별 요소 생성 요청" 목록과 "합성 요청" 단계로 나누어 구성할 수 있습니다 (예: 1차 배치로 요소별 이미지를 생성 → 2차 요청으로 합성 → 3차 요청으로 디테일 수정).

## 6. 범용 자동화 스크립트 구조 (Python 예시)

아래는 특정 서비스에 종속되지 않도록 인터페이스를 추상화한 예시 구조입니다. 실제 사용 시 `generate()` 내부에 원하는 서비스의 API 호출 코드를 채워 넣으면 됩니다.

```python
"""AI 이미지·영상 생성 자동화를 위한 범용 파이프라인 예시.

특정 AI 서비스에 종속되지 않도록 생성기(Generator)를 추상화하여,
서비스를 교체하더라도 파이프라인 코드는 그대로 재사용할 수 있게 구성했다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class GenerationRequest:
    """생성 요청 1건에 대한 정보를 담는다."""
    prompt: str
    output_path: Path
    extra_options: dict


class MediaGenerator(ABC):
    """이미지·영상 생성기의 공통 인터페이스.

    서비스별 실제 구현(예: Stable Diffusion 호출, Canva API 호출 등)은
    이 클래스를 상속받아 generate()만 구현하면 된다.
    """

    @abstractmethod
    def generate(self, request: GenerationRequest) -> Path:
        """실제 생성 로직을 수행하고 결과 파일 경로를 반환한다."""
        raise NotImplementedError


class DummyGenerator(MediaGenerator):
    """실제 서비스 연동 전, 파이프라인 동작을 확인하기 위한 예시 구현."""

    def generate(self, request: GenerationRequest) -> Path:
        # TODO: 실제 서비스의 이미지/영상 생성 API 호출 코드로 교체
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_text(f"prompt: {request.prompt}")
        return request.output_path


def run_batch(generator: MediaGenerator, requests: list[GenerationRequest]) -> list[Path]:
    """여러 생성 요청을 순차 처리하며, 개별 실패가 전체를 중단시키지 않도록 처리한다."""
    results: list[Path] = []
    for req in requests:
        try:
            result_path = generator.generate(req)
            logger.info("생성 완료: %s", result_path)
            results.append(result_path)
        except Exception as exc:  # noqa: BLE001 - 배치 작업 중 개별 실패를 격리하기 위한 광범위 처리
            logger.error("생성 실패 (prompt=%s): %s", req.prompt, exc)
    return results


if __name__ == "__main__":
    requests = [
        GenerationRequest(
            prompt="맑은 하늘 아래 도시 전경",
            output_path=Path("output/city_view.txt"),
            extra_options={},
        ),
    ]
    run_batch(DummyGenerator(), requests)
```

### 실제 서비스에 연결하려면

1. `MediaGenerator`를 상속한 클래스를 새로 만든다 (예: `StableDiffusionGenerator`, `CanvaGenerator`).
2. `generate()` 안에서 해당 서비스의 SDK/REST API를 호출하고, API 키는 환경 변수로 관리한다.
3. `run_batch()`는 그대로 재사용하면 되므로, 서비스 교체 시 파이프라인 코드를 다시 작성할 필요가 없다.

## 7. 도입 시 체크리스트

- [ ] API 키/자격 증명을 코드에 하드코딩하지 않고 환경 변수 또는 시크릿 매니저로 관리했는가
- [ ] 생성 결과물에 대한 저작권·라이선스 정책을 확인했는가
- [ ] 대량 생성 시 비용 상한선과 알림 체계를 마련했는가
- [ ] 생성 실패 시 재시도 및 로그 기록이 되는가
- [ ] 결과물 저장 경로와 네이밍 규칙이 일관되는가
- [ ] 복잡한 장면은 요소별로 나눠 생성한 뒤 합성하는 절차를 프롬프트 설계에 반영했는가

## 8. 참고 원칙

새로운 프로젝트이므로 특정 도메인(예: 상수도 현장 업무)과는 무관하게 독립적으로 운영합니다. 추후 특정 AI 서비스(API 키 보유 여부)나 활용 목적이 정해지면, 이 문서의 6장 구조를 바탕으로 해당 서비스 전용 `Generator` 구현체를 추가하는 방식으로 확장하면 됩니다.
