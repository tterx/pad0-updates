"""Publish only the checked210 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks210.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_2.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=210,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.2'),None)
notes='''084 오류의 연전 스테이지 번호 재판독 실패를 수정했습니다. 보상 집계 후 연전 화면에서 번호를 다시 OCR하지 않고 최초 진입의 스테이지 정보를 유지합니다.

선택한 재료가 부족하면 확인된 OK로 연전을 계속하고, 모두 모으면 그만두기 후 이전 화면 전환을 확인하고 완료 소리/알림을 보냅니다. 집계 미완료/불확실한 버튼/전환 실패/F10 안전 중단은 유지합니다. SDK·기존 컨트롤러와 저장된 매칭 속도는 변경하지 않습니다.

Linux357개·Windows82개 및 패키징 검사 통과. 첨부 실제 버튼 영역과 스크립트 OCR/장치로 연전 번호 판독이 실패하는 조건에서도 계속/종료 흐름을 검사했습니다. 사용자 MuMu 직접 조작은 포함하지 않습니다. 근거는 REVIEW210.md 및 release_checks210.json에 있습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.2',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.2 · 연전 스테이지 재판독 제거',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.2 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
