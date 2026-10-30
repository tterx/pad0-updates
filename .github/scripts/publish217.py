"""Publish only the checked217 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.9/release_checks217.json').read_text())
artifact=Path('releases/v1.0.9/pad0_overlay_windows_v1_0_9.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=217,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.9'),None)
notes='''목표 재료를 다 모았을 때 그만두기 입력 완료 직후 파밍 완료 팝업과 작은 완료음을 표시합니다. 복귀 화면 확인 때문에10초 뒤 오류로 중단하던091 경로를 제거했습니다.

팝업에는 선택한 재료별 총수량(현재 보유)과 이번 자동파밍에서 획득한 마나를 표시합니다. 마지막 보상 한 번의 수량이나 계정 전체 마나 잔액을 사용하지 않습니다. 마나 미확인 페이지가 있으면 함께 표시합니다.

정리 작업을 기다리지 않고 먼저 알리며, 이후 완료 처리에서 팝업과 소리가 중복되지 않습니다. 긴 이름의5개 재료에서도 닫기 버튼이 보이도록 높이를 맞춥니다. 기존 알림/효과음 설정은 유지합니다.

SDK·저장된 속도·필드 파괴 우선/차선책·재고 집계·전투 검증·F10은 변경하지 않았습니다. 버튼 인식이나 실제 입력/손 떼기 응답이 실패하면 완료로 처리하지 않습니다. 게임의 복귀 화면 자체를 확인했다는 의미는 아닙니다.

Linux360개·Windows131개 및 패키징 검사 통과. 실제091 버튼 이미지와 스크립트 장치로 복귀 화면이 그대로여도 완료하는 흐름, 실제Tk 팝업, 수량/마나, 알림 중복 방지를 확인했습니다. 사용자MuMu 조작/실제 PC 오디오 출력 검사는 포함하지 않습니다. REVIEW217.md와 release_checks217.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.9',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.9 · 파밍 완료와 수량·마나 알림',body=notes,draft=True,prerelease=False))
asset=next((a for a in release['assets'] if a['name']==artifact.name),None)
if asset is None:
    assert release['draft'],'Never change an already published release'
    asset=api(f'https://uploads.github.com/repos/{repo}/releases/{release["id"]}/assets?name={artifact.name}',
        'POST',artifact.read_bytes(),binary=True)
assert asset['size']==artifact.stat().st_size
assert asset['digest']=='sha256:'+meta['zip_sha256'],'GitHub uploaded byte digest must match checked ZIP'
if release['draft']:
    release=api(f'/repos/{repo}/releases/{release["id"]}','PATCH',dict(draft=False,prerelease=False,make_latest='true',body=notes))
assert not release['draft']
print('Published:',release['html_url'])
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
    summary.write(f'v1.0.9 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
