# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_10.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.10: 프로그램 실행 후 최초 F7에서 크리스탈 관리창 고정을 켭니다. 이후 F7에서는 사용자가 고정 버튼으로 변경한 상태를 유지합니다. [사용 안내](FARMING_STATS.md)
회귀 테스트 142개와 가상 Tk GUI에서 최초 F7 고정·수동 해제 후 재스캔 상태 유지를 확인했습니다. 실제 Windows/MuMu 창 고정은 별도 확인이 필요합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
