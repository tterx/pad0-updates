"""Publish only the checked266 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.58/release_checks266.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.58/pad0_overlay_windows_v1_0_58.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.58'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=266,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.58'),None)
notes='잠입 화면의 재료 파밍 옆에 자동반복을 추가했습니다. 빈칸 확인은1회, 양의 정수를 입력하면 그 횟수만큼 같은 던전을 돌고 종료합니다. 재료 등록 없이 사용할 수 있고 F10으로 중지합니다. 보상·연전 버튼 확인 후 횟수를 세며 마지막에는 그만두기를 누릅니다. 콤보 미확정을 실제 숫자 변경과 구분하고 실행 전 한 번 재확인합니다. 알트탭 복귀 시 준비된 경로 표시를 유지하고 작업 시작 대기로 탐색이 일찍 종료되는 경우를 보완했습니다. 실패 저장에는 실제 판독값과 탐색 상태를 남깁니다. Windows652개, Linux689개 중688개(Windows 전용1개 제외), 배포본137개와 업데이트 검사 통과. 원본138·139는 당시 시간·콤보가 없어 실패 원인은 미확정이며 실제 MuMu 입력 검증은 포함하지 않습니다. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.58',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.58 · 자동반복과 경로 준비 보완',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.58 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
