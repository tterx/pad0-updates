"""Publish only the checked223 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.15/release_checks223.json').read_text())
artifact=Path('releases/v1.0.15/pad0_overlay_windows_v1_0_15.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=223,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.15'),None)
notes='099에서 마지막 드롭을 들고 있을 때 그 칸의 판독이 어둠→회복→어둠으로 바뀌어, 나머지29칸이 세 번 모두 맞아도 손 떼기 전 안정화 실패로 중단되던 오류를 수정했습니다. 불일치 검사에서는 마지막 칸을 제외했지만 연속 안정성 검사에서는 포함하던 기준 차이가 원인이었습니다.\n\n두 검사 모두 고정된29칸을 사용하고, 예상 배치와 일치하는 판이 두 번 연속 확인되어야 통과합니다. 다른 칸이 틀리면 연속 확인을 초기화합니다. 들고 있는 마지막 드롭은 기존 별도 영상·입력 경로 검증과 손 떼기 후 게임 반응 확인으로 검증합니다. 실제 배치 불일치를 무시하거나 이동을 재시도하지 않습니다.\n\nSDK·스킬 컨트롤러·강화 캡처64·속도·모델·쿨타임·구름 처리·F10은 유지합니다. Linux 핵심·관련373개, Windows164개 및 패키징 검사 통과. 실제 사용자 MuMu 플레이는 미확인입니다. REVIEW223.md 및 release_checks223.json에 범위를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.15',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.15 · 들고 있는 마지막 드롭의 안정성 비교 수정',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.15 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
