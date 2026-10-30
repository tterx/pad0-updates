"""Publish only the checked207 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v101.207/release_checks207.json').read_text())
artifact=Path('releases/v101.207/pad0_overlay_windows_v101_207.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=207,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v101.207'),None)
notes='''부족한 드롭을 보완할 준비된 아텐 등 판 변환 스킬을 먼저 확인합니다. 잠금 해제 대기가 준비된 아텐까지 가리던 문제를 수정했습니다. 준비된 우선 대응/단축 스킬과 바인드 해제 우선순위는 유지합니다.

보완할 수 없는 수량 조건이면 현재 판에서 가능한 회복·콤보 경로로 진행합니다. 저체력에서 가능한 회복을 우선하고, 없는 회복 드롭을 요구해 중단하지 않습니다. 요청 조건과 실제 회복 포함 여부는 별도로 기록합니다. 인식/시간/스킬 효과/이동 검증과 F10 안전 중지는 유지합니다.

Linux 핵심/관련360개, Windows65개와 패키징 검사 통과. 081 판의 코드 재현에서5칸 이동, 4직접 콤보+가산2=6콤보를 확인했습니다. 실제 MuMu 터치 검사는 포함하지 않습니다.

PAD0를 완전히 종료하고 시작.vbs로 다시 실행하면 자동 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요. SDK/MaaTouch와 저장된 이동 속도는 유지합니다.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v101.207',target_commitish=os.environ['GITHUB_SHA'],
        name='v101.207 준비된 아텐 검토·부족한 판의 가능한 매칭',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v101.207 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
