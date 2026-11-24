# v1.0.138 · 달인 다색 재접속에서 기존 던전 재개

251번에서는 겅호 로고, 시작 버튼, 첫 번째 저장 데이터가 차례로
선택됐습니다. 마지막에는 ‘잠입 도중인 던전이 있습니다. 다시
도전하시겠습니까?’ 확인창이 떠 있었습니다. 전체 화면 판독에서
안내 두 줄과 왼쪽 버튼을 완전히 읽지 못해 선택이 보류됐습니다.

재접속 시 확인창의 두 버튼 위치를 확인하고, 안내 문구와 버튼 글자가
빠지면 해당 영역을 두 크기로 직접 읽습니다. 같은 확인창이 새 화면
두 장에서 확인되면 오른쪽 ‘네’를 한 번 누릅니다.

기존 스테이지, 파티 정보, 보상 회차를 이용해 전투를 이어갑니다.
새 퍼즐판과 파티를 확인한 다음 자동 전투를 재개합니다. 저장 데이터
선택 후 확인창 없이 홈으로 들어온 경우 기존 재료 검색 흐름을
이용합니다.

Codex에서 251번 실제 화면의 ‘네’ 위치를 판독했습니다. 모의 재접속에서
중단했던 보상 회차를 이어서 한 번만 집계하는 것과 F10 취소를
검증했습니다. 실제 Windows와 MuMu 재접속은 미검증입니다.

Codex 소스 검사 87개와 배포 코드 검사 84개를 통과했습니다.

Publication verified: PR49 merged as d158c18b9c8d1153f819e2432f6fa33e9fb4c806.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.138 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.138 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates347.
The unchanged launcher installed347 from the live manifest over v1.0.137 in an
isolated installation at state346, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.

Codex의 격리 설치에서 v1.0.137이 v1.0.138로 자동 업데이트됐으며, 기존 장부와 설정을 유지한 채 메인 창이 열렸습니다.
