"""Publish only the checked221 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks221.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_13.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=221,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.13'),None)
notes='097의 한 칸 구름에서 회색 턴수 표시가 흰 구름 중심을 가려 구름 감지가 빠지던 문제를 보완했습니다. 해당 칸을 방해 드롭으로 확정하지 않고 기존 구름 기억·제자리 확인 절차로 보내며, 확인된30칸으로만 계산합니다.\n\n회색 중앙 사각형·안쪽 빨간 글자·바깥쪽 흰 구름 테두리가 함께 있어야 추가 감지합니다. 턴수 숫자를 드롭 색으로 읽거나 가려진 색을 추측하지 않습니다. 실제097 이동 전후 이미지와 두 배율을 검사했고, 흰 테두리나 빨간 글자가 없으면 추가 감지를 거부합니다. 기존 잘못 읽은 판의 눈에 보이는 불일치는 여전히 거부합니다.\n\n이동 속도·SDK·스킬 컨트롤러·강화 캡처64·경로 계산·학습된 가중치·F10은 유지합니다. Linux 핵심·관련361개, Windows154개 및 패키징 검사 통과. 사용자 MuMu 실기는 미확인입니다. REVIEW221.md 및 release_checks221.json에 검증 범위를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.13',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.13 · 턴수 표시 아래 한 칸 구름 감지',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.13 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
