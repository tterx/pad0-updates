"""Publish verified release331 assets; channel activation remains a separate commit."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
folder=Path('releases/v1.0.122')
meta=json.loads((folder/'release_checks331.json').read_text())
assert meta['version']=='v1.0.122' and meta['release']==331
assert meta['validation']['github_actions_executed'] is False
assert not meta['validation']['local']['failed']
assert meta['validation']['additional']['bundle']['failures']==meta['validation']['additional']['bundle']['errors']==0
assert meta['publication']['source_pr_merged'] is True
stable=meta.get('publication',{}).get('approved_release') is True
title='퍼드0 올인원 1.0.122 · 레벨 업 확인창 경로 버튼 숨김' if stable else '퍼드0 올인원 1.0.122 후보 · 레벨 업 확인창 경로 버튼 숨김'
artifact=folder/'pad0_overlay_windows_v1_0_122.zip'
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.122'
    source=z.read('app/pad0_overlay.py')
    assert len(source)<=2097152
    assert hashlib.sha256(source).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=331,sha256=meta['source_sha256'])
    compile(source,'pad0_overlay.py','exec')

def api(path,method='GET',data=None,content_type=None):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if content_type else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':content_type or 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

notes=(folder/'release_notes.md').read_text()
release=next((r for r in api(f'/repos/{repo}/releases?per_page=20') if r['tag_name']=='v1.0.122'),None)
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.122',target_commitish=os.environ['GITHUB_SHA'],
        name=title,body=notes,draft=True,prerelease=not stable,make_latest='false'))
download_url=meta['publication']['zip_download_url']
assert download_url.startswith('https://raw.githubusercontent.com/tterx/pad0-updates/')
with urllib.request.urlopen(download_url,timeout=60) as response:published_zip=response.read(8*1024*1024)
assert hashlib.sha256(published_zip).hexdigest()==meta['zip_sha256']
if release['draft'] or stable:
    release=api(f'/repos/{repo}/releases/{release["id"]}','PATCH',dict(
        draft=False,prerelease=not stable,make_latest='true' if stable else 'false',target_commitish=os.environ['GITHUB_SHA'],body=notes,name=title))
assert not release['draft'] and release['prerelease']==(not stable)
print('Published release:' if stable else 'Published candidate:',release['html_url'])
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
    summary.write('Published verified v1.0.122 release. Channel activation is managed separately.\n\n' if stable else 'Published verified v1.0.122 candidate. Automatic update channel unchanged.\n\n')
    summary.write('ZIP SHA256: `'+meta['zip_sha256']+'`\n')
