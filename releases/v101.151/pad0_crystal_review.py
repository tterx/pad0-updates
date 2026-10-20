"""Human-reviewed crystal image pairs, independent of UI and device input."""
from __future__ import annotations
import base64
import hashlib
from pathlib import Path
import re
import threading
import time
import zlib
import numpy as np
from PIL import Image
from pad0_common import _atomic_json_write, _load_json_dict
from pad0_crystal import crystal_art_signatures

LOCK=threading.RLock()
_MODEL_CACHE={}


def _root(root):return Path(root)/'crystal_review'

def _pack(signatures):
    return base64.b85encode(zlib.compress(np.asarray(signatures,dtype=np.int16).tobytes())).decode('ascii')

def _unpack(value):
    return np.frombuffer(zlib.decompress(base64.b85decode(value)),dtype=np.int16).reshape(3,64,48,3)

def _distance(a,b):return min(float(np.abs(x-y).mean()) for x,y in zip(a,b))

def icon_review_list(root):
    with LOCK:
        records=[_load_json_dict(path) for path in (_root(root)/'cases').glob('*/record.json')]
        return sorted((r for r in records if r.get('id')),key=lambda r:r.get('created',0),reverse=True)[:200]

def icon_review_record(root,image,references,reason):
    probe=_pack(crystal_art_signatures(image));candidates=[]
    for cid,label,reference,score in references:
        candidates.append(dict(cid=str(cid),label=str(label),signature=_pack(crystal_art_signatures(reference)),score=float(score)))
    if not candidates:return None
    digest=hashlib.sha256((probe+repr([(c['cid'],c['signature']) for c in candidates])).encode()).hexdigest()
    folder=_root(root)/'cases'/digest
    with LOCK:
        previous=_load_json_dict(folder/'record.json')
        if previous:return previous
        folder.mkdir(parents=True,exist_ok=True);image.convert('RGB').save(folder/'probe.png')
        for index,(_,_,reference,_) in enumerate(references):reference.convert('RGB').save(folder/f'reference_{index}.png')
        record=dict(id=digest,created=time.time(),probe=probe,candidates=candidates,reason=str(reason),decisions={},status='pending')
        _atomic_json_write(folder/'record.json',record);return record

def icon_review_confirm(root,case_id,cid,same):
    if not re.fullmatch('[0-9a-f]{64}',str(case_id)):raise ValueError('비교 기록을 확인해 주세요.')
    with LOCK:
        folder=_root(root)/'cases'/case_id;record=_load_json_dict(folder/'record.json')
        candidate=next((c for c in record.get('candidates',[]) if c['cid']==str(cid)),None)
        if candidate is None or type(same) is not bool:raise ValueError('비교할 등록 재료를 선택해 주세요.')
        decisions=record.setdefault('decisions',{})
        if same:
            # Selecting one identity invalidates a previous positive decision.
            for other in list(decisions):
                if decisions[other]=='same':decisions.pop(other)
        decisions[str(cid)]='same' if same else 'different'
        record['status']='confirmed' if same or len(decisions)==len(record['candidates']) else 'pending'
        _atomic_json_write(folder/'record.json',record)
        model=_load_json_dict(_root(root)/'matches.json');examples=model.setdefault('examples',{})
        for key in list(examples):
            if key.startswith(case_id+':'):examples.pop(key)
        for entry in record['candidates']:
            verdict=decisions.get(entry['cid'])
            if verdict:
                examples[case_id+':'+entry['cid']]=dict(probe=record['probe'],reference=entry['signature'],cid=entry['cid'],same=verdict=='same')
        _atomic_json_write(_root(root)/'matches.json',model)
        return record

def _examples(root):
    path=_root(root)/'matches.json'
    try:
        stat=path.stat();key=(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    except FileNotFoundError:return []
    with LOCK:
        if key not in _MODEL_CACHE:
            model=_load_json_dict(path);examples=[]
            for row in model.get('examples',{}).values():
                try:examples.append((row,_unpack(row['probe']),_unpack(row['reference'])))
                except (ValueError,TypeError,KeyError,zlib.error):continue
            _MODEL_CACHE.clear();_MODEL_CACHE[key]=examples
        return _MODEL_CACHE[key]

def icon_review_hint(root,image,templates):
    probe=crystal_art_signatures(image)
    positives=[];excluded=set()
    for example,saved_probe,saved_reference in _examples(root):
        cid=example.get('cid')
        if cid not in templates:continue
        try:
            # Both the current card and DB artwork must agree with the reviewed pair.
            if _distance(saved_reference,templates[cid])>.25:continue
            distance=_distance(probe,saved_probe)
            if distance>1.5:continue
            if example['same']:positives.append((distance,cid))
            else:excluded.add(cid)
        except (ValueError,TypeError,KeyError,zlib.error):continue
    positives.sort()
    ids={cid for _,cid in positives}
    return (positives[0][1] if len(ids)==1 and positives[0][1] not in excluded else None),excluded
