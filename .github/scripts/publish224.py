"""Publish only the checked224 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks224.json').read_text())
artifact=Path('pad0_overlay_windows_v1_0_16.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=224,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.16'),None)
notes='필드파괴는 확인된 가시가6칸 이상일 때만 최우선으로 사용합니다. 그 외에는 적의 데미지 무효·흡수·콤보 조건에 맞는 대응 스킬을 선택하며, 잠금이 있으면 색 변환 전에 해제합니다. 모든 스킬이 준비됐어도 조건이 없으면 필드파괴를 먼저 소비하지 않습니다. 관통·콤보가산이 필요한 복합 스킬의 필드파괴 부수 효과는 허용하며, 대응 스킬이 없을 때는 기존 차선책을 유지합니다.\n\n가시 전용 보조 모델을 실제 자료로 학습해 포함했습니다. 실제 가시5칸 중3칸과 가시가 없는120칸을 변형해1200개 학습 입력을 만들고,360단계×초기값3개(총1080번 갱신)를 수행했습니다. 다른 화면의 가시2칸을 포함한60칸 중59칸을 확정했고, 낮은 확신의1칸은 기존 인식을 유지합니다. 최종 가시가 없는90칸 평가은 오탐이 없었습니다. 실제 가시 표본이5칸뿐이고 학습·선택과 분리된 가시 평가가 없어 실사용 전체 정확도를 뜻하지 않습니다. 개인 모델과 방해/물211 모델은 보존합니다.\n\nLinux 핵심·관련402개, Windows175개와 패키징 검사 통과. SDK·기존 스킬 컨트롤러·강화 캡처64·저장된 속도를 유지합니다. 실제 사용자 MuMu 실기는 미확인입니다. REVIEW224.md·SPIKE224.json·release_checks224.json에 근거와 한계를 기록했습니다.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.16',target_commitish=os.environ['GITHUB_SHA'],
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
    summary.write(f'v1.0.16 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
