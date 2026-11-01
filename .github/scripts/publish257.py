"""Publish only the checked257 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.49/release_checks257.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.49/pad0_overlay_windows_v1_0_49.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.49'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=257,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.49'),None)
notes='완료 알림음을 더 크고 길게(2.2초) 조정하고 수동 경로 창의 5속성 표시를 한 줄로 정리했습니다. 기본 수동 탐색은 5속성·6콤보 우선이며 짧은 시간 검증에서 확정 낙하 콤보가 빠져 6콤보 경로로 바뀌던 문제를 수정했습니다. 회복 포함 재계산은 준비된 공격 경로를 지우지 않고 새 경로를 찾으며 자동 입력하지 않습니다. 위쪽 버튼은 준비된 경로를 현재 조건으로 재검증해 실행합니다. 최초 조건 확인 실패는 한 번 더 자동 확인하고 클릭한 실행을 준비 완료 후 이어가며, 계속 불명확하면 보류합니다. 실패 판독 기록도 다음 진단에 보존합니다. 128·129 원본은 재확인 후 성공 기록이므로 최초 실패 원인은 확정할 수 없습니다. Windows530개, Linux567개 중566개(Windows 전용1개 제외), 번들17개·실제 Tk 표시·원본 OCR 및 업데이트 검사 통과. 실제 MuMu와 스피커 체감 음량은 미검증입니다. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.49',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.49 · 수동 5속성 우선과 회복 재계산',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.49 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
