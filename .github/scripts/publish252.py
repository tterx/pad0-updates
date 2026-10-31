"""Publish only the checked252 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.44/release_checks252.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.44/pad0_overlay_windows_v1_0_44.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.44'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=252,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.44'),None)
notes='재료확인에서 4번째 보유 수량 ×46이 여러 숫자 줄로 인식되어 전체 수량 갱신이 보류되는 문제를 수정했습니다. ×와 숫자 높이가 달라 부분 4와 전체46이 동시에 후보가 되는 오류를 재현했습니다. 같은 곱하기 기호·같은 색상에서 완전한 숫자 줄에 포함되는 부분 후보만 통합합니다. 독립된 숫자 줄·서로 다른 ×·잘린 숫자·OCR 불일치는 계속 보류합니다. 생성한 재현 화면에서 실제 한국어 OCR과 F7 전체 작업을 통해 6/0,6/0,6/0,15/46 저장을 검증했습니다. Windows483개, Linux520개 중519개(Windows 전용1개 제외), 번들16개·업데이트 검사 통과. 첨부 원본 픽셀과 실제 MuMu 플레이는 미검증입니다. PAD0 종료 후 시작.vbs로 업데이트하고 재료창에서 재료확인/F7을 누르세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.44',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.44 · 재료 수량 줄 중복 판독 수정',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.44 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
