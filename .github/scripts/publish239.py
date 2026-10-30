"""Publish only the checked239 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.31/release_checks239.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.31/pad0_overlay_windows_v1_0_31.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.31'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=239,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.31'),None)
notes='TPA+6콤보 동시 탐색을 배열 계산으로 빠르게 처리하고,1.2초마다 처음부터 다시 찾던 탐색 반복을 제거했습니다. 첫 판은 전사한 데이터에서 약0.1초에 조건 충족 경로를 찾았습니다(캡처·OCR·작업 시작 시간 제외). 판 색 배치가 같다면 가산·시간 값이 다시 읽혀도 현재 조건으로 저장 경로를 재검증해 실행합니다. 실제 조건 미달·새 위험·색 변경은 계속 차단합니다. 첫 TPA 사전 탐색에서 체력 OCR을 미루고 실행 전에 확인합니다. 플레이 후 직접 고른 경로 조건이 TPA로 덮어써지던 문제도 수정했습니다. Windows365개 검사, Linux402개 중401개 통과·Windows 전용1개 제외. 원본 inline 픽셀 인식과 MuMu 실기는 미검증입니다. PAD0 종료 후 시작.vbs를 다시 실행하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.31',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.31 · TPA 동시탐색·경로 실행 수정',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.31 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
