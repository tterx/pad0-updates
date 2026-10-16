# PAD0 개인용 업데이트 배포

[최신 실행 패키지 다운로드](https://raw.githubusercontent.com/tterx/pad0-updates/main/pad0_overlay_windows_v101_15.zip)

기존 프로그램을 종료하고 ZIP을 새 폴더에 압축 해제한 뒤 `시작.vbs`로 실행합니다. 이후 실행할 때 최신 버전을 확인합니다.
설정과 개인 DB는 기존 LocalAppData/Pad0Overlay에서 읽으며 배포 파일에 포함하지 않습니다.

업데이트 주소: https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json

현재 v101.15: F7에서 돌파 대상 제목의 작은 회색 글자를 놓치는 경우 글자만 좁혀 한국어 인식기에 직접 전달합니다. 대비 보정·이진화 두 결과가 일치할 때 이름을 채택하며, 확인한 대상 이름은 등록 대기 중에도 표시합니다. 이전 대상 이름을 임의 재사용하지 않습니다. [사용 안내](FARMING_STATS.md#f7-돌파-대상-이름-판독-보완-v10115)
회귀 테스트 165개와 가상 Tk GUI의 아텐·필요7/보유2 흐름 검증을 통과했습니다. 실제 한국어 모델로 합성 회색 제목 아텐(16/20px), 오시리스, 라의 크리스탈을 판독했습니다. 첨부 화면 자체의 픽셀 OCR 및 Windows/MuMu 실기기 검증과 구분합니다.
자동 경로·스크린샷 최근 기록은 중단 상태를 유지하며, 오류 저장 버튼 또는 수동 F12 진단으로만 진단 파일을 남깁니다.

다운로드 파일 보안경고는 [Windows 실행 안내](UPDATE_SETUP.md#다운로드-파일의-windows-보안경고)를 참고하세요. ZIP 속성의 차단 해제 후 새 폴더에 압축 해제하는 방법을 설명합니다. Windows 보안 설정을 자동으로 바꾸지 않습니다.

이 저장소와 배포 파일은 공개되어 있습니다.

추후 업데이트는 새로운 releases/<버전>/pad0_overlay.py를 추가하고, update.json의 release 정수를 증가시켜 source_url 및 SHA-256을 갱신합니다.
현재 실행기는 pad0_overlay.py만 자동 교체합니다. 실행기나 의존성이 바뀌면 전체 패키지를 다시 설치합니다.
