# 크리스탈 모듈 구조

`pad0_crystal.py`는 이름과 ID 연결, 재료 그룹·재고·보상 영수증, 선택 재료 검증, 이미지/스크롤 비교, 던전 번호·훈장/드롭 선택을 담당합니다. 메인 오버레이나 Tk, OCR 엔진, Android 입력을 import하지 않습니다. 이미지 함수의 NumPy/OpenCV는 호출 시에만 로드합니다.

`pad0_overlay.py`는 UI, OCR 엔진과 토큰 생성, SDK 캡처·입력, 취소 제어, 파일의 원자적 저장을 담당하며 크리스탈 함수를 import해서 사용합니다. 몬스터 연결은 메인에서 카탈로그 목록을 전달합니다. 기존 JSON 필드와 함수 호출 경로를 유지합니다.

빠른 이름·재고 검증:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_crystal_core*.py'
```

기존 테스트 21개를 그대로 옮긴 검사입니다. 메인 프로그램이나 OCR 모델을 초기화하지 않습니다. 이미지/SDK/창 연동을 바꾸거나 배포할 때에는 전체 검사도 실행합니다:

```sh
PYTHONPATH=tests .venv/bin/python -m unittest discover -s tests
```

## 기존 자동 업데이트 호환

이전 실행기는 SHA-256으로 확인한 pad0_overlay.py 하나만 교체하고 같은 파일만 백업합니다. 따라서 `tools/build_release_source.py`는 배포용 파일에 압축된 기능 모듈을 포함합니다. 시작 시 해당 버전의 모듈을 메모리에 로드하므로 예전 ZIP의 오래된 모듈 파일이 남아 있어도 새 코드로 실행되고, 백업 파일만 복원해도 실행할 수 있습니다. 추가 네트워크 요청이나 사용자 데이터 저장은 하지 않습니다.

개발 파일은 일반 import를 사용합니다. 배포 파일은 생성물이며, 기능 수정은 pad0_crystal.py에서 합니다. 배포용 단일 파일 생성:

```sh
.venv/bin/python tools/build_release_source.py /tmp/pad0_overlay_release.py
```

ZIP에는 분리된 모듈 파일도 포함합니다. 배포 manifest의 해시는 생성된 배포용 파일을 기준으로 계산합니다. 학습 DB·모델·사용자 설정·토큰은 배포물에 넣지 않습니다.
