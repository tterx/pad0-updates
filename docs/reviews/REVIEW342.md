# v1.0.133 · 달인다속 재실행 영상 건너뛰기와 이동 한도 재계산

242번은 보유 한도 경고 카드가 있는 결과창의 스크롤 직전 Android에서
게임을 확인하지 못한 기록입니다. 243번은 그 재접속의 마지막 캡처로,
‘시작’ 버튼이 화면에 있었지만 제목 전체 OCR은 버튼 글자를 놓쳤습니다.
245번은 로고를 한 번 누른 뒤 어두운 크리처 화면에서 대기하다 멈췄습니다.

재실행 중 ‘시작’이 보이면 버튼 글자와 색 영역을 확인합니다. 전체 읽기가
글자를 빠뜨리면 중앙 제목과 버튼 형태를 확인한 뒤 글자 영역을 따로 읽습니다.
크리처 전환 화면은 로고 이후에만 확인합니다. 동일 위치를 두 새 캡처에서
확인하고 간격을 두어 누르며, 로고와 영상 클릭 합계는 최대 여섯 번입니다.
오른쪽 위에 ‘넘어가기’에 해당하는 글자가 보이면 위치를 확인해 한 번 누릅니다.
그 뒤 ‘시작’, 1번 저장 데이터, 던전 복귀 안내 순으로 처리합니다.
F10은 클릭 전에 확인합니다.

244번은 8초 타이머에서 23칸 경로를 계산했지만 입력 직전 한도를 넘었습니다.
달인다속은 동일 판과 타이머에서 더 작은 한도를 기록하고 해당 한도로
경로를 다시 계산합니다. 기존 4초 확보와 실행 직전 확인을 적용합니다.

243번 ‘시작’과 245번 크리처는 실제 캡처에서 확인했습니다. 오른쪽 위
넘어가기 버튼과 클릭 후 전환은 모의 화면으로 검증했습니다. 244번 실제
판 배열의 모의 19칸 한도에서는 17칸, 5속성·6콤보 경로를 찾았습니다.
실제 MuMu 실행과 클릭 결과는 미검증입니다.

Codex 소스 검사 91개와 배포 코드 검사 35개를 통과했습니다.

Publication verified: PR44 merged as 67843850169a69974f365e1465c1c16f4a75b006.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.133 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.133 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates342.
The unchanged launcher installed342 from the live manifest over v1.0.132 in an
isolated installation at state341, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
