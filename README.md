# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_4.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.4: 정확히 3개뿐인 속성을 한 줄로 모으는 거리 평가를 추가했습니다. 5속성 발견 즉시 반환, 최대 탐색 10초, 목표 미달 실행 보류를 유지합니다.
회귀 테스트 35개 통과. 별도 생성 검증: 속성 각 3개+방해 15개 40/40, 한 속성만 3개 40/40, 두 속성만 3개 40/40 성공. 기존 검증을 포함한 총 319판을 검증했습니다. [조건과 한계](SOLVER_BENCHMARK.md)를 확인하세요. Windows/MuMu 실기기 검증은 별도 필요합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
