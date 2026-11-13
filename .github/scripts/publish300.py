"""Publish verified release300 assets; channel activation remains a separate commit."""
from pathlib import Path
import hashlib,json,os,urllib.request,zipfile

repo=os.environ['GITHUB_REPOSITORY']
assert repo=='tterx/pad0-updates'
token=os.environ['GH_TOKEN']
folder=Path('releases/v1.0.92')
meta=json.loads((folder/'release_checks300.json').read_text())
assert meta['version']=='v1.0.92' and meta['release']==300
assert meta['windows_ci']['conclusion']=='success'
assert meta['windows_ci']['head_sha']==meta['source_commit']
stable=meta.get('publication',{}).get('approved_release') is True
title='퍼드0 올인원 1.0.92 · 드롭·파티 확인 및 지도 복귀 보완' if stable else '퍼드0 올인원 1.0.92 후보 · 드롭·파티 확인 및 지도 복귀 보완'
artifact=folder/'pad0_overlay_windows_v1_0_92.zip'
assert hashlib.sha256(artifact.read_bytes()).hexdigest()==meta['zip_sha256']
with zipfile.ZipFile(artifact) as z:
    assert z.testzip() is None
    assert z.read('app/VERSION.txt').strip()==b'v1.0.92'
    source=z.read('app/pad0_overlay.py')
    assert len(source)<=2*1024*1024
    assert hashlib.sha256(source).hexdigest()==meta['source_sha256']
    assert json.loads(z.read('app/pad0_update_state.json'))==dict(release=300,sha256=meta['source_sha256'])
    compile(source,'pad0_overlay.py','exec')

def api(path,method='GET',data=None,content_type=None):
    url=path if path.startswith('https://uploads.github.com/') else 'https://api.github.com'+path
    body=data if content_type else None if data is None else json.dumps(data).encode()
    request=urllib.request.Request(url,data=body,method=method,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','Content-Type':content_type or 'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)

notes=(folder/'release_notes.md').read_text()
release=next((r for r in api(f'/repos/{repo}/releases?per_page=20') if r['tag_name']=='v1.0.92'),None)
if release is None:
    release=api(f'/repos/{repo}/releases','POST',dict(tag_name='v1.0.92',target_commitish=os.environ['GITHUB_SHA'],
        name=title,body=notes,draft=True,prerelease=not stable,make_latest='false'))
for path,content_type in [(artifact,'application/zip'),(folder/'checksums_v1_0_92.json','application/json')]:
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    asset=next((a for a in release['assets'] if a['name']==path.name),None)
    if asset is None:
        assert release['draft'],'Never replace a published candidate asset'
        asset=api(f'https://uploads.github.com/repos/{repo}/releases/{release["id"]}/assets?name={path.name}',
                  'POST',path.read_bytes(),content_type)
    assert asset['size']==path.stat().st_size and asset['digest']=='sha256:'+digest
if release['draft'] or stable:
    release=api(f'/repos/{repo}/releases/{release["id"]}','PATCH',dict(
        draft=False,prerelease=not stable,make_latest='true' if stable else 'false',target_commitish=os.environ['GITHUB_SHA'],body=notes,name=title))
assert not release['draft'] and release['prerelease']==(not stable)
print('Published release:' if stable else 'Published candidate:',release['html_url'])
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
    summary.write('Published verified v1.0.92 release. Channel activation is managed separately.\n\n' if stable else 'Published verified v1.0.92 candidate. Automatic update channel unchanged.\n\n')
    summary.write('ZIP SHA256: `'+meta['zip_sha256']+'`\n')
