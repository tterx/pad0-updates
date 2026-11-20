# v1.0.131 · 달인다속 연전확인과 MuMu 연결 복구

239번의 실제 중단 화면은 11-2 연전확인입니다. 실제 화면에서 11-2와
OK 버튼을 확인했습니다. 오류는 현재 보상 집계 미완료이며, 같은 파일의
receipt.png는 앞선 판에서 이미 처리한 크리스탈 결과입니다. 이번 판의
연전 직전 보상 원본은 첨부에 없습니다.

달인다속에서는 현재 전투의 흰색 마나 결과에서 ‘다음’ 입력이 성공했고,
크리스탈 목록이 관측되지 않았으며, 동일 스테이지의 연전확인 화면과
OK 버튼이 연속 캡처에서 확인되면 크리스탈 0개를 기록한 뒤 재도전합니다.
이번 판의 마나 결과와 이전에 집계한 크리스탈 결과는 별도 이미지로
저장합니다. 인식된 크리스탈 목록은 기존 수량 집계 절차를 거칩니다.

238번은 전투 중 Android의 실행 앱을 확인하지 못했습니다. 기존 복구가
패키지 서비스 부재를 확인한 뒤 ADB 응답 시간 초과로 끝났습니다. 종료
직후 선택한 기기도 offline으로 보고됐습니다. 복구 중 조회 시간 초과는
취소 가능한 기존 45초 범위에서 다시 확인합니다. 오프라인이면 선택된
로컬 MuMu 주소만 다시 연결합니다. 앱이 살아 있으면 화면을 새로 읽고,
홈이 확인되면 기존 게임 재시작 절차를 사용합니다. 경로 입력 전 조회가
실패한 경우 새 판을 확인해 재계산합니다. 입력 명령을 재전송하지 않습니다.

실제 239번 연전확인과 이전 보상 화면은 Codex의 OCR과 픽셀 검사에
사용했습니다. 마나 결과에서 연전으로 이어지는 흐름과 238번 연결 복구는
모의 화면과 연결로 검증했습니다. 실제 MuMu 연전과 재접속은 미검증입니다.

Codex 소스 검사 156개와 배포 코드 검사 41개를 통과했습니다.

Publication verified: PR42 merged as 017b4d7a7056f91a6a5023d3c2c883c5e76ee2e2.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.131 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.131 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates340.
The unchanged launcher installed340 from the live manifest over v1.0.130 in an
isolated installation at state339, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
