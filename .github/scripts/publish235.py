"""Publish only the checked235 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.27/release_checks235.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.27/pad0_overlay_windows_v1_0_27.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.27'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=235,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.27'),None)
notes='지우기&콤보의 TPA 경로를 계산 버튼 없이 미리 탐색합니다. 정상9초 판에서도 시작하고, 준비되면 1TPA~4TPA와 경로 실행을 표시합니다. 실행 버튼을 누르기 전에는 게임 입력을 하지 않으며, 클릭 시 판·시간·가산·가시 위험을 다시 확인해 준비된 경로만 실행합니다. TPA가 우선이고6콤보는 가능한 경우 추가합니다.\n\n수동 경로 이동을 칸당75ms 목표의 연속 이동으로 조정했습니다. 경로 길이는90ms/칸 예산으로 제한하며, 실제 속도는 OS와 입력 지연에 좌우됩니다. 자동 파밍 속도 설정은 유지합니다.\n\nWindows341개 검사 통과. Linux378개 중377개 통과, Windows 전용1개 제외. 실제 별도 프로세스 미리 탐색, 명시적 클릭 전 입력 없음, 캐시 재사용과 무효화, 가짜 시계에서 이동 간격을 확인했습니다. 실기 MuMu 조작은 미검증입니다. REVIEW235.md·release_checks235.json에 근거를 기록했습니다. PAD0를 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.27',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.27 · TPA 미리 탐색·수동 이동 속도',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.27 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
