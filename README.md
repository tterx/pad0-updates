# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_6.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.6: 자동 이동 중 마우스 개입을 차단하고 F10 키보드 중지는 유지합니다. 계산 버튼은 실제 퍼즐판을 인식한 뒤 표시하며, 5속성 수량 안내는 5속성 공격 선택 시에만 표시합니다. [동작 안내](INVENTORY_PRECHECK.md)
회귀 테스트 70개 통과. 실제 Windows/MuMu의 마우스 보호와 화면 판정은 확인이 필요합니다. 탐색 알고리즘은 v101.4 그대로이며 [이전 성능 측정과 한계](SOLVER_BENCHMARK.md)를 확인하세요.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
