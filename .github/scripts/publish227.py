"""Publish only the checked227 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks227.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_19.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.19'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=227,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.19'),None)
notes='시간 인식 자체를 개선했습니다. 흰 판 테두리에 붙은 오른쪽 숫자 배지도 숫자 윤곽으로 찾고 왼쪽 시계 아이콘은 제외합니다. 첨부103의 실제8초를 기존 RapidOCR로 읽어 두 판독이8로 일치했고, 신뢰도는99.08%와91.96%였습니다. 강한 판독 충돌·숫자 가림은 추측하지 않습니다.\n\n시간 인식이 끝내 실패하거나 대응 스킬이 없어 기존 경로를 찾지 못하면4초 잔여를 해제하고 **5속성·6콤보 → 6콤보만** 순서로 시도합니다. 읽은 시간은 정상적으로 사용하며, 미확인은 미확인으로 기록합니다. 시간 미확인 시의5초는 제한된 입력 예산이며 실제 인식값이 아닙니다. 정상적으로 기존 조건을 만족하는 경로는4초 잔여를 유지합니다.\n\nAI 모델 버튼을 제거했습니다. 개발환경 내장 모델만 사용하고 로컬 학습·자료 저장을 하지 않는1.0.18 정책은 유지합니다.\n\nLinux249개 중248개 통과·Windows 전용1개 건너뜀, 실패/오류0개. 소스2b047fa의 Windows 집중 검사와 패키지 검증을 통과한 파일을 배포합니다. 실제 MuMu 플레이는 미검증입니다. REVIEW227.md·TIMER_OCR227.json·release_checks227.json에 근거를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.19',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.19 · 시간 인식과 단계별 경로',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.19 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
