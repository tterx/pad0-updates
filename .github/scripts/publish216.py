"""Publish only the checked216 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks216.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_8.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=216,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.8'),None)
notes='''090은 경로 이동 전에 들린 방해 드롭을 물로 읽어 시작 확인에서 멈춘 사례였습니다.

방해 드롭의 작은 비교 영역에서 한쪽 눈이 잘리던 문제를 보완했습니다. 넓은 영역에서 두 눈의 모양·새로 나타난 위치·주변 판의 안정성을 함께 확인합니다. 실제 물 드롭·변화 없는 이웃 방해 드롭·밝기 변화만으로는 새 판정이 통과하지 않습니다.

스킬은 사용 가능한 필드 파괴를 먼저 사용한 뒤 새로 내려온 잠금 블록을 다시 확인합니다. 이후 관통·잠금 해제 등 필요한 대응을 선택하며, 필드 파괴가 대기·사용 불가이면 기존 차선책으로 넘어갑니다. 바인드된 캐릭터·자동사용 꺼짐·미확인 스킬은 계속 제외합니다.

SDK·기존 스킬 컨트롤러·강화 캡처64·저장 속도·최종 판 검증·해제·F10을 유지합니다. 중간 캡처나 자동 이동 재시도는 추가하지 않았습니다. 학습 모델은 변경하지 않았습니다.

Linux401개·Windows125개 및 패키징 검사 통과. 저장된090 이미지와 스크립트 장치를 이용한 시작 확인/필드→관통→새 잠금 해제→탐색 회귀 검사이며 사용자MuMu 직접 조작은 포함하지 않습니다. REVIEW216.md와 release_checks216.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.8',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.8 · 방해 드롭 시작과 필드 파괴 우선',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.8 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
