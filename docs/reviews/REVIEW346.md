# v1.0.137 · 달인 다색 재접속 시작 버튼 인식

249번과 250번은 겅호 로고에서 누름·뗌 명령을 한 번 보냈습니다.
두 진단의 마지막 화면은 게임 제목과 노란 시작 버튼이 표시된 상태이며,
로고 뒤 시작 버튼을 누른 기록은 없습니다. 로고가 처음 표시된 시각은
기존 로그에 없어 표시 후 실제 클릭까지의 시간은 확인할 수 없습니다.

250번은 기존 시작 버튼 판독 범위에서 장식까지 포함되어 ‘[시작’으로
읽혔습니다. 249번의 마지막 이미지는 기존 코드도 단독 판독에 성공해
당시 연속 확인이 실패한 이유는 확정할 수 없습니다.

시작 버튼의 글자 부분만 좁게 잘라 기존 두 크기로 판독합니다.
겅호 로고와 시작 버튼 모두 새 화면 두 장을 확인하는 흐름과 250ms
폴링 간격을 유지합니다. 재접속 진단에는 게임 실행 요청을 마친 뒤
로고와 시작을 처음 인식한 시각 및 각 클릭 요청 시각을 추가합니다.

Codex에서 249번과 250번의 실제 화면을 읽어 시작 버튼을 확인했습니다.
기존 243번의 시작 버튼, 245번의 크리처 영상 및 246번의 검은 화면도
판독했습니다. Windows와 MuMu 실제 재접속은 미검증입니다.

Codex 소스 검사 63개와 배포 코드 검사 60개를 통과했습니다.

Publication verified: PR48 merged as 403f9ef4ffeee240623525ade57d89f0e06cc62d.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.137 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.137 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates346.
The unchanged launcher installed346 from the live manifest over v1.0.136 in an
isolated installation at state345, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.

Codex의 격리 설치에서 v1.0.136이 v1.0.137로 자동 업데이트됐으며, 기존 장부와 설정을 유지한 채 메인 창이 열렸습니다.
