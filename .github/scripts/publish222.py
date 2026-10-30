"""Publish only the checked222 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.14/release_checks222.json').read_text())
artifact=Path('releases/v1.0.14/pad0_overlay_windows_v1_0_14.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=222,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.14'),None)
notes='098에서 아텐 카드에 남은 쿨타임1과 준비 삼각형이 함께 보이는데 삼각형만 보고 사용 가능으로 선택하던 문제를 수정했습니다. 오른쪽 위의 작은 쿨타임 글자가 있으면 준비 후보와 클릭 직전 검사에서 제외합니다. 다른 사용 가능한 스킬 또는 기존 가능한 콤보 경로로 진행합니다.\n\n초상화 전체를 OCR하지 않고 정해진 오른쪽 위 영역에서 밝은 글자 모양·위치·검은 외곽선을 확인합니다. 해당 표시의 존재만 사용하며 턴수를 추측하거나 단축 스킬의 근거로 쓰지 않습니다. 가운데 바인드 숫자와 의상 글자는 이 영역 밖입니다. 실제055에서는 준비된 아텐이 계속 허용되고,098에서는 쿨타임 중인 아텐이 클릭 전에 제외되는지 검사했습니다.\n\nSDK·스킬 클릭 방식·강화 캡처64·속도·모델·스킬 발동 확인·F10은 유지합니다. Linux390개 통과, Windows159개 통과 및 패키징 확인. Linux에서 건너뛴 Windows 전용 DLL 검사1개는 Windows CI에서 실행·통과했습니다. 사용자 MuMu 실기는 미확인입니다. REVIEW222.md 및 release_checks222.json에 범위를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.14',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.14 · 쿨타임 중 스킬 클릭 차단',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.14 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
