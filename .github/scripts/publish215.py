"""Publish only the checked215 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks215.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_7.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=215,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.7'),None)
notes='''089는 경로가 끝나기 전에 v1.0.6의 새 코너 확인이 이동을 중단한 사례였습니다. 교환 중인 칸의 읽기값이 흔들리고 확인 시간도 소진돼 실제 이동 실패를 확인하지 못한 채 중단했습니다.

중간 캡처와 색 판독을 제거해 SDK 연속 이동을 복원했습니다. 저장된 속도·모든 경유점·시작 드롭 확인·최종 판 검증·원래 손가락 해제·F10은 유지합니다. 입력 재시도나 백엔드 자동 전환은 추가하지 않았습니다.

오류 ZIP은 시작 확인 화면을 start.png로 구분하고, 정상 해제 후에는 진단용 화면을 한 번만 읽어 released.png에 저장합니다. 해당 시점도 명시합니다. 중간 애니메이션을 정상 드롭으로 판독해 필수 통과시키지 않습니다.

Linux377개·Windows114개 및 패키징 검사 통과. 실제088/089 자료와 스크립트 컨트롤러 회귀 검사이며 사용자MuMu 직접 조작은 포함하지 않습니다. 기존088의 드문 코너 누락까지 해결됐다고 확정하지 않습니다. REVIEW215.md와 release_checks215.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.7',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.7 · SDK 연속 이동 복원',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.7 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
