# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_9.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.9: 돌파 대상과 재료 이름을 분리하고, 게임에서 클릭한 재료의 상세창에서 F7을 누르면 해당 재료에 이름을 연결합니다. 기존 라·오시리스 등 DB 이름을 관리목록과 결과 통계에 표시하고 이미지와 ID를 재사용합니다. 관리창 고정 표시와 F7 시 고정 설정도 수정했습니다. [사용 안내와 검증 범위](FARMING_STATS.md)
회귀 테스트 142개와 가상 Tk GUI 검증을 통과했습니다. 실제 Windows/MuMu의 클릭 연결은 별도 확인이 필요합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
