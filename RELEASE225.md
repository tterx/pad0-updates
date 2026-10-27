# 퍼드0 올인원 1.0.17 / updater225

1.0.16의 현재 기능 코드를 유지하고 표시 버전과 자동 업데이트 순번만 올린 배포입니다.

- 소스: tterx/pad0-all-in-one@305dbaabab83a85cf8f60d75c8bd3d85f00e2653
- 공식 릴리스: https://github.com/tterx/pad0-updates/releases/tag/v1.0.17
- ZIP 업로드 해시 및 발행 후 다운로드 해시 일치 확인
- Windows CI 및 ZIP 무결성·번들 로딩·오프라인 업데이트 전환 검사 통과
- Linux 215개 중 211개 통과, 기존 실패 1개·자료 누락 오류 2개·Windows 전용 건너뜀 1개
- 실제 MuMu 게임 조작 미검증. 개인 설정·DB·학습 모델은 배포 파일에 포함하지 않음

자동 업데이트 manifest는 update.json(release225)입니다. 상세 검증 상태와 SHA256은 release_checks225.json에 기록합니다.
