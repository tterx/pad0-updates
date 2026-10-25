"""Publish only the checked208 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks208.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=208,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0'),None)
notes='''표시 버전을 1.0으로 변경하고 자동 업데이트 순번208을 별도로 유지합니다. 기존 v101.207 설치에서도 시작.vbs 재실행으로 업데이트됩니다.

입력은 MuMu SDK, 스킬 클릭은 기존 컨트롤러로 고정하고 선택 메뉴를 제거했습니다. MuMu 강화 캡처와 저장된 매칭 속도/코너 대기 조절을 유지합니다.

연전 확인 버튼은 실제 영역과 글자를 분리해서 확인합니다. 재료가 부족하면 OK로 계속하고, 선택 목표를 모두 채우면 그만두기를 누른 뒤 화면 전환을 확인하고 작은 소리와 완료 알림을 표시합니다. 불확실한 버튼을 중복 클릭하지 않습니다.

Linux 핵심·관련366개, Windows74개와 패키징 검사 통과. 실제 사용자 MuMu 게임 조작은 포함하지 않습니다. 변경과 검증 범위는 REVIEW208.md 및 release_checks208.json에 기록했습니다.

기존 설치는 PAD0를 완전히 종료하고 시작.vbs를 다시 실행하세요. 새 설치는 첨부 ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0 · SDK 고정과 연전 완료 처리',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
