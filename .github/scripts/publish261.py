"""Publish only the checked261 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.53/release_checks261.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.53/pad0_overlay_windows_v1_0_53.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.53'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=261,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.53'),None)
notes='135번은 v1.0.52에서도 정상 판독한8초·콤보 결과가3.141초 뒤 만료되어 버려진 사례였습니다. 느린 조건 판독 후 OCR을 다시 반복하지 않고 현재 판·가시·바인드를 새 화면에서 검증해 경로 준비로 전달합니다. 판독 중4초만 지나도 미확인으로 처리하던 대기는12초로 조정하고 실제 미확인 결과의 제한된 재시도는 유지합니다. 실제 이동은 별도 실행 버튼과 최신 조건 재검증을 거칩니다. Windows579개, Linux616개 중615개(Windows 전용1개 제외), 번들64개와135 원본 판독·작업자 대기열 흐름·업데이트 검사를 통과했습니다. 실제9.687초의 실행 순서는 통제된 시간으로 재현했으며 MuMu 입력은 수행하지 않았습니다. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.53',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.53 · 느린 조건 결과 전달',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.53 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
