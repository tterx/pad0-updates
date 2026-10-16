# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_14.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.14: 자동 경로·스크린샷 최근 기록을 중단했습니다. 이동 전 원본 한 세트와 경로·입력 정보는 메모리에만 보관하고 **오류 저장** 버튼 또는 수동 **F12 진단**을 실행할 때만 `debug/manual_errors`에 저장합니다. 일반 실행 중 이미지 저장 작업을 시작하지 않고 이동 후 자동 캡처도 하지 않습니다. [사용 안내](INVENTORY_PRECHECK.md#수동-오류-저장과-메모리-보관-v10114)
1602×941 게임+690×575 보드의 합성 RGB 캐시를 Linux RSS로 측정한 추가 메모리는 약 7~9MiB였고, 100회 교체해도 누적 증가하지 않았습니다. Windows 실기기 측정과 구분합니다. 이전 버전 파일은 자동 삭제하지 않습니다.
회귀 테스트 158개와 가상 Tk GUI에서 자동 진단 파일 없음·수동 저장·경로 실행 중 창 숨김을 검증했습니다. 실제 게임 매치 실패의 원인을 자동 확정하는 기능은 아닙니다.

다운로드 파일 보안경고는 [Windows 실행 안내](UPDATE_SETUP.md#다운로드-파일의-windows-보안경고)를 참고하세요. ZIP 속성의 차단 해제 후 새 폴더에 압축 해제하는 방법을 설명합니다. Windows 보안 설정을 자동으로 바꾸지 않습니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
