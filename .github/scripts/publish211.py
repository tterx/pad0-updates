"""Publish only the checked211 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks211.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_3.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=211,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.3'),None)
notes='''085의 마지막 잡힌 방해 드롭 검증 누락을 수정했습니다. 원본 방해의 두 눈·윤곽과 전체 경유점/contact/UP를 확인하고 손을 뗀 뒤 게임 반응 검사도 유지합니다. 실제 다른 칸이나 입력이 틀리면 중지합니다.

방해/물 전용 보조 모델을 별도로 학습해 내장했습니다. 화면 그룹을 나눠73개 원본에만 변형을 추가한584개 입력으로320epoch×초기값3개(960번 갱신)를 수행했습니다. 선택 검증36개와 미학습085의13개는 모두 맞았습니다. 작은 동일 게임 자료 범위이며 실사용 전체 정확도를 주장하지 않습니다. 낮은 확신/낯선/단색 입력은 기존 판독을 유지합니다. 기존 사용자 일반AI 파일을 보존하며 원본 학습PNG를 공개하지 않습니다.

SDK·기존 컨트롤러, 저장된 매칭 속도, 스킬/경로 계산 및 연전 정책은 유지합니다. Linux385개·Windows92개 및 패키징 검사 통과. 실기 MuMu 조작은 포함하지 않습니다. REVIEW211.md / JAMMER_WATER211.json / release_checks211.json에서 변경·학습·검증 범위를 확인할 수 있습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.3',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.3 · 방해·물 학습과 마지막 드롭 검증',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.3 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
