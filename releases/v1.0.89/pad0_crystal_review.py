"""Human-reviewed crystal image pairs, independent of UI and device input."""
from __future__ import annotations
import base64
import hashlib
import json
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

def _distance(a,b):
    # Retain the stored 3 x 64 x 48 LAB format, but never score the lower
    # quantity area. Old reviewed pairs and new captures use the same mask.
    return min(float(np.abs(np.asarray(x)[:46]-np.asarray(y)[:46]).mean()) for x,y in zip(a,b))


def crystal_artwork_score(probe,references):
    """Compare portrait interiors; a shared frame cannot supply identity evidence.

    Keep the persisted signature format unchanged. Small capture shifts are
    checked inside the artwork, outside the frame and the amount badge.
    """
    best=float('inf')
    for left,right in zip(probe,references):
        a=np.asarray(left,dtype=np.float32);b=np.asarray(right,dtype=np.float32)
        if a.shape!=(64,48,3) or b.shape!=a.shape:continue
        for dy,dx in ((0,0),(-2,0),(2,0),(0,-2),(0,2)):
            x=a[12:46,10:38];y=b[12+dy:46+dy,10+dx:38+dx]
            distance=float(np.abs(x-y).mean())
            if distance<=3:
                best=min(best,distance);continue
            if distance>=25:continue
            l=x[:,:,0]-x[:,:,0].mean();r=y[:,:,0]-y[:,:,0].mean()
            scale=float(np.sqrt(np.sum(l*l)*np.sum(r*r)))
            if scale>1 and float(np.sum(l*r))/scale>=.60:best=min(best,distance)
    return best


def crystal_identity_score(probe,references):
    """Require both the upper card and portrait to agree, ignoring ×1..×999."""
    return max(_distance(probe,references),crystal_artwork_score(probe,references))


