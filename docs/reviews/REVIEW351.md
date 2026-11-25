# v1.0.142 · 달인 다색 일반 연전과 분기 선택

일반 스테이지에서는 선택한 재료가 한 개든 여러 개든 현재 목표가
충족될 때까지 연전합니다. 현재 목표가 충족되면 다음 부족 재료로
넘어가고, 선택한 목표를 모두 채우면 완료합니다. 보상 10회마다
재료 확인창으로 돌아가는 경로를 제거했습니다.

10회 기준은 확인된 분기 선택지에 적용합니다. 같은 선택지의 집계
결과에서 목표 크리스탈이 10회 연속 나오지 않으면 같은 스테이지의
다음 갈림길에서 다른 선택지를 고릅니다. 획득이 확인된 선택지는
스테이지와 크리스탈별로 저장해 이후 파밍에 사용합니다.

Codex에서 한 재료와 여러 재료의 일반 연전을 각각 11회 진행해
목표에서 종료하는 것을 확인했습니다. 여러 재료를 선택한 분기
테스트에서도 10회 미획득 뒤 11번째 갈림길에서 선택지만 바뀌고
새로 입장하는 흐름이 시작되지 않았습니다. 실제 Windows와 MuMu
자동 파밍은 미검증입니다.

Codex 소스 검사 80개와 배포 코드 검사 77개를 통과했습니다.

Publication verified: PR53 merged as 4fce9a1715b0f0ba97330e1951c093240690d670.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.142 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.142 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates351.
The unchanged launcher installed351 from the live manifest over v1.0.141 in an
isolated installation at state350, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.

Codex의 격리 설치에서 v1.0.141이 v1.0.142로 자동 업데이트됐습니다. 기존 장부와 설정을 유지한 채 메인 창이 열렸습니다.
