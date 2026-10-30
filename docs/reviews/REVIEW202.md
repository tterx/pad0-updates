# v101.202 — 068 스킬 중단 및 배포 폴더 정리

## 첨부 068에서 확인한 사실

v101.201, Android 표준 스킬 탭 선택. 캐릭터 탭(native 445,840)은 약 65ms에 명령 완료됐지만, 다음 캡처가 `MAA 작업을 중지했습니다`로 실패했다. phase=background_skill_open, skill_use_requested=false이므로 사용하기 클릭이나 스킬 발동 확인까지 도달한 실패가 아니다.

MaaFrameworkTouch.repeat_release(cancel=False)는 Android 탭 전 기존 접점을 정리하면서 input_cancel을 켜고, 성공해도 해제하지 않았다. 이어지는 Android 탭은 MAA input 작업을 거치지 않으므로 이벤트가 계속 남았고 MAA 캡처 대기를 취소했다. 일반 접점 해제 성공 시에만 캡처 대기 취소 신호를 해제한다. 같은 컨트롤러의 I/O 잠금 안에서 해제 확인과 캡처 재개를 처리해 중간에 다른 캡처가 끼지 않도록 한다. device.cancel은 지우지 않으며 F10 전후 확인, cancel=True, 해제 실패 시 중지 상태를 유지한다. 다른 입력 방식으로 전환하거나 다시 누르지 않는다.

## 변경 파일

- pad0_input.py: MaaFrameworkTouch.repeat_release의 일반 해제 후 캡처 재개.
- pad0_launcher.py: 설치 루트에서 단일 실행 잠금 유지, app/layout_manifest의 알려진 옛 평면 파일만 archive에 되돌릴 수 있게 이동. 다른 파일, DB/모델/개인 설정, platform-tools 보존. 새 루트의 README.md/run.bat/시작.vbs는 보관 이동에서 제외한다.
- run.bat: app 실행·의존성 경로 지원, logs/startup.log 생성. 개발용 루트 실행도 유지.
- pad0_overlay.py: app 배치 시 ADB 상대 경로는 설치 루트 유지, 디버그 안내 경로 갱신.
- tools/package_windows.py: 실행 파일 3개만 루트에 두는 app/docs/tools ZIP 생성 및 파일 정합성 확인. 별도 자료는 기존 위치 유지.
- tools/windows/README.md: 새 설치·이전 설치 덮어쓰기·되돌리기·자동 업데이트 범위 안내.

새 ZIP 루트: 시작.vbs, run.bat, README.md / app / docs / tools. 실행 후 logs와 필요한 경우 archive가 생긴다. 기존 자동 업데이트는 단일 번들 교체이므로 폴더 정리는 ZIP 전체 덮어쓰기가 필요하다. 소스 저장소의 개발 모듈 경계와 가져오기는 유지한다.

저장된 MaaTouch/SDK 선택과 속도·코너 설정을 유지한다. 사용자가 보고한 17~18ms를 새 기본값으로 강제하지 않는다. 캡처64, 경로 계산, 시작/마지막 판 검증, 실패 ZIP, F10 유지.

## 확인 범위

첨부 로그 및 코드 정적 검토, Python 구문, 번들·ZIP CRC/바이트·업데이트 SHA256·원격 배포 정합성만 확인. 단위검사/속도검사/시뮬레이션/에뮬레이터 조작은 실행하지 않는다. 실기 스킬 성공, 동시 눌림 해소는 아직 확정할 수 없다. 직접 한 번 실행해 스킬 팝업→사용하기→효과 확인과 F10, 기존 설정/ADB 경로 유지 여부를 확인한다.
