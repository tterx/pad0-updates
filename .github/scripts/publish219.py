"""Publish only the checked219 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.11/release_checks219.json').read_text())
artifact=Path('releases/v1.0.11/pad0_overlay_windows_v1_0_11.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=219,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.11'),None)
notes='095에서는6-13 번호를 두 번 확인했지만 DB 출현 이미지와 부족 재료를 연결하지 못해 진입을 중단했습니다. 방금 이미지가 일치한 재료를 누르고 연 획득 던전 목록을 추가 선택 근거로 사용합니다. 직접 열린 목록에는 이 근거를 재사용하지 않으며, 확인된 스테이지에서만 적용합니다.\n\n이미지가 미확정인 출현 크리스탈의 ID·판단 정답·재고·보상 집계는 변경하지 않습니다. 여러 필요 재료가 겹치는 던전을 우선하며 잠입 화면의 번호가 다르면 중지합니다. 실패 ZIP에는 선택 단계의 실제 목록 화면과 DB 연결·선택 근거를 기록합니다.\n\nSDK·기존 스킬 컨트롤러·강화 캡처64·속도·모델·F10을 유지합니다. Linux 핵심·관련371개, Windows145개와 패키징 검사를 통과했습니다. 실제 사용자 MuMu 조작은 미확인입니다. REVIEW219.md 및 release_checks219.json에 검증 범위를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs로 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.11',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.11 · 획득 목록을 통한 파밍 진입',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.11 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
