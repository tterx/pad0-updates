"""Publish only the checked209 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks209.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_1.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=209,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.1'),None)
notes='''083 오류의 시작 드롭 확인 오판을 수정했습니다. 실제로 들린 어둠 드롭의 반투명 중심부를 회복색으로 읽어 MOVE 없이 중지하던 사례입니다. 원본 어둠 무늬·보라색 테두리·주변 칸의 정지를 함께 확인하는 시작 증거를 추가했습니다.

SDK/기존 컨트롤러 고정, 강화 캡처, 저장된 이동 속도, 코너 대기, 경로 계산과 연전 판단은 유지합니다. 속도 값을 바꾸거나 새 DOWN/경로 재시도를 추가하지 않았습니다. 실제 회복 드롭·정지 화면·다른 시작 칸은 계속 차단하며 F10/손가락 해제/최종 판 검증/오류 ZIP을 유지합니다.

Linux373개·Windows81개 및 패키징 검사 통과. 첨부 실제 이미지에서 시작 증거를 확인했고 스크립트 SDK 채널로 확인 뒤 MOVE가 전달되는 흐름을 검사했습니다. 실제 MuMu 게임 조작은 포함하지 않습니다. 자세한 근거는 REVIEW209.md 및 release_checks209.json에 있습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.1',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.1 · 어둠 드롭 시작 확인 수정',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.1 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
