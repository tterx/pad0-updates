# UI 모듈 분리 (v101.144)

`pad0_ui.py`는 주요 창·오버레이 6개, 화면 구성 메서드 18개와 배치 보조 함수 6개를 담당합니다. `AppUIMixin`은 메인 App의 상태와 작업 콜백을 사용합니다.

파티 설정·연결 마법사의 장치 및 스킬 API는 `UIBackendServices`로 전달합니다. UI는 메인 모듈이나 입력 장치를 역으로 불러오지 않으며, import만으로 창·장치·작업을 시작하지 않습니다. 자동파밍 판단, 작업 시작·중지, 캡처 수명과 F10 처리는 메인에 유지합니다.

개발 소스는 common / crystal / input / solver / ai / ui 모듈을 사용합니다. 배포 소스에는 6개 모듈을 내장하므로 기존 단일 파일 업데이트와 백업 복원 방식이 유지됩니다.

UI 경계 검사: `PYTHONPATH=tests .venv/bin/python -m unittest test_ui_module144 test_attribute_strip91`
