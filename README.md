# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_11.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.11: 경로 계산 시 메인창·크리스탈 창이 게임판을 가리는 문제를 수정했습니다. 캡처 전에 메인창을 최소화하고 관리창을 숨긴 뒤 계산·실행 직전 확인·드래그가 끝날 때까지 유지합니다. 완료 후 관리창만 기존 고정 상태로 복원합니다. [사용 안내](INVENTORY_PRECHECK.md)
회귀 테스트 146개와 가상 Tk GUI에서 창 숨김·이동 예약 중 유지·종료 후 복원을 확인했습니다. 실제 Windows/MuMu 창 배치는 별도 확인이 필요합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
