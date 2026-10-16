# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_5.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.5: 5속성 모드에서 계산 버튼을 누르기 전에 수량 충족/부족을 표시합니다. 같은 판을 두 번 읽고 결과를 갱신하며, 경로 탐색과 실행은 F6/계산 버튼으로만 시작합니다. [사전 수량 확인 안내](INVENTORY_PRECHECK.md)
회귀 테스트 47개 통과. 실제 Windows/MuMu의 새 사전 표시와 창 판정은 확인이 필요합니다. 탐색 알고리즘은 v101.4 그대로이며 [이전 성능 측정과 한계](SOLVER_BENCHMARK.md)를 확인하세요.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
