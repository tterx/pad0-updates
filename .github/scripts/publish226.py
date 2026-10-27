"""Publish only the checked226 artifact; no emulator or training data access."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
meta=json.loads(Path('release_checks226.json').read_text())
assert meta['checks']['windows_ci']['conclusion']=='success'
assert meta['checks']['windows_ci']['head_sha']==meta['source_commit']
artifact=Path('pad0_overlay_windows_v1_0_18.zip')
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.18'
    assert hashlib.sha256(z.read('app/pad0_overlay.py')).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=226,sha256=meta['source_sha256'])

def api(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if binary else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/zip' if binary else 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

releases=api(f'/repos/{repo}/releases?per_page=20')
release=next((r for r in releases if r['tag_name']=='v1.0.18'),None)
notes='바인드·각성무효 해제 → 필요한 잠금 해제·5속성 생성 → 구름 확인 순서로 처리합니다. 칼리처럼 해제와 생성을 함께 하는 스킬은 중복 생성하지 않습니다. 구름 확인 후에도 실제 속성이 부족하면 다음 생성 스킬을 쓰고 다시 확인합니다.\n\n체력이 낮아도 5속성·6콤보가 최우선이며, 회복은 가능한 경우 함께 넣습니다. 회복 경로가 없다고 회복만 하는 경로를 먼저 택하지 않습니다. 판독하지 못한 구름 칸은 임시 방해(J)로 계산을 계속합니다.\n\n들린 독 드롭 전용 모델을 개발환경에서 학습해 내장했습니다. 실제 독4칸을 포함한30칸에서960개 변형 입력을 만들고360단계×3개 초기값(총1080번 갱신)을 수행했습니다. 별도30개 음성 검증으로 모델을 선택한 뒤, 첨부102의 들린 독1칸과 보이는18칸을 최종 평가해19칸 모두 판독했습니다. 작은 표본이며 같은 화면의 들림/일반 칸이 포함되므로 실사용 전체 정확도를 의미하지 않습니다.\n\n앱은 개발환경에서 학습한 내장 모델만 사용합니다. 로컬 학습·자료 저장·모델 업로드 기능은 제거했습니다. 업데이트 후 첫 실행에서 ai_review(모델·원본·정답·전송 대기 자료), ai_quantities, 저장된 판 이력을 삭제합니다. 크리스탈 DB·재고·파티·일반 설정은 유지합니다. 삭제 실패는 AI 모델 상태에 표시하고 다음 실행 때 다시 정리합니다.\n\nLinux233개 검사 중232개 통과·Windows 전용1개 건너뜀, 실패/오류0개. 소스 커밋1ce3bd1의 Windows CI 및 ZIP 검증을 통과한 파일을 배포합니다. 실제 MuMu 플레이는 미검증입니다. 자세한 근거와 한계는 REVIEW226.md·CLOUD_POISON226.json·release_checks226.json을 참고하세요.\n\nPAD0를 완전히 종료한 뒤 시작.vbs를 다시 실행하면 업데이트됩니다. 새 설치는 첨부 ZIP을 사용하세요.'
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.18',target_commitish=os.environ['GITHUB_SHA'],
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
    summary.write(f'v1.0.18 release uploaded and published. ZIP SHA256: `{meta["zip_sha256"]}`.\n\nUpdater activation remains separate.\n')
