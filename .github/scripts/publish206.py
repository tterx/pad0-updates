"""Publish only the checked206 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v101.206/release_checks206.json').read_text())
artifact=Path('releases/v101.206/pad0_overlay_windows_v101_206.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=206,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v101.206'),None)
notes='''자동 중지/F10 후 수동 플레이 모드를 입력 실행으로 오인해 파밍 연결 설정을 열지 못하던 문제를 수정했습니다. 이미 열린 창은 앞으로 복원하고, 닫힌 창은 다시 열 수 있습니다. 설정에서 MuMu SDK를 선택해 저장할 수 있습니다.

실제 파밍/경로 실행 중 연결 변경 차단은 유지하며, 창을 여는 것으로 F10 중지 상태를 해제하거나 자동 입력을 재개하지 않습니다. SDK 엔진/속도/ADB는 변경하지 않았습니다.

Linux 핵심/관련357개, Windows45개와 패키징 검사 통과. 실제 Tk 창에서 설정 열기/SDK 저장/재열기를 확인했습니다. 실제 MuMu 게임 조작 검사는 포함하지 않습니다.

기존 설치는 PAD0를 완전히 종료하고 시작.vbs로 실행하세요. 새 설치는 첨부 ZIP을 사용하세요. 변경과 검사 결과는 REVIEW206.md 및 release_checks206.json에 있습니다.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v101.206',target_commitish=os.environ['GITHUB_SHA'],
        name='v101.206 자동 종료 후 파밍 연결 설정 복구',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v101.206 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
