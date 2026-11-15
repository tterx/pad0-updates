# 퍼드0 올인원

[**프로그램 ZIP 다운로드 · v1.0.98**](https://github.com/tterx/pad0-updates/releases/download/v1.0.98/pad0_overlay_windows_v1_0_98.zip) · [전체 릴리스](https://github.com/tterx/pad0-updates/releases)

현재 표시 버전은 **1.0.98**, 자동 업데이트 내부 순번은 **306**입니다.

새로 설치할 때는 ZIP을 풀고 `시작.vbs`를 실행하세요. 기존 설치는 PAD0를 완전히 종료한 뒤 `시작.vbs`를 다시 실행하면 업데이트됩니다.

달인다속에서 잠금 해제 불가 시에도 검증한 콤보 경로로 진행하고, 같은 조건의 실행 직전 재확인마다 경로를 다시 계산하지 않도록 했습니다. F10 이후 계산 버튼의 캡처 유효시간·이전 미완료 검사 처리도 보완했습니다. Windows 검사 1,253개와 독립 번들 검사 84개 통과. [변경 내용](docs/reviews/REVIEW308.md) · [검증 기록](releases/v1.0.100/release_checks308.json)

## 저장소 안내

| 폴더·파일 | 내용 |
| --- | --- |
| [docs](docs/README.md) | 사용·기술 문서, 버전별 변경 기록과 검증 자료 |
| [releases](releases/README.md) | 버전별 프로그램, ZIP, 배포 검증과 업데이트 기록 |
| [data/catalog](data/catalog) | 크리처 목록 원본 JSON·CSV |
| [documents](documents) | 이벤트 문서 템플릿 |
| [update.json](update.json) | 프로그램이 사용하는 최신 자동 업데이트 주소 |

이전의 긴 메인 안내문은 [안내문 보관본](docs/history/README-before-cleanup.md)에서 볼 수 있습니다. 다음 배포 시 파일을 둘 위치는 [저장소 관리 안내](docs/MAINTAINING.md)를 참고하세요.
