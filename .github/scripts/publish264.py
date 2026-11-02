"""Publish only the checked264 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.56/release_checks264.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.56/pad0_overlay_windows_v1_0_56.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.56'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=264,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.56'),None)
notes='137번은8초와 적 콤보를 정상 판독한 뒤 추가 최신 판 확인에서 결과를 버린 사례였습니다. 경로 준비에 있던 추가 캡처와 만료 판정을 제거하고, 현재 작업·판·설정에 속한 정상 판독값으로 계산을 바로 시작합니다. 원본 시각과12초 한도를 유지하며 오래된 작업·변경된 판/조건은 거부합니다. 실제 이동 전의 현재 판·시간·적 콤보·가시 검증과 별도 실행 버튼은 유지합니다. 137 원본의 실제 한국어 OCR과 기존 결과 폐기 재현, 수정 후 실제5속성·6콤보 경로 준비까지 확인했습니다. 원래 실행 순서는 통제된 시간으로 재현했으며 실제 MuMu 입력은 미재현입니다. Windows609개, Linux646개 중645개(Windows 전용1개 제외), 번들94개 및 업데이트 검사 통과. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.56',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.56 · 정상 조건으로 경로 준비',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.56 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
