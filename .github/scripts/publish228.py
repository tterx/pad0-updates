"""Publish only the checked228 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks228.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_20.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.20'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=228,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.20'),None)
notes='자동 실행이 이동 전에 멈추던 첨부104의 원인을 수정했습니다. 가시가 붙은 어둠 드롭을 누르면 반투명해져 중앙이 독처럼 읽히던 장면입니다. 실제 들린 원본 무늬·가시·배경을 비교해 시작을 확인하며, 다른 드롭이나 정지 화면은 통과시키지 않습니다. 입력 방식과 이동 속도는 유지합니다.\n\n수동으로 경로를 찾은 뒤 가시 위치가 달라졌다고 실행을 막던 문제도 수정했습니다. 실제 경로 계산에 사용한 위험 조건을 기준으로 다시 확인합니다. 체력이 충분하고 표시만 달라진 경우 실행할 수 있으며, 체력이 낮아지거나 새로운 위험이 생기면 다시 계산합니다. 시간 조건을 완화해 찾은 경로도 실제 목표와 이동 한도로 검증해 수동 실행에 전달합니다.\n\n최종 소스7163d29의 Windows 검사229개 통과. Linux266개 중265개 통과, Windows 전용1개 건너뜀. ZIP·내장 소스·업데이트·백업·손상 파일 거부·로컬 AI 자료 정리를 검증했습니다. 첨부 실제 이미지와 모의 컨트롤러로 재현했으며, 물리 MuMu 게임 조작은 이 환경에서 검증하지 않았습니다. REVIEW228.md·THORN_START228.json·release_checks228.json에 근거를 기록했습니다.\n\n5속성 우선·시간 인식 및 단계별 경로·개발환경 내장 모델만 사용·로컬 AI 자료 삭제·AI 모델 버튼 제거 정책을 유지합니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.20',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.20 · 가시 드롭 시작 확인과 수동 실행',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.20 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
