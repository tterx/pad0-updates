"""Publish only the checked250 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.42/release_checks250.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.42/pad0_overlay_windows_v1_0_42.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.42'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=250,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.42'),None)
notes='빛 가시 드롭을 집은 뒤 첫 이동 전에 멈추던 문제를 수정했습니다. 원래 무늬·가시·반투명 배경 합성과 나머지 판 안정으로 잡힌 상태를 확인하며, 기존 어둠 가시 판독과 일반 판정 기준은 유지합니다. 입력 방식과 이동 속도는 바꾸지 않습니다. 수동은 조건을 만족한 TPA·콤보 경로를 찾으면 추가 개선을 기다리지 않고 반환합니다. 달인 다속도 선택적 회복 때문에 5속성6콤보 경로 표시를 지연하지 않습니다. 명시적 회복 조건·적 콤보·이동 상한·F10 검사는 유지합니다. 첨부124 원본과 전체 SDK 경로 재현, Windows467개·Linux504개 중503개 검사(Windows 전용1개 제외), 번들14개 회귀·업데이트 검사 통과. 특정 수동 TPA 재현판은 약3초에서0.06초로 단축됐으며 모든 판의 시간 보장은 아닙니다. 실제 MuMu 플레이는 미검증입니다. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.42',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.42 · 빛 가시 드롭과 빠른 수동 경로',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.42 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
