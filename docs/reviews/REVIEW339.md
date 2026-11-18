# v1.0.130 · 달인다속 집계와 수동 반응 속도

236·237번은 같은 결과창 중단을 기록합니다. 보유 한도 경고 카드 한 장을
제외했고 아래로 스크롤했습니다. 8·9번째 캡처는 처음 위치보다 127픽셀
아래였고, 10번째는 128픽셀이었습니다. 3회 연속 정지 확인이 초기화됐는데
이때 10회 제한도 끝났습니다. 마지막 화면의 스크롤바는 목록 중간입니다.

달인다속 자동 파밍에서 보유 한도 안내가 확인된 결과창은 스크롤마다 최대
2.5초 동안 최대 40회 정지 여부를 확인합니다. 정지 판정은 여전히 연속
3회입니다. 정지하면 실제 목록 끝까지 보상을 확인합니다. F10으로 대기를
중지할 수 있습니다. 수량 합산과 경고 카드 제외 기준을 유지합니다.

달인다속 수동 전환은 F10 이후 판독을 예약하고, 정지된 판의 확인 간격을
650ms에서 300ms로 줄였습니다. 새 판의 첫 번째 판독이 들어오면 이전 속성
수량 표시를 지우고 두 번째 판독을 앞당깁니다. 새 판은 독립 캡처 두 장으로
확인합니다. 이미 확인한 격자를 같은 프레임에서 재검사하는 작업을 줄였습니다.

계산 뒤 준비된 경로를 바로 실행 대기열에 넣습니다. 창이 이미 최소화됐다면
이동 전 창 최소화 대기도 생략합니다. 이동 직전에는 새 게임 화면을 캡처해
판, 가시, 시간, 적 조건을 확인합니다. 이전 판독과 이미지 전체가 일치하는
경우에만 드롭 분류를 재사용합니다. 조건 확인 원본을 이동 진단에도 저장합니다.

검증은 Codex에서 실행했습니다. 236·237번 실제 화면은 128픽셀 이동과
목록 중간 위치를 확인하는 데 사용했습니다. 이후 정지는 해당 기록 뒤에
정지 캡처를 추가한 모의 입력으로 검증했습니다. 실제 Windows/MuMu 조작에서
보상 수령 완료와 수동 이동 체감 시간은 확인하지 못했습니다.

Codex 소스 검사 186개와 배포 코드 검사 84개를 통과했습니다. 같은 저장 판 이미지에서 실행 직전 판독 중앙값은 411.9ms에서 135.2ms로 줄었습니다. 이 측정에는 캡처 입출력과 실제 마우스 이동 시간이 포함되지 않습니다.

Publication verified: PR41 merged as 2acf42ee23d55703da6d5b70efecb51b1598d31e.
Its tree matches the locally checked head. No source, build or publish Actions ran.
The actual v1.0.130 ZIP downloaded from the immutable repository URL passed SHA256,
ZIP CRC, version and updater-state checks; release v1.0.130 is stable latest.
The release notes and root README point to that working repository ZIP URL;
no attached release asset is claimed.
The source matches the immutable payload and root update.json activates339.
The unchanged launcher installed339 from the live manifest over v1.0.129 in an
isolated installation at state338, preserving the backup, settings, inventory and
four separate normal/starred/hidden stage/branch markers, and skipped a repeat.
No PAD0 or physical game input was executed during updater verification.
