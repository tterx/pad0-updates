"""Publish only the checked233 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks233.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_25.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.25'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=233,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.25'),None)
notes='수동 경로 계산에서도 현재 TPA 파티의 정확한 4매치 개수를 최우선으로 찾습니다. 그 수를 유지하면서 가능한 경우에만 6콤보를 추가하고, 6콤보가 안 돼도 4매치 경로는 실행합니다.\n\n상태창에는 실제 경로로 확인된 1TPA·2TPA·3TPA·4TPA를 표시하고, 하나도 찾지 못하면 속성부족을 표시합니다. 단순히 드롭 수가 충분한 경우는 경로 계산 대기로 구분합니다. 같은 판의 실시간 감지가 TPA 결과를 5속성 문구로 덮어쓰지 않도록 했습니다. 선택된 모드 번호와 관계없이 TPA 상태창을 표시합니다.\n\nWindows321개 검사 통과, Linux358개 중357개 통과 및 Windows 전용1개 제외. 실제 수동 worker·결과 handler·실행 검사와 Tk 상태 표시, ZIP·업데이트·백업·기존 설정 보존을 검사했습니다. 실제 MuMu 조작은 미검증이며 가산 아이콘 모델은 변경하지 않았습니다. REVIEW233.md·release_checks233.json에 범위를 기록했습니다.\n\nPAD0를 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.25',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.25 · 수동 TPA 우선·상태 표시',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.25 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
