"""Publish only the checked230 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('releases/v1.0.22/release_checks230.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('releases/v1.0.22/pad0_overlay_windows_v1_0_22.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.22'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=230,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.22'),None)
notes='각성 등록은 현재 크리처의 번호를 등록 전후에 확인해 연결합니다. 이름 OCR이 흔들려도 각성 이름·효과를 두 번 확인하면 동일 번호에 등록할 수 있습니다. 기존 이름을 미확인 OCR로 덮어쓰지 않으며, 번호나 효과가 다르면 저장하지 않습니다.\n\n**연결&지우기** 모드를 추가했습니다. 같은 드롭 4개 이상 연결·6콤보 이상·조작시간 2초 남기기를 함께 확인합니다. 확률로 나오는 콤보드롭은 보장 콤보에 넣지 않습니다. **TPA**는 TPA 각성을 가진 현재 파티원의 주·부속성만 정확한 4개 매치 목표에 포함합니다. 다른 속성의 4개 매치를 TPA 성공으로 세지 않습니다.\n\n메인창의 크리처 1기 등록·등록한 크리처/각성 확인 버튼을 제거하고, **스킬 택틱 · 상태별 우선순위** 창을 추가했습니다. 현재 파티 6기의 스킬을 보며 상태별 순서를 추가·이동·삭제·저장합니다. 준비된 대응 스킬 사이에서 저장한 순서를 따르며, 해제·잠금 해제 등 기존 우선 대응은 유지합니다. 파티가 달라지면 이전 순서를 다른 캐릭터에게 적용하지 않습니다. 저장한 택틱은 다음 자동 파밍부터 적용합니다.\n\n최종 소스1a87776의 Windows 검사283개 통과. Linux320개 중319개 통과, Windows 전용1개 건너뜀. 실제 Tk 편집·저장·다시 불러오기, 스킬 선택, ZIP·번들·업데이트·백업·손상 파일 거부·AI 자료 정리와 택틱 보존을 검증했습니다. 실제 사용자 원본 스크린샷 OCR과 물리 MuMu 조작은 미검증입니다. OCR230.json은 직접 렌더링한 번호·한글 이름으로 실제 OCR을 실행한 결과이며 사용자 원본 판독 결과가 아닙니다. REVIEW230.md·release_checks230.json에 근거를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.22',target_commitish=os.environ['GITHUB_SHA'],
        name='퍼드0 올인원 1.0.22 · 각성 등록·연결 경로·스킬 택틱',body=notes,draft=True,prerelease=False))
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
    summary.write(f'v1.0.22 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
