# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_7.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.7: 돌파 화면에서 F7로 대상 이름과 전체 재료를 등록하고, 전투 결과창에서 자동 집계합니다. 12시 파밍 표시줄과 보상 계산 버튼을 제거했습니다. 카드 아래 수량만 세며 크리스탈 획득 옆 ×2 배율은 사용하지 않습니다. [자동 집계 사용 안내](FARMING_STATS.md)
회귀 테스트 107개와 가상 Tk GUI 검증 통과. 실제 Windows/MuMu의 한국어 OCR·자동 스크롤·마우스 보호는 별도 확인이 필요합니다. 탐색 알고리즘은 v101.4 그대로이며 [이전 성능 측정과 한계](SOLVER_BENCHMARK.md)를 확인하세요.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
