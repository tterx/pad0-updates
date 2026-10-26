"""Publish only the checked218 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks218.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_10.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=218,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.10'),None)
notes='''재료확인(F7)으로 재료 이름에 이어 획득 스테이지와 훈장 버튼의 출현 크리스탈 목록까지 등록하고 원래 재료창으로 돌아옵니다. 기존 이름과 확인된 출현 목록은 재사용하며, 보유 수량이 충분한 재료도 확인합니다. 등록 작업만으로 배틀에 진입하지 않습니다.

재료가 한 던전에 등록돼 있으면 나머지 스테이지 확인을 생략하던 조건을 제거했습니다. 전체 화면 판독에서 누락된 번호는 훈장 아래 글자 영역을 두 배율로 읽어 같은 번호일 때만 사용합니다. 미확정 번호를 추측하지 않으며, 목록 로딩이 늦으면 클릭을 반복하지 않고 화면만 한 번 더 확인합니다.

094 실패 ZIP에는 실제 실패한 획득 던전 화면 대신 이전 재료창이 저장돼 있었습니다. 이제 목록·출현 아이템의 현재 화면과 판독 기록, 작업 단계를 저장합니다. 기존 ZIP만으로 어떤 번호가 누락됐는지는 확정할 수 없습니다. 중간 실패 시 이미 등록한 이름과 스테이지는 보존합니다.

MuMu SDK 입력·기존 스킬 컨트롤러·강화 캡처64·저장된 속도·개인 DB·학습 모델·전투 검증·F10은 유지합니다. 그만두기 입력 완료 직후 재료 총 보유 수량과 이번 자동파밍 획득 마나를 알리는1.0.9 동작도 유지합니다.

Linux 핵심·관련391개와 최종 진단 화면 보강 뒤 관련10개 재확인, Windows141개 및 패키징 검사를 통과했습니다.사용자 MuMu 실기 조작 검사는 포함하지 않습니다. REVIEW218.md와 release_checks218.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.10',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.10 · 재료확인에서 획득 스테이지 등록',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.10 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
