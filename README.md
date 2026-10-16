# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_8.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.8: F7 필요 수량의 작은 숫자 검출 실패를 수정했습니다. 게임 숫자 모양 판독을 복구하고, 글자 검출을 생략한 숫자 직접 인식으로 필요/보유 수량을 보완합니다. [사용 안내와 검증 범위](FARMING_STATS.md)
회귀 테스트 119개 통과. 실제 한국어 모델로 합성 5종 재료판의 필요 6·3·3·3·3 / 보유 4·4·8·10·12를 판독했습니다. 실제 첨부 이미지의 픽셀 OCR 및 Windows/MuMu 실기기 검증과는 구분합니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
