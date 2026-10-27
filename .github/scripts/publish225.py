"""Publish only the checked225 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks225.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_17.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=225,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.17'),None)
notes='현재 1.0.16의 기능 코드를 그대로 유지하고 표시 버전을 **1.0.17**, 자동 업데이트 내부 순번을 **225**로 올린 배포입니다. 새 기능이나 게임 동작 변경은 없습니다.\n\nWindows 입력·설정·패키징 CI가 소스 커밋 305dbaabab83a85cf8f60d75c8bd3d85f00e2653에서 통과했습니다. Linux 회귀검사는 215개 중 211개 통과, 기존 기대값 불일치 1개, 자료 누락 오류 2개, Windows 전용 건너뜀 1개입니다. ZIP 무결성·번들 로딩·224→225 업데이트·백업·반복 실행·손상 파일 거부 검사를 확인했습니다. 실제 Windows/MuMu 게임 조작은 이번 배포에서 검증하지 않았습니다.\n\n기존 설치는 PAD0를 완전히 종료한 뒤 `시작.vbs`를 다시 실행하세요. 새 설치는 첨부 ZIP을 풀고 `시작.vbs`를 실행하세요. 개인 설정·DB·학습 모델은 배포 ZIP에 포함하지 않습니다.\n'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.17',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.16 · 조건별 스킬 순서와 가시 학습',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.17 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