def complete_reward_card_boxes(image,viewport):
    """Keep pale crystal facets connected by luminance edges, independent of saturation."""
    import cv2
    rgb=np.asarray(image.convert('RGB'));height,width=rgb.shape[:2]
    left,right=round(width*viewport[0]),round(width*viewport[1])
    top,bottom=round(height*viewport[2]),round(height*viewport[3])
    patch=rgb[top:bottom,left:right]
    if not patch.size:return []
    edge=cv2.Canny(cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY),45,130)
    edge=cv2.morphologyEx(edge,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(edge,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    found=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if not (.035*width<=w<=.14*width and .09*height<=h<=.29*height and .55<=w/h<=1.02):continue
        if x<=0 or y<=0 or x+w>=patch.shape[1]-1 or y+h>=patch.shape[0]-1:continue
        if cv2.contourArea(contour)<.78*w*h:continue
        art=patch[y:y+h,x:x+w];hsv=cv2.cvtColor(art,cv2.COLOR_RGB2HSV)
        if ((hsv[:,:,1]>45)&(hsv[:,:,2]>55)).mean()<.12 or art.std(axis=(0,1)).mean()<10:continue
        found.append((left+x,top+y,left+x+w,top+y+h))
    result=[]
    for box in sorted(found,key=lambda b:(b[2]-b[0])*(b[3]-b[1]),reverse=True):
        a,b,c,d=box
        if any(max(0,min(c,z)-max(a,x))*max(0,min(d,t)-max(b,y))>.55*min((c-a)*(d-b),(z-x)*(t-y)) for x,y,z,t in result):continue
        result.append(box)
    return result


def normalized_crystal_icon(image):
    """Unwrap old F7 icons only with one card, its upper pill and neutral margins.

    Ordinary tight icons and ambiguous/multi-card images remain unchanged. No
    stored image or persisted signature is rewritten by this pixel-only view.
    """
    import cv2
    image=image.convert('RGB');rgb=np.asarray(image,dtype=np.int16)
    h,w=rgb.shape[:2]
    if min(h,w)<40:return image,None
    chroma=np.ptp(rgb,axis=2)
    coloured=((chroma>25)&(rgb.max(axis=2)>70)).astype(np.uint8)
    coloured=cv2.morphologyEx(coloured,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(coloured,8)
    cards=[]
    for x,y,cw,ch,area in stats[1:]:
        if (.35*w<=cw<=.94*w and .40*h<=ch<=.85*h and .60<=cw/ch<=1.0
                and area>=cw*ch*.25 and x>=3 and x+cw<=w-3
                and y>=h*.12 and y+ch<=h*.96):cards.append((int(x),int(y),int(cw),int(ch)))
    if len(cards)!=1:return image,None
    x,y,cw,ch=cards[0]
    outside=np.ones((h,w),dtype=bool);outside[y:y+ch,x:x+cw]=False
    if float((chroma[outside]<=25).mean())<.94:return image,None
    dark=((chroma<28)&(rgb.mean(axis=2)>=32)&(rgb.mean(axis=2)<=125)).astype(np.uint8)
    dark=cv2.morphologyEx(dark,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    _,_,pills,_=cv2.connectedComponentsWithStats(dark,8)
    above=[(px,py,pw,ph) for px,py,pw,ph,area in pills[1:]
        if .65*cw<=pw<=1.25*cw and .10*ch<=ph<=.40*ch and area>=pw*ph*.62
        and py+ph<=y-2 and abs(px+pw/2-(x+cw/2))<=cw*.15]
    if len(above)!=1:return image,None
    pad=max(1,round(cw*.02));box=(max(0,x-pad),max(0,y-pad),min(w,x+cw+pad),min(h,y+ch+pad))
    return image.crop(box),box

def icon_review_list(root):
    with LOCK:
        records=[_load_json_dict(path) for path in (_root(root)/'cases').glob('*/record.json')]
        return sorted((r for r in records if r.get('id')),key=lambda r:r.get('created',0),reverse=True)[:200]

def icon_review_record(root,image,references,reason,category='icon',context=None):
    probe=_pack(crystal_art_signatures(image));candidates=[]
    for cid,label,reference,score in references:
        candidates.append(dict(cid=str(cid),label=str(label),signature=_pack(crystal_art_signatures(reference)),score=float(score)))
    if not candidates:return None
    identity=(_pack([a[:46] for a in _unpack(probe)])+repr([
        (c['cid'],_pack([a[:46] for a in _unpack(c['signature'])])) for c in candidates])).encode()
    if category=='image_update':identity+=image.convert('RGB').tobytes()+b''.join(ref.convert('RGB').tobytes() for _,_,ref,_ in references)
    if context is not None:
        if not isinstance(context,dict):raise ValueError('비교 자료의 연결 정보가 잘못됐습니다.')
        context=json.loads(json.dumps(context,ensure_ascii=False,sort_keys=True))
        identity+=json.dumps(dict(category=category,context=context),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(identity).hexdigest()
    folder=_root(root)/'cases'/digest
    with LOCK:
        previous=_load_json_dict(folder/'record.json')
        if previous:return previous
        if category!='image_update':
            # A new amount must not create another identity question. Keep the
            # original images and all human verdicts; image-update reviews
            # deliberately retain their separate full-image history.
            for previous in icon_review_list(root):
                old=previous.get('candidates',[])
                if (previous.get('category','icon')!=category or previous.get('context')!=context
                        or [c.get('cid') for c in old]!=[c['cid'] for c in candidates]):continue
                try:
                    if (_distance(_unpack(previous['probe']),_unpack(probe))<=1.5
                            and all(_distance(_unpack(a['signature']),_unpack(b['signature']))<=.25 for a,b in zip(old,candidates))):
                        return previous
                except (KeyError,ValueError,TypeError,zlib.error):continue
        folder.mkdir(parents=True,exist_ok=True);image.convert('RGB').save(folder/'probe.png')
        for index,(_,_,reference,_) in enumerate(references):reference.convert('RGB').save(folder/f'reference_{index}.png')
        record=dict(id=digest,created=time.time(),probe=probe,candidates=candidates,reason=str(reason),decisions={},status='pending',category=category)
        if context is not None:record['context']=context
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


def crystal_border(image):
    """Read saturated frame pixels, excluding the artwork centre and ×N row."""
    import cv2
    rgb=np.asarray(image.convert('RGB'));h,w=rgb.shape[:2]
    if min(h,w)<20:return None
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    mask=np.zeros((h,w),dtype=bool)
    mask[round(h*.02):round(h*.15),round(w*.08):round(w*.92)]=True
    mask[round(h*.18):round(h*.72),round(w*.02):round(w*.15)]=True
    mask[round(h*.18):round(h*.72),round(w*.85):round(w*.98)]=True
    pixels=hsv[mask];coloured=pixels[(pixels[:,1]>=105)&(pixels[:,2]>=100)]
    if len(coloured)<max(12,len(pixels)*.04):return None
    hue=coloured[:,0]
    votes={'R':int(((hue<13)|(hue>=170)).sum()),'Y':int(((hue>=13)&(hue<40)).sum()),
           'G':int(((hue>=40)&(hue<90)).sum()),'B':int(((hue>=90)&(hue<130)).sum()),
           'P':int(((hue>=130)&(hue<170)).sum())}
    key=max(votes,key=votes.get)
    return key if votes[key]/len(coloured)>=.65 else None


def preview_stock_views(image):
    """Separate white/red ×N strokes from the coloured preview card border."""
    import cv2
    zone=image.crop((round(image.width*.32),round(image.height*.72),image.width,image.height))
    rgb=np.asarray(zone.convert('RGB'),dtype=np.int16)
    white=(rgb.min(axis=2)>=145)&(rgb.max(axis=2)-rgb.min(axis=2)<85)
    red=(rgb[...,0]>=125)&(rgb[...,0]>rgb[...,1]+40)&(rgb[...,0]>rgb[...,2]+30)
    count,labels,stats,_=cv2.connectedComponentsWithStats((white|red).astype(np.uint8),8)
    mask=np.zeros(labels.shape,dtype=np.uint8);components=[]
    for index in range(1,count):
        x,y,w,h,area=map(int,stats[index])
        if h>=max(7,zone.height*.25) and h<zone.height*.9 and w>=2 and area>=8 and x>0 and x+w<zone.width and y>0 and y+h<zone.height and y+h/2>zone.height*.4:
            mask[labels==index]=255;components.append((x,y,w,h,index))
    ys,xs=np.where(mask)
    if len(xs)<15:return zone,[]
    def views_for(selected):
        yy,xx=np.where(selected)
        if len(xx)<8:return []
        glyph=255-selected[yy.min():yy.max()+1,xx.min():xx.max()+1]
        glyph=cv2.resize(glyph,(max(1,round(glyph.shape[1]*64/glyph.shape[0])),64),interpolation=cv2.INTER_NEAREST)
        glyph=cv2.copyMakeBorder(glyph,12,12,12,12,cv2.BORDER_CONSTANT,value=255)
        return [Image.fromarray(pixels).convert('RGB') for pixels in (glyph,cv2.GaussianBlur(glyph,(3,3),.6))]
    views=views_for(mask)
    components.sort()
    if len(components)>=2:
        x,y,w,h,index=components[0]
        if .8<=w/h<=1.25:
            digits=mask.copy();digits[labels==index]=0
            views+=views_for(digits)
    return zone,views
