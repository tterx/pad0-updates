# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_12.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.12: 자동 이동마다 원본 보드·인식표·예상 경로·실제 입력 좌표와 시간·이동 직후 화면을 `debug/route_records`에 기록합니다. 실패를 확인하면 F11로 최근 이동을 보존하고, 계산 버튼이 안 보이면 F12로 게임 화면과 표시 조건을 저장합니다. 일반 경로/버튼 진단은 각각 최근 20개를 유지하며 사용자 실패 표시는 자동 정리에서 제외합니다. [사용 안내](INVENTORY_PRECHECK.md)
입력 완료를 게임 매치 성공으로 단정하지 않습니다. 이 버전은 원인 분석 자료를 남기며 버튼 미표시나 실제 매치 실패의 원인을 아직 확정하지 않습니다.
회귀 테스트 154개와 가상 Tk GUI의 기록·복원 검증을 통과했습니다. 실제 Windows/MuMu 입력과 화면은 별도 확인이 필요합니다.

다운로드 파일 보안경고는 [Windows 실행 안내](UPDATE_SETUP.md#다운로드-파일의-windows-보안경고)를 참고하세요. ZIP 속성의 차단 해제 후 새 폴더에 압축 해제하는 방법을 설명합니다. Windows 보안 설정을 자동으로 바꾸지 않습니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
