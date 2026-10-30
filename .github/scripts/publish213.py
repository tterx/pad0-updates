"""Publish only the checked213 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.5/release_checks213.json').read_text())
artifact=Path('releases/v1.0.5/pad0_overlay_windows_v1_0_5.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=213,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.5'),None)
notes='''087에서 바인드 해제가 대기 중이면 준비된 관통·잠금 해제까지 막혀 중지되던 문제를 수정했습니다.

최우선 대응이나 현재 쿨타임으로 유효한 단축이 없으면 다음으로 유용한 대응을 검토합니다. 사용할 스킬이 없으면 현재 판의 가능한 회복·콤보로 턴을 진행합니다. 실제 바인드 상태는 유지해 인식하고, 다음 결정에서 해제가 준비되면 다시 최우선으로 선택합니다. 바인드된 캐릭터·사용한 스킬·자동사용 해제·미확인 스킬은 계속 제외합니다.

스킬 발동·판·시간·실제 이동·손가락 해제 검증과F10은 유지합니다. SDK·기존 컨트롤러·속도·방해/물 모델·연전 정책은 변경하지 않았습니다.

Linux368개·Windows104개 및 패키징 검사 통과. 실제087 화면으로 선택을 재현하고 스크립트 장치로 대응이 전혀 없을 때의 실제 경로 탐색/턴 진행을 검사했습니다. 사용자MuMu 직접 조작은 포함하지 않습니다. REVIEW213.md와 release_checks213.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.5',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.5 · 사용 불가 스킬의 차선책',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.5 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
