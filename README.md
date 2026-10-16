# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_3.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.3: 5속성 전용 비트보드 탐색, 속성 조합별 후보 유지, 10초 상한, 조건 충족 즉시 반환. 자동 갱신과 퍼즐판 수정 기능은 제거된 상태입니다.
회귀 테스트 33개 통과. 기존 실패판 23/23(평균 탐색 0.167초), 생성 보드 100/100, 최소 수량 보드 14/20 성공. [검증 조건과 한계](SOLVER_BENCHMARK.md)를 확인하세요. Windows/MuMu 실기기 검증은 별도 필요합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
