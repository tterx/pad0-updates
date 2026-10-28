"""Publish only the checked234 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks234.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_26.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.26'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=234,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.26'),None)
notes='어둠4개·9초 판에서 일반 탐색이 4매치를 놓치는 경우를 보완했습니다. 대상 색 위치만 사용하는 전용 탐색으로 실제4매치를 먼저 확보하고, 그 경로를 보존하며 추가 TPA·콤보를 개선합니다. 지워지지 않는 넷째 드롭을4매치 성공으로 표시하지 않도록 실제 그룹도 확인합니다.\n\n첨부 판을 색상으로 전사해8이동 경로를 확인했습니다. 맨 아래 줄 둘째 어둠에서 오른쪽·오른쪽·위·위·위·오른쪽·아래·왼쪽으로 움직이면 어둠4개가 세로 매치됩니다. 짧은 계산 예산,4방향 반전, 실제 수동9초 조건과 실행 전 검사를 통과했습니다. 원본 PNG의 인식 결과나 실기 원인을 확정한 검사는 아닙니다.\n\nWindows329개 검사 통과. Linux366개 중365개 통과, Windows 전용1개 제외. 업데이트·백업·기존 설정 보존을 검사했습니다. 실제 MuMu 조작은 미검증입니다. 가산 아이콘 모델·타이머OCR·입력 속도는 변경하지 않았습니다. REVIEW234.md·TPA234.json·release_checks234.json에 근거를 기록했습니다. PAD0를 종료하고 시작.vbs를 다시 실행하면 업데이트됩니다.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.26',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.26 · 어둠4개 판 TPA 탐색 개선',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.26 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
