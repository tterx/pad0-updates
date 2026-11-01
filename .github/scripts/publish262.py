"""Publish only the checked262 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.54/release_checks262.json').read_text())
assert meta['publication']['main_branch_updated'] is True, 'Source main promotion must be approved and completed before publication'
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.54/pad0_overlay_windows_v1_0_54.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.54'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=262,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.54'),None)
notes='판 인식부터 경로 준비까지의 중복 작업과 대기를 줄였습니다. 안정 확인 전 샘플 간격650→150ms, 같은 캡처의 중복 판독 재사용, 확인된 다속 조건의 반복 OCR 제거, 시간 표시줄 밖 배경 후보 제외, 결과 전달 대기300→100ms를 반영했습니다. 두 번의 판 안정 확인·실행 직전 현재 시간/콤보/판/가시 검증·별도 실행 버튼은 유지합니다. 같은 저장 화면의 로컬 단계별 중앙값은 판 읽기0.4474→0.2316초, 조건 준비0.8092→0.6016초였습니다. 사용자 PC 전체 시간은 미측정이며 실제 캡처·Windows 작업자 시작·MuMu 입력은 이 측정에서 제외했습니다. Windows588개, Linux625개 중624개(Windows 전용1개 제외), 번들73개·원본 조건 판독·업데이트 검사 통과. PAD0 종료 후 시작.vbs로 업데이트하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.54',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.54 · 경로 준비 대기 단축',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.54 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
