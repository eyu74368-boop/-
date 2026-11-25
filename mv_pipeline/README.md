# 노래 mp3 + 가사 → 뮤직비디오 (mvp)

노래를 분석해 소절 시간표를 만들고, 그 시간표에 맞춰 클립과 자막을 코드로 합성한다.

```
노래 분석 → 소절 시간표 → 클립 배치 → 빈 장면 생성 → 합성/자막 → 검수/교체
```

필요한 것: Python 3.10+, NumPy, Pillow, FFmpeg(libass 포함). librosa 등은 쓰지 않는다.

```
pip install -r mv_pipeline/requirements.txt
```

## 한 번에 실행

```
cd mv_pipeline
python -m mvp all 노래.mp3 --lyrics 가사.txt --vocals 분리/vocals.mp3 -o 작업폴더 --preview
```

작업폴더에 생기는 것:

- `report.md` 분석 보고서(BPM·마디·후렴 교차 확인·소절 시간표)
- `timeline.json` 박·마디 시간
- `lyrics_timed.json` 줄·단어 시간(손으로 고쳐도 됨)
- `lyrics.ass` 노래방 자막(\kf로 글자가 차오름)
- `edl.csv` 배치표(한 줄 = 장면 하나)
- `scenes.md` 아직 영상이 없는 빈 장면 목록과 그록용 프롬프트
- `mv.mp4` / `mv_preview.mp4` 완성본(빈 장면은 색 화면으로 채움)
- `review.jpg` 소절마다 한 장씩 뽑은 검수 시트

## 단계별

1. **노래 분석** `python -m mvp analyze 노래.mp3 --lyrics 가사.txt --vocals vocals.mp3 -o 작업폴더`
   - 박자: 스펙트럼 증가량(온셋)의 자기상관으로 BPM을 구하고, 온셋 봉우리에 직선을 맞춰 긴 곡에서도 박이 밀리지 않게 다듬는다. 마디 첫 박은 저음(킥)이 가장 센 박.
   - 가사 위치: 보컬 분리본의 음량 곡선을 쉼과 음량 골짜기로 끊어 소절 후보를 만들고, 가사 줄을 음절 수 기준으로 순서대로 맞춘다(동적 계획법). 줄 시작은 8분음표 격자에 붙인다.
   - (선택) 받아쓰기: `--asr-cmd "python gemma_asr.py {wav}"`로 로컬 AI(Gemma 4 E2B 등)를 붙이면, 한 마디 길이 창을 8분음표씩 옮겨 가며 받아써서 '첫 단어가 온전히 들리다 앞이 잘리는 지점'을 줄 시작으로 다듬는다. 받아쓰기 글자는 위치 찾기에만 쓰고 자막은 원본 가사를 쓴다.
   - 보정: 같은 구역(후렴)이 몇 마디 간격으로 반복되는지 보고서에 적고, 소리 반복 간격과 맞는지 표시한다.
2. **클립 배치** `edl.csv`를 고친다.
   - 열: `start`(초 또는 `마디:박`, 예 `12:1`), `clip`, `in`, `out`, `transition`(섞기 길이), `speed`, `top`(1이면 자막을 위로), `prompt`, `note`
   - 클립 동작 확인: `python -m mvp sheet 클립.mp4` → 0.5초 간격 썸네일 시트. 동작 절정(빛이 터짐, 착지)이 가사에 오도록 in/out을 정한다. speed를 비우면 in~out이 자리 길이에 맞게 늘거나 줄어든다.
3. **빈 장면 생성** `scenes.md`의 목록대로 만든다.
   - 첫 이미지: ComfyUI Qwen-Image-Edit 2509(Lightning LoRA, 4스텝)에 캐릭터 원본을 참조로. 기존 `4.음악/뮤직비디오/tools/mv_gen.py edit`를 그대로 쓴다.
   - 영상: 첫 이미지 + 영어 프롬프트로 그록 이매진.
   - 첫 이미지에 없는 부위는 영상 모델이 지어낸다. 작은 손 등은 첫 이미지에 미리 그려 넣고, 프롬프트에 크기를 고정하라고 적는다(scenes.md 공통 지시에 들어 있음).
   - 만든 영상은 `clips/scene_000.mp4`처럼 넣고 배치표 `clip` 칸에 적는다.
4. **합성/자막** `python -m mvp render 작업폴더 [--preview] [--fontsdir 글꼴폴더]`
   - FFmpeg로 클립을 1920×1080·24fps로 풀고, NumPy로 장면 전환을 섞은 뒤 H.264로 굽는다. 클립 소리는 버리고 원곡만 AAC로 넣는다.
   - 자막 글꼴 기본값은 `Griun Cocochoitoon`(Griun 코코초이툰). PC에 설치된 이름과 다르면 `analyze --font "이름"` 또는 `subs --font "이름"`.
5. **검수/교체** `python -m mvp review 작업폴더` → `review.jpg`
   - 자막이 얼굴을 가리면 그 장면 `top`을 1로. 동작이 가사와 어긋나면 그 줄의 in/out만 고쳐 다시 render.
   - 가사 시간이 틀리면 `lyrics_timed.json`을 고치고 `python -m mvp subs 작업폴더 --resplit`.
   - 최종 싱크는 사람이 들어서 확인한다.

## 시험

```
python -m pytest mv_pipeline/tests
```
