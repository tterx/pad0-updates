"""Publish only the checked231 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.23/release_checks231.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.23/pad0_overlay_windows_v1_0_23.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.23'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=231,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.23'),None)
notes='재료확인의 흰색·빨간색 필요 수량 1~999와 보유 수량 ×0~999를 같은 절차로 판독하고 등록합니다. 빨간 보유 0을 거부하던 보조 경로와 세 자리 숫자가 잘릴 수 있던 자르기를 제거했습니다. ×를 숫자와 분리하고 자릿수·두 영상의 OCR 일치를 확인합니다. 작은 ×, 붙은 111, 작은 0의 배율 문제도 보완했습니다. 출현 재료 미리보기의 보유량에도 적용합니다.\n\n남아 있던 개인 수량 템플릿 사용·저장을 제거하고 과거 verified_stock_digits.json을 시작 시 정리합니다. 기존 재고·파티·택틱은 보존합니다.\n\n렌더링한 수량 전체 범위 3,998건과 글꼴/크기 변형486건 및 Windows 연결획 재현80건, 총4,564건의 실제 RapidOCR 검사 통과. 제보 숫자(필요3·2·2·1·10, 보유3·9·0·1·85) 패널의 판독과 등록도 통과했습니다. Windows298개 검사 통과, Linux335개 중334개 통과 및 Windows 전용1개 제외. 원본 첨부 화면의 픽셀 OCR과 물리 MuMu 조작은 미검증입니다. REVIEW231.md·QUANTITY231.json·release_checks231.json에 근거를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.23',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.23 · 흰색·빨간색 수량 판독과 등록',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.23 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
