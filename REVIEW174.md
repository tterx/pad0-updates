# v101.174 MAA 캡처·입력 연결

사용자가 ADB 전용 제한을 해제했습니다. MAA 게임 자동화 코드를 복사하는 대신 공식 MaaFramework 5.14.3의 공개 API와 MaaAgentBinary 1.0.1을 연결했습니다. 기존 scrcpy 3.1 제어 서버와 Android 경로 실행기도 선택할 수 있습니다.

## 제공하는 연결

- 캡처: MuMu 강화, MAA 자동 무손실(EmulatorExtras/RawByNetcat/RawWithGzip/Encode), 네트워크 RAW, RAW 압축, minicap Direct/Stream, 기존 ADB RAW/PNG.
- 입력: MAA 자동(MuMu SDK → MaaTouch → minitouch), 방식별 지정, scrcpy 제어 소켓, Android 경로 일괄 입력. 자동 연결 실패 시에만 scrcpy → Android를 확인합니다. 지정 방식 실패는 다른 방식으로 바꾸지 않습니다.
- 기본 캡처는 MuMu 강화 우선이며 불가하면 MAA 무손실을 확인합니다. MAA 입력을 준비하면 시작/놓기 확인을 포함해 해당 MAA 컨트롤러의 캡처로 통일합니다. 재사용하므로 스테이지마다 서버를 다시 실행하지 않습니다.
- minicap Direct/Stream은 JPEG입니다. 무손실 자동 목록에 포함하지 않고 별도 선택으로 제공합니다. scrcpy는 이번 변경에서 입력 제어용이며 영상 스트림 디코더를 추가한 것은 아닙니다.

## 설정과 준비

[파밍 연결 설정]의 화면 캡처/클릭·경로 입력에서 선택합니다. 기존 ADB 전용 설정은 새 자동 선택으로 이행하며 새 선택은 이후 유지합니다. MuMu 폴더와 기기 번호는 계속 사용할 수 있습니다. Google ADB 사용 시 기존 MuMu 설치 경로도 확인합니다.

처음 MAA가 필요할 때 PAD0 사용자 폴더의 tools/maa-5.14.3에 MaaFw==5.14.3, MaaAgentBinary==1.0.1, StrEnum==0.4.15를 설치합니다. 기존 Python의 numpy 등이나 다른 프로그램의 패키지는 교체하지 않습니다. 실행 중 인터넷 연결이 필요할 수 있으며 설치 오류는 연결 사유로 남깁니다. 일반 실행/업데이트 때마다 재설치하지 않습니다. 공식 라이브러리는 별도 설치이며 교체할 수 있습니다. DLL이나 사용자 자료를 업데이트 ZIP에 묶지 않습니다.

[스크린샷 테스트]는 화면만, [캡처·입력 연결 테스트 (누름 없음)]는 입력 채널 준비까지 확인합니다. 둘 다 게임을 누르지 않습니다. 자동 MAA 표시는 후보 집합을 뜻하며 실제 선택의 속도/입력 지연 보증이 아닙니다. 준비 시간을 제외한 1장 캡처 측정값과 실패 사유를 표시합니다.

## 입력 소유권과 판 확인

공식 MaaFramework는 별도 프로세스에서 실행하고 공유 메모리로 화면을 전달합니다. 원본 해상도 사용을 강제하고 첫 ADB 화면과 화면/기기 일치를 확인합니다. 다른 기기·다른 해상도·단색 잘못된 화면은 거부합니다. MuMu 기기 번호를 추정할 수 없으면 임의로 #0에 입력하지 않습니다.

한 경로는 같은 컨트롤러/접점 0으로 DOWN → 시작 드롭 확인 → 모든 경유점 → 놓기 전 화면 → UP을 처리합니다. SDK의 내부 접점 변환은 공식 MAA API가 담당합니다. GUI와 경로 계산이 있는 주 프로세스에서 매 MOVE마다 명령을 왕복하지 않습니다. 중지 이벤트를 공유하며, F10과 실패 정리도 같은 컨트롤러의 UP으로 처리합니다. MAA 연결 준비와 정상 idle 종료에서는 접점 전체 해제를 보내지 않습니다. 명시적 F10의 기존 Windows 마우스 UP 보조 정리는 유지합니다. UP 실패는 성공 이벤트로 기록하지 않습니다.

잘못된 시작점, 제한시간 부족, 캡처 오류가 생기면 MOVE를 차단하고 손을 놓습니다. DOWN 이후 다른 입력 방식으로 재시도하지 않습니다. 최종 판 일치/들린 마지막 드롭/UP 뒤 실제 게임 반응을 확인하는 기존 검사를 유지합니다. 입력 ACK는 게임 성공으로 간주하지 않습니다.

## 검증 범위

공식 Linux MaaFramework 네이티브 라이브러리를 실제 로드하고 CustomController 시험 기기에 연결했습니다. 실제 작업 큐를 통한 원본 해상도·좌표·BGR/RGBA 방향·전체 경유점·단일 DOWN/UP, 취소/잘못된 시작점에서 MOVE 금지, UP 실패 기록, 실제 Pipe/공유 메모리 캡처 전용 채널, 설정창 선택 저장을 검사합니다. 이는 실제 Windows/MuMu 실기 검증을 대신하지 않습니다.

실제 MuMu의 게임 터치, FPS, 지연 및 기존 동시 눌림 재발 해소는 이 개발 환경에서 측정할 수 없습니다. 30~60 FPS 또는 1ms 미만 입력을 보장하지 않습니다.

- 전체 검사 1486개 실행: 1484개 통과, 이전 ADB 전용 실행기를 가정한 두 검사에서 오류(390.382초). 새 자동 연결/명시적 Android 입력으로 시험 대상을 조정했습니다.
- 보완 후 자동 연결 4개, 연결 지연/재사용 5개, MAA 실제 네이티브 API/선택/연결 손실/설정 12개 모두 통과했습니다. 별도 실제 spawn 프로세스에서도 MAA를 불러오고 존재하지 않는 시험 기기를 거부하는 것과 종료를 확인했습니다.
- **최종 배포 내장 코드 242개 통과(54.191초), 건너뜀 0개.** 위의 두 보완 검사, 실제 MAA 네이티브 작업 큐, 실제 Tk 설정, 기존 캡처·입력·파밍·Java 실행기·오류 재현·업데이트 모듈 검사를 포함합니다. 최종 코드에서 전체 1488개를 다시 돌린 결과라고 표기하지 않습니다.

## 출처·라이선스

- [MaaFramework v5.14.3](https://github.com/MaaXYZ/MaaFramework/tree/v5.14.3): LGPL-3.0, 공식 PyPI MaaFw를 변경 없이 동적 로드합니다. [라이선스](https://github.com/MaaXYZ/MaaFramework/blob/v5.14.3/LICENSE.md)
- [MaaAgentBinary](https://github.com/MaaXYZ/MaaAgentBinary): MaaTouch/minitouch/minicap 등 공식 사전 빌드 자원. 각 자원의 출처/라이선스는 해당 저장소와 설치 패키지를 참조합니다.
- [MAA MuMu 구현](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev-v2/src/MaaCore/Controller/MumuExtras.cpp): 캡처·입력 분리, 접점 번호 변환을 비교 검토했습니다. 소스 복사 없음.
- [scrcpy v3.1](https://github.com/Genymobile/scrcpy/tree/v3.1): 기존 내장 제어 서버, Apache-2.0. 기존 서버 무결성 해시와 라이선스 파일을 유지합니다.
