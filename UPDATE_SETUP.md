# Windows 실행 및 자동 업데이트

## 지금 실행하기

프로그램을 종료하고 새 실행 패키지를 별도 폴더에 압축 해제한 뒤 `시작.vbs`를 실행합니다.
이 최초 교체는 수동으로 해야 합니다. 예전 v101의 `시작.vbs`와 `run.bat`에는 업데이트 기능이 없습니다.
개인 설정과 DB는 기존처럼 LocalAppData/Pad0Overlay에서 읽습니다. 실행 패키지에 개인 데이터를 넣지 마세요.

실행 순서는 `시작.vbs` → `run.bat` → `pad0_launcher.py` → `pad0_overlay.py`입니다.
현재 수정본은 패키지에 포함되어 있어 배포 주소 없이 실행할 수 있습니다.
Windows 실기기에서 실행과 업데이트는 아직 검증하지 않았습니다.

## 배포 주소 연결하기

동봉된 `update_config.json`에는 다음 업데이트 주소가 설정되어 있습니다.

`https://raw.githubusercontent.com/tterx/pad0-updates/main/update.json`

주소를 비우면 인터넷 확인을 생략하고 설치된 프로그램을 실행합니다.

HTTPS로 접근 가능한 배포 서버에 아래 두 파일을 올립니다.

1. 최신 프로그램 소스 `pad0_overlay.py`.
2. 아래 형식의 `update.json`.

```json
{
  "release": 2,
  "source_url": "https://YOUR-HOST/releases/2/pad0_overlay.py",
  "sha256": "배포한 pad0_overlay.py의 소문자 SHA-256 값"
}
```

`release`는 매 배포마다 증가시키는 정수입니다. 동봉된 v101.27 패키지는 release 27입니다.
SHA-256 값은 Windows PowerShell에서 다음과 같이 구합니다.

```powershell
(Get-FileHash .\pad0_overlay.py -Algorithm SHA256).Hash.ToLower()
```

설치 폴더의 `update_config.json`에 실제 JSON 주소를 입력합니다.

```json
{"manifest_url": "https://YOUR-HOST/update.json"}
```

예시 주소를 그대로 사용하지 마세요. 비공개 GitHub 저장소 주소는 일반 Windows PC에서 인증 없이 받을 수 없습니다.
공개 GitHub Releases 등으로 배포하면 다운로드되는 Python 소스도 공개됩니다.
비공개 배포를 원한다면 접근 권한과 인증 방식을 먼저 정해야 합니다. 현재 실행기는 별도 인증 기능이 없습니다.
프로그램은 설정한 서버의 코드를 실행하므로 본인이 관리하거나 신뢰하는 배포 주소를 사용합니다.

## 교체 및 복구 동작

- 시작 시 최신 manifest를 확인하고 더 높은 release일 때만 소스를 받습니다.
- HTTPS 인증서 검증, SHA-256 검사, Python 문법 검사를 통과한 파일만 교체합니다.
- 기존 소스는 `pad0_overlay.py.bak`에 보관하고 임시 파일을 이용해 교체합니다.
- 다운로드 또는 검증 실패 시 기존 프로그램을 실행합니다. 내용은 `startup.log`에서 확인합니다.
- 네트워크 확인 시간이 실행 대기에 추가될 수 있습니다. 네트워크 요청의 소켓 타임아웃은 5초입니다.
- 실행기를 통해 프로그램이 실행 중이면 다른 실행기의 업데이트와 중복 실행을 막습니다.
  구버전 실행기나 Python으로 직접 실행한 프로세스까지 감지하지는 못하므로 최초 교체 전에 기존 프로그램을 종료합니다.
- 설정, DB, 기록 파일은 다운로드하거나 덮어쓰지 않습니다.
- 현재 자동 업데이트 대상은 `pad0_overlay.py` 하나입니다. 실행기나 requirements가 바뀌면 전체 패키지를 다시 설치합니다.
- 문법 검사는 실제 앱의 정상 동작을 보장하지 않습니다. 실행 후 오류까지 자동 롤백하는 기능은 없습니다.

복구가 필요하면 프로그램을 종료하고 `.bak` 파일을 복사해 `pad0_overlay.py`로 되돌린 뒤,
`manifest_url`을 비워서 업데이트를 중단합니다. 이때 개인 DB를 삭제하지 않습니다.

## 다운로드 파일의 Windows 보안경고

인터넷에서 받은 ZIP의 출처 표시 때문에 실행 확인이 뜨는 경우, 신뢰하는 배포 파일인지 확인한 뒤
ZIP을 우클릭 → 속성 → **차단 해제** → 적용하고 새 폴더에 다시 압축 해제합니다.
압축을 푼 파일보다 원본 ZIP에 먼저 적용합니다. ‘차단 해제’가 없으면 이 원인으로 단정할 수 없습니다.
SmartScreen의 ‘Windows의 PC 보호’, 백신 탐지 또는 관리자 권한 확인은 별개입니다.
현재 배포에는 코드 서명이 없으므로 모든 경고가 없어지는 것은 아닙니다.
실행기가 보안 기능을 끄거나 다운로드 파일의 출처 표시를 자동으로 제거하지는 않습니다.
