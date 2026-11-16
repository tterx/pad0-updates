# 퍼드0 올인원

[**프로그램 ZIP 다운로드 · v1.0.115 복구**](https://github.com/tterx/pad0-updates/releases/download/v1.0.115/pad0_overlay_windows_v1_0_115.zip) · [전체 릴리스](https://github.com/tterx/pad0-updates/releases)

현재 배포는 **1.0.115 복구본**, 자동 업데이트 내부 순번은 **325**입니다.

새로 설치할 때는 ZIP을 풀고 `시작.vbs`를 실행하세요. 기존 설치는 PAD0를 완전히 종료한 뒤 `시작.vbs`를 다시 실행하면 업데이트됩니다.

v1.0.116에서 잠입·계산 버튼 미표시 추가 회귀가 보고되어 직전 v1.0.115로 되돌렸습니다. 기존 계산 문제와 정확한 회귀 원인은 조사 중입니다. PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 v1.0.116 설치도 복구됩니다. [복구 검증](releases/rollback325/rollback_checks325.json)

## 저장소 안내

| 폴더·파일 | 내용 |
| --- | --- |
| [docs](docs/README.md) | 사용·기술 문서, 버전별 변경 기록과 검증 자료 |
| [releases](releases/README.md) | 버전별 프로그램, ZIP, 배포 검증과 업데이트 기록 |
| [data/catalog](data/catalog) | 크리처 목록 원본 JSON·CSV |
| [documents](documents) | 이벤트 문서 템플릿 |
| [update.json](update.json) | 프로그램이 사용하는 최신 자동 업데이트 주소 |

이전의 긴 메인 안내문은 [안내문 보관본](docs/history/README-before-cleanup.md)에서 볼 수 있습니다. 다음 배포 시 파일을 둘 위치는 [저장소 관리 안내](docs/MAINTAINING.md)를 참고하세요.
