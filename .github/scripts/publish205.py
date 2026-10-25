"""Publish only the checked205 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks205.json').read_text())
artifact=Path('pad0_overlay_windows_v101_205.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=205,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v101.205'),None)
notes='''MaaTouch의 스킬 캐릭터/사용하기는 동일 컨트롤러에서 DOWN 완료 → 80ms 유지 → UP 완료로 처리합니다. 실패 시 원래 접점을 해제하며 재클릭하거나 다른 입력으로 전환하지 않습니다. 퍼즐 이동 속도/SDK 선택/MuMu 강화64/예상판·효과 확인/F10/오류 ZIP을 유지합니다.

학습 → 데이터 정리 → 학습된 모델만 private 저장소 업로드를 정식 반영합니다.

Linux 핵심/관련380개 통과(Windows 전용1개는 Windows에서 별도 확인), Windows41개와 패키징 검사 통과, 양쪽 플랫폼의 번들 SHA256 일치를 확인했습니다. 074 로컬 실기 문서는80ms 방식으로 두 스킬 발동을 보고했으나, 수정본 전체 MuMu 플레이를 온라인에서 직접 검증한 것은 아닙니다.

기존 설치는 PAD0를 완전히 종료하고 시작.vbs로 실행하세요. 새 설치는 첨부 ZIP을 사용하세요. 연결 방법은 사용법.txt와 README, 근거와 검사 결과는 REVIEW205.md 및 release_checks205.json에 있습니다.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v101.205',target_commitish=os.environ['GITHUB_SHA'],
        name='v101.205 MaaTouch 스킬80ms 유지 입력·학습 모델 업로드',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v101.205 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
