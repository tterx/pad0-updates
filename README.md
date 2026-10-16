# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_13.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.13: **현재 인식한 퍼즐판** 제목 오른쪽에 **오류 저장** 버튼을 추가했습니다. 표시된 판의 이동 전 원본을 메모리에 보관하고 버튼을 누를 때만 `debug/manual_errors`에 따로 저장합니다. 확보된 게임 전체 화면·보드 원본·인식표·경로·정보를 포함합니다. 수동 저장본은 최근 자동 기록 개수와 무관하게 보존합니다. [사용 안내](INVENTORY_PRECHECK.md#현재-인식한-퍼즐판의-오류-저장-v10113)
기존 자동 기록과 F11/F12는 별도로 유지됩니다. 게임 매치 실패 원인을 자동 확정하는 기능은 아닙니다.
회귀 테스트 158개 및 가상 Tk GUI의 버튼 위치·원본 저장 검증을 통과했습니다. 실제 Windows/MuMu 입력과 화면은 별도 확인이 필요합니다.

다운로드 파일 보안경고는 [Windows 실행 안내](UPDATE_SETUP.md#다운로드-파일의-windows-보안경고)를 참고하세요. ZIP 속성의 차단 해제 후 새 폴더에 압축 해제하는 방법을 설명합니다. Windows 보안 설정을 자동으로 바꾸지 않습니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
