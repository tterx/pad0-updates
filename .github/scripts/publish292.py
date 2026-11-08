"""Publish only the checked292 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.84/release_checks292.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.84/pad0_overlay_windows_v1_0_84.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.84'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=292,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.84'),None)
notes='이름이 미확정된 대상은 파밍 완료창에 이름 등록 필요와 입력란을 표시합니다. 입력 중 창이 자동으로 닫히지 않으며, 이름 등록 또는 Enter로 해당 번호에 저장합니다. 닫으면 번호 등록을 유지합니다. 수량·집계·원래 OCR 기록은 보존하며 오래된 완료창의 덮어쓰기는 차단합니다. Windows940개·Linux977개 중976개·배포본425개 검사 통과. 종료 후 시작.vbs로 다시 실행하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.84',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.84 · 파밍 완료 후 이름 등록',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.84 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
