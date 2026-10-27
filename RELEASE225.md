# 퍼드0 올인원 1.0.17 / updater225 배포 준비

1.0.16의 현재 기능 코드를 유지하고 표시 버전과 자동 업데이트 순번만 올립니다. 새 기능이나 게임 동작 변경은 없습니다.

- 소스 저장소 커밋: tterx/pad0-all-in-one@305dbaa
- 배포 파일: pad0_overlay_windows_v1_0_17.zip
- 자동 업데이트용 번들: releases/v1.0.17/pad0_overlay.py
- 전환용 manifest: update225.json (현재 update.json에는 적용하지 않음)
- 검증 근거: release_checks225.json

이 브랜치는 배포 준비 상태입니다. GitHub 릴리스 v1.0.17의 발행 및 다운로드 검증 후 update225.json을 update.json에 적용하고 README 다운로드 링크를 갱신해야 합니다. 현재 환경의 GitHub API/업로드 도메인 접근 제한 때문에 릴리스 발행은 수행하지 않았습니다.

실제 Windows/MuMu 게임 조작과 새 Windows CI 결과는 이번 Linux 검사로 검증하지 않았습니다. 기존 사용자 설정·DB·모델은 패키지에 포함하지 않습니다.
