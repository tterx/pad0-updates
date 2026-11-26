# v1.0.144 달인 다색 보상 집계와 생성 검색

258·259번은 SP20의 같은 보상 화면입니다. 다섯 카드 중 첫째와 넷째를
서로 비교하면 기존 유사도 기준에서 보류 영역에 들어갑니다.
이전 버전은 그런 카드 하나 때문에 나머지 카드 수량을 읽기 전에 끝났습니다.
이제 확인된 카드와 그 수량을 저장한 뒤 연전 판단을 이어갑니다.
미확인 카드의 위치와 후보 점수를 최근 집계 진단에 남깁니다.
미확인 카드가 목표 재료 후보인 분기에서는 그 판을 미획득으로 세지 않습니다.

260번은 SP19 보상을 이미 저장한 뒤 다음 재료 때문에 생성 목록으로 돌아온
상황입니다. 생성 대상 No.485에 대해 악동마녀차콜만 검색됐고 결과가 비었습니다.
이 경우 악동마녀챠콜로 한 번 더 검색하며, 열린 생성 확인 화면에서 대상 번호를 확인합니다.
기존 등록값과 보상 장부를 보존합니다.

Codex에서는 저장된 SP20 화면의 실제 카드 두 장을 각각 대조용으로 삼아
이전 버전의 중단을 재현했습니다. 수정본은 두 경우 모두 한 카드의 ×2를 실제 OCR로
판독하고 유사한 카드의 수량을 보류했습니다. 사용자의 F7 등록 원본은 ZIP에
포함되어 있지 않았습니다. 생성 검색의 대체 철자와 화면에서 연 No.485 확인은
모의 흐름에서 검증했습니다. Windows와 MuMu 실기 자동파밍은 미검증입니다.

Codex 소스 검사 182개와 배포 코드 검사 179개를 통과했습니다.

Publication verified: PR55 merged as d9bdc34207d23bf83e7190a2cb8bc782402aca31.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.144 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.144 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates353.
The unchanged launcher installed353 from the live manifest over v1.0.143 in an
isolated installation at state352, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.

별도의 Codex 시작 검사에서 v1.0.143이 공개 자동 업데이트를 설치하고,
v1.0.144 Tk 창을 표시했으며 사용자 설정과 재료 장부를 보존했습니다.
