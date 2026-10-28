"""Publish only the checked232 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks232.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_24.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.24'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=232,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.24'),None)
notes='팀 이름을 지우기&콤보로 수정했습니다. TPA 보유 파티는 해당 캐릭터의 주·부속성 4매치를 최우선으로 찾고, 가능한 TPA 수를 늘립니다. TPA 수를 줄이지 않는 범위에서 6콤보를 추가하며, 6콤보가 안 돼도 TPA가 있으면 실행합니다.\n\n택틱에 드롭 생성을 추가했습니다. 현재 파티의 No368·247 생성 스킬과, 두 스킬이 쿨타임일 때 관측된 쿨타임을 단축하는 No119·120 스킬을 연결했습니다. 스킬을 쓸 수 없고 TPA도 없으면 판 정리로 쿨타임을 진행합니다.\n\n실제로 지운 가산 드롭은 1개당+1, 2개는+2를 스킬 가산과 별도로 더합니다. 가산 표시는 드롭과 함께 이동합니다. 보조 인식 모델은 개발환경에서 렌더링한 형태로 학습한 고정 모델입니다. 학습2400개·별도800개 검증에서 양성 재현88%, 음성400개 오탐0이며 이는 실제 게임 정확도가 아닙니다. 원본 게임 양성 이미지 검증과 실제 MuMu 조작은 미검증입니다. 사용자 환경의 학습·자료 저장은 하지 않습니다.\n\nWindows315개 검사 통과. Linux352개 중351개 통과, Windows 전용1개 제외. REVIEW232.md·COMBO232.json·release_checks232.json에 범위를 기록했습니다. PAD0를 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.24',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.24 · TPA 우선·드롭 생성 택틱',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.24 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
