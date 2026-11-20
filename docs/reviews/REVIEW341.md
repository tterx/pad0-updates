# v1.0.132 · 달인다속 게임 재접속의 저장 데이터 선택

241번은 MuMu에 재연결하고 퍼즐드래곤 0를 다시 실행해 로고와 시작 버튼을
눌렀습니다. 저장 데이터 선택 단계에서 기다리다가 종료됐고, 1번 카드 입력
기록은 없습니다. 실패 화면에는 1번의 저장 데이터와 빈 2·3번 카드가 있습니다.

실제 241번 실패 화면에서 전체 화면 OCR은 1번 카드의 랭크와 날짜를
읽었지만 선택 화면 제목을 빠뜨렸습니다. 정렬된 카드 세 장과 저장된
1번 카드가 확인될 때 제목 부분을 원본과 두 배 크기로 읽도록 보완했습니다.
두 번 모두 저장 데이터 선택 문구를 확인해야 통과합니다. 새 캡처 두 장에서
같은 카드가 확인되면 기존처럼 중앙을 한 번 누릅니다. 빈 1번 카드,
다른 제목, 낮은 신뢰도, F10은 입력을 차단합니다.

실제 241번 캡처에서 두 제목 판독의 신뢰도는 0.96356과 0.94507이었고,
확인된 카드 중앙은 1600×940 화면의 (800, 410)입니다. 모의 재접속은
1번 선택과 기존 던전 복귀 안내까지 검증했습니다. 실제 MuMu에서 터치
전달과 복귀는 미검증입니다. 240번은 v1.0.130의 전투 중 시작 드롭
확인 실패입니다. 이번 수정 범위는 저장 데이터 선택입니다.

Codex 소스 검사 57개와 배포 코드 검사 31개를 통과했습니다.

Publication verified: PR43 merged as d49e5627507e23f4351d436f9c7dc8f6f5f48865.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.132 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.132 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates341.
The unchanged launcher installed341 from the live manifest over v1.0.131 in an
isolated installation at state340, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
