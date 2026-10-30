"""Publish only the checked212 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.4/release_checks212.json').read_text())
artifact=Path('releases/v1.0.4/pad0_overlay_windows_v1_0_4.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=212,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.4'),None)
notes='''086 연전 오류를 수정했습니다. 버튼 영역은 맞았지만 한국어OCR이 영문OK를ㅇK/.75로 읽어 멈추던 사례입니다. 확인된 두 버튼 안의 실제OK·그만두기 글자 모양을 직접 비교해 해당 OCR 오독에 막히지 않게 했습니다. 빈글자/다른문자/잘못된 버튼쌍은 누르지 않으며 기존 OCR 기준도 유지합니다.

보상 집계 후 재료가 부족하면OK로 계속하고 충족하면그만두기 후 완료 알림을 보냅니다. 연전 스테이지 재판독은 하지 않습니다. 중복 클릭 방지/F10/화면 전환 확인을 유지합니다. SDK·기존 컨트롤러·매칭 속도·1.0.3의 방해/물 학습 모델은 변경하지 않았습니다.

Linux364개·Windows96개 및 패키징 검사 통과. 실제086의 글자 모양을 비교하며 사용자MuMu 조작은 포함하지 않습니다. REVIEW212.md / release_checks212.json에 검증 범위를 기록했습니다.

PAD0를 완전히 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부ZIP을 사용하세요.'''
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.4',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.4 · 연전 OK 오독 수정',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.4 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
