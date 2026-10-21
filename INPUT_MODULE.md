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

## v101.155 해제·이동 보강

실제 제스처 직전과 종료·취소 후 SDK 1~10번 및 기본 터치 해제. 단순 연결 검사는 읽기 전용을 유지합니다. F10과 정상 종료에서는 SDK를 사용한 장치에 한해 알려진 MuMu 창·자식 창에 WM_LBUTTONUP/WM_CANCELMODE 및 Windows UP만 보냅니다. 새 누름·커서 이동·전면 활성화는 하지 않습니다. 외부에서 강제 종료하면 정상 종료 정리는 실행되지 않을 수 있습니다.

경로 전송은 목표 시각을 기준으로 보정하되 최소 1/60초 간격과 모든 경유점을 유지합니다. SDK 반환 성공을 게임 반영 성공으로 간주하지 않으며, 놓기 전 판 비교를 유지합니다. 오류 자료에 정리 결과·최대 MOVE 간격·50ms 초과 횟수를 저장합니다. 실제 MuMu 게임의 입력 잔류 해제는 현장 확인이 필요합니다.

## v101.156 첫 드롭 확인과 방향 전환

실제 퍼즐 경로에만 시작 판·출발 칸·좌표를 SDK 작업자에게 전달합니다. 같은 지점에서 DOWN을 유지하고 직접 SDK 화면을 읽어, 지정 드롭의 무늬가 위로 이동한 것을 확인한 뒤 MOVE를 보냅니다. 최대 4회 확인하며, 전체 경로를 마칠 시간은 먼저 예약합니다. 확인 실패·취소·캡처 오류 시 새 DOWN이나 경로 재시도 없이 놓고 중지합니다. 일반 UI tap·스크롤·구름 확인용 단일 누름은 이 경로와 구분합니다.

시작 판정은 원래 무늬·색과 들린 무늬의 일치도, 원위치 대비 개선, 변화 면적을 함께 사용합니다. RGBYPH·독·방해를 지원하며, 구름 칸은 기존 들림 판독으로 실제 나타난 드롭을 확인합니다. 반짝임·밝기 변화·정지 화면은 통과시키지 않습니다. SDK adapter가 이미지 판독을 담당하고 입력 모듈은 콜백을 호출하므로 입력 모듈의 독립성을 유지합니다.

방향 전환·왕복 지점에서는 다음 좌표 전송까지 최소 31.25ms를 유지합니다. 중간 경유점을 생략하지 않고 DOWN은 하나로 이어 갑니다. 코너 비용을 포함한 보수적 이동 예산은 칸당 0.08125초이며, 4초 사용 한도에서 36회 이동을 허용합니다. 마지막 판 검증·손 떼기 후 반응 확인·F10 해제는 유지합니다. 시작 판정 점수·횟수·시간과 실제 SDK display_id를 오류 로그에 남깁니다.

첨부 v101.155에서는 첫 회복 드롭 대신 빨강을 잡고, 뒤쪽 일부 경로만 반영된 판이 확인됐습니다. 정상 19ms 전송 간격과 UP 성공 응답만으로 게임 반영 성공을 판단하지 않습니다. 이번 시작 확인·코너 보강의 실제 MuMu 효과는 현장 확인이 필요합니다.
