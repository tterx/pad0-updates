"""Quarantine v116 and restore v115 release discovery without changing assets."""
import hashlib,json,os,urllib.request
from pathlib import Path
assert os.environ['GITHUB_REPOSITORY']=='tterx/pad0-updates'
def api(path,data=None):
 request=urllib.request.Request('https://api.github.com/repos/tterx/pad0-updates'+path,
  data=None if data is None else json.dumps(data).encode(),method='GET' if data is None else 'PATCH',
  headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
releases={version:api('/releases/tags/v1.0.'+str(version)) for version in (115,116)}
for version,release in releases.items():
 checks=json.loads(Path(f'releases/v1.0.{version}/checksums_v1_0_{version}.json').read_text())
 asset=next(a for a in release['assets'] if a['name']==f'pad0_overlay_windows_v1_0_{version}.zip')
 assert asset['digest']=='sha256:'+checks['zip_sha256'] and asset['size']==checks['zip_bytes']
prefix='v1.0.116에서 버튼 미표시 추가 회귀가 보고되어 배포를 보류하고 v1.0.115로 복구했습니다. 자동 업데이트 내부 순번325가 기존 v1.0.116 설치도 v1.0.115로 되돌립니다. 정확한 회귀 원인과 기존 계산 문제는 조사 중입니다.\n\n'
current=releases[116]
api('/releases/'+str(current['id']),dict(prerelease=True,name='퍼드0 올인원 1.0.116 · 배포 보류',body=prefix+current['body'] if not current['body'].startswith(prefix) else current['body']))
previous=releases[115]
api('/releases/'+str(previous['id']),dict(prerelease=False,make_latest='true',body=prefix+previous['body'] if not previous['body'].startswith(prefix) else previous['body']))
assert api('/releases/latest')['tag_name']=='v1.0.115'
assert api('/releases/tags/v1.0.116')['prerelease']
print('v116 quarantined; latest restored to v115; published asset bytes unchanged.')
