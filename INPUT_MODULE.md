# 입력 모듈 구조

`pad0_input.py`는 WindowsDragInput·WindowsMaterialClickObserver, AndroidTouchPacer, PersistentAndroidTouch·AndroidBatchTouch·MuMuSDKTouch와 NativeSDKInputMixin을 담당합니다. 마우스 훅과 입력 태그, 타이밍, 한 경로의 DOWN/MOVE/UP, 취소·손가락 해제, 전송 결과·추적을 여기서 관리합니다. scrcpy/Android 일괄 실행의 기존 바이너리·해시·라이선스도 여기에 있습니다.

메인 `pad0_overlay.py`는 BackgroundADB 장치 연결과 화면 캡처·SDK 프로세스 수명주기, UI·F10 요청을 관리합니다. _MuMuNativeCapture는 입력 믹스인을 상속하고 캡처·DLL 수명주기는 그대로 담당합니다. 입력 모듈은 메인, GUI, OCR, 이미지 인식이나 퍼즐 알고리즘을 import하지 않습니다. import만으로 DLL을 로드하거나 사용자 폴더·DB를 만들지 않습니다.

## 장치가 제공하는 서비스

전송 객체의 device는 취소 이벤트, native_size, shell/프로세스 서비스와 추적·진단 필드를 제공합니다. SDK 해제에 새 연결이 필요하면 `device.create_input_capture_client(cancel)`을 사용합니다. BackgroundADB가 실제 MuMuCaptureClient를 생성하며 입력 모듈은 SDK 캡처 구현을 알지 않습니다. 취소된 플레이 이벤트와 독립적인 새 이벤트로 해제 채널을 엽니다.

기존 도구 파일 위치는 LOCALAPPDATA/Pad0Overlay/tools 아래 그대로입니다. 입력 서버를 준비할 때 해당 도구 폴더만 만들며 크리스탈 DB를 초기화하지 않습니다.

## 빠른 검사

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_input_core*.py'
```

기존 검사 14개를 그대로 옮긴 마우스 보호·취소·SDK 제스처·타이머 검사입니다. GUI·OCR·게임을 시작하지 않습니다. 실제 Windows 훅과 SDK 함수는 모의 객체로 검증하므로 실제 MuMu 실행 검증과는 구별됩니다.

입력 변경 시 장치·캡처·UI 통합 검사도 확인하고, 배포 전에는 전체 검사를 실행합니다.

```sh
PYTHONPATH=tests .venv/bin/python -m unittest discover -s tests
```

## 배포

개발에서는 분리된 모듈을 일반 import합니다. tools/build_release_source.py는 기존 실행기가 받는 단일 배포 파일에 공통·크리스탈·입력·탐색·AI 모듈을 함께 포함합니다. 따라서 오래된 외부 모듈 파일이 남아 있거나 단일 백업 파일만 복원해도 해당 버전의 모듈로 실행합니다. 추가 다운로드·사용자 데이터 변경 없이 메모리에 로드하며 ZIP에도 모듈 파일을 포함합니다.

Android 실행 바이너리 재현 검증 도구 tools/build_android_batch.py는 이제 pad0_input.py의 바이너리와 해시를 확인합니다. Java 소스와 실제 바이너리는 변경하지 않았습니다.
