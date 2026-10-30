"""Publish only the checked229 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.21/release_checks229.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.21/pad0_overlay_windows_v1_0_21.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.21'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=229,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.21'),None)
notes='전체 바인드 등으로 사용할 대응 스킬이 없거나 적이 판을 바꿔 5속성 공격을 할 수 없을 때 **판 정리**로 턴을 진행합니다. 사용 가능한 해제·생성 스킬은 먼저 사용합니다.\n\n자동·수동 모두 이 상황에서는 **5속성·콤보 조건 없이 지우는 드롭 수를 우선**합니다. 19개를 지우는 4콤보보다 20개를 지우는 3콤보를 선택합니다. 자동은 매치를 찾지 못해도 실제 한 칸을 움직여 턴을 넘깁니다. 수동 버튼은 판 정리 실행으로 표시하며 매치를 확인한 경로를 실행합니다. 적 콤보 숫자의 인식 실패나 조건 변화가 이 정리 경로를 막지 않습니다. 정상 공격 가능한 판의 기존 5속성 우선 정책은 유지합니다.\n\n스크린샷의 불·방해 판을 직접 옮겨 재현해 30개를 지우는 5콤보·34이동 경로를 확인했습니다. 실제 판 인식·기기 동작 검증을 뜻하지 않으며, 탐색은 전역 최적해를 보장하지 않습니다. 시간·판 변화·입력 확인과 F10 중지는 유지합니다.\n\n최종 소스 af217b7의 Windows 검사244개 통과. Linux281개 중280개 통과, Windows 전용1개 건너뜀. ZIP·번들·업데이트·백업·손상 파일 거부·기존 로컬 AI 자료 삭제를 검증했습니다. 물리 MuMu 게임 조작은 이 환경에서 검증하지 않았습니다. REVIEW229.md·BOARD_CLEAR229.json·release_checks229.json에 근거를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.21',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.21 · 바인드·변환 판 정리와 턴 진행',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.21 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
