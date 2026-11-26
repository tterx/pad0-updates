# v1.0.143 달인 다색 자동반복 진입과 경로 계산창

256·257번은 잠입 확인 화면까지 도달했고 도전 입력 전에 멈췄습니다.
기존 판독은 실제 카드 윗줄과 그 위 장식에서 두 후보를 찾은 뒤
파티를 확인할 수 없다고 판단했습니다. 이제 카드 윗 테두리가 연속
세 줄에서 확인돼야 하며, 안정적인 판독에는 여전히 두 캡처가 필요합니다.

스테이지 지도는 하단의 파란 팀 버튼과 초록 육성 버튼을 함께 확인합니다.
지도에서 기존 전투 표시를 해제하고, 지도 확인보다 새로운 퍼즐판이 확인되면
다시 표시할 수 있습니다. 지도에서는 계산창 항상 표시 설정을 켜둔 경우에도
계산창을 숨깁니다. 전투 중 수동 경로 실행은 진행 상태를 표시합니다.

Codex에서 256·257번의 저장 화면으로 도전 입력 직전까지 재현했습니다.
도전 입력은 모의 기기로 확인했습니다. 지도 판독은 동일한 하단 메뉴가 있는
기존 지도 캡처와 전투·잠입 화면으로 확인했습니다. 실제 Windows와 MuMu에서의
자동반복은 검증 범위에 포함되지 않았습니다.

Codex 소스 검사 268개와 배포 코드 검사 265개를 통과했습니다.

Publication verified: PR54 merged as f2374601b694c5d397e12ae30442ec3cc14c573c.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.143 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.143 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates352.
The unchanged launcher installed352 from the live manifest over v1.0.142 in an
isolated installation at state351, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.

별도의 Codex 시작 검사에서 v1.0.142가 공개 자동 업데이트를 설치하고,
v1.0.143 Tk 창을 표시했으며 사용자 설정과 재료 장부를 보존했습니다.
