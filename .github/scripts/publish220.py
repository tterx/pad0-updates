"""Publish only the checked220 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.12/release_checks220.json').read_text())
artifact=Path('releases/v1.0.12/pad0_overlay_windows_v1_0_12.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=220,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.12'),None)
notes='096에서 바인드가 남아 있는 동안 스킬 판단용 부분 판(미확인12칸)이 경로 계산으로 넘어가 중단되던 문제를 수정했습니다. 바인드·각성무효 해제와 가능한 대응 스킬을 먼저 확인하고, 사용할 스킬이 없으면 기존 구름 기억이나 미확인 칸의 제자리 드롭 확인으로30칸을 확정한 뒤 계산합니다. 미확인 칸을 속성 부족으로 간주해 불필요하게 판 변환 스킬을 쓰던 경로도 차단합니다.\n\n바인드가 남아 있어도 이미 확인된 구름 판은 새 입력 없이 재사용합니다. 모르는 칸은 기존 안정 확인·제자리 DOWN/UP·원상복귀 검증을 거치며, 판독 실패나 판 변화 시 중지합니다. 미확인 칸으로 경로를 계산하거나 동일 칸을 무작정 재시도하지 않습니다. F10과 손 떼기 안전 처리는 유지합니다.\n\nSDK·기존 스킬 컨트롤러·강화 캡처64·저장된 속도·모델·재료 집계·095 진입 수정은 유지합니다. Linux 핵심·관련360개, Windows150개와 패키징 검사를 통과했습니다. 실제 사용자 MuMu 조작은 미확인입니다. REVIEW220.md 및 release_checks220.json에 검증 범위를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs로 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.12',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.12 · 바인드와 구름 판 확인',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.12 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
