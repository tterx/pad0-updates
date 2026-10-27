"""Local supervised quantity-image classifier and review store; no UI/devices.

Only explicitly confirmed examples enter the model. Normalised digit templates
compose quantities; approved whole strips remain a conservative fallback.
"""
from __future__ import annotations
import base64
import zlib
import hashlib
from pathlib import Path
import re
import threading
import time
import uuid
import numpy as np
from PIL import Image
from pad0_common import _atomic_json_write, _load_json_dict

QUANTITY_KINDS = ('필요', '보유', '획득')
QUANTITY_LOCK = threading.RLock()
_MODEL_CACHE = {}
_REVIEW_INDEX = {}


def quantity_root(root):
    return Path(root) / 'ai_quantities'


def _value(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,6}', str(value)):
        raise ValueError('수량은 0~999999의 정수로 입력해 주세요.')
    return int(value)


def _feature(image):
    if image.width < 4 or image.height < 4:
        raise ValueError('숫자 이미지가 너무 작습니다.')
    # Fixed crop geometry retains digit width, spacing and colour. It deliberately
    # avoids stretching each glyph to the same shape (e.g. confusing 1 and 7).
    rgb = image.convert('RGB').resize((96, 40), Image.Resampling.BILINEAR)
    return np.asarray(rgb, dtype=np.uint8)


def _number_glyphs(image):
    """Separate red/white ink; refuse uncertain lines instead of inventing digits."""
    import cv2
    a=np.asarray(image.convert('RGB'),dtype=np.int16);h,w=a.shape[:2]
    if min(h,w)<4:return None
    red_score=np.maximum(a[...,0]-a[...,1:].max(axis=2),0).astype('uint8')
    neutral=a.max(axis=2)-a.min(axis=2)<65
    background=float(np.percentile(a.min(axis=2),20))
    white_score=np.maximum(a.min(axis=2)-background,0).astype('uint8')
    def ink(score,eligible):
        # Relative half-ink threshold keeps anti-alias edges independent of
        # the other digits in the line and of red/white foreground colour.
        return (score>max(20,float(score.max())*.5))&eligible
    red=ink(red_score,a[...,0]>135)
    white=ink(white_score,neutral)
    candidates=[]
    for mask in (red,white):
        n,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype('uint8'),8)
        boxes=[];rejected=[]
        for i,(x,y,bw,bh,area) in enumerate(stats[1:],1):
            if bh<max(6,h*.16) or area<max(4,h*w*.0008) or bw>w*.85 or bh>h*.96:continue
            if area/(bw*bh)<.12 or y==0 or y+bh>=h:continue
            # Quantity text has a dark outline/backplate; pale crystal
            # highlights must not become extra digits or a multiplier.
            component=(labels==i).astype('uint8')
            ring=(cv2.dilate(component,np.ones((5,5),dtype='uint8'))>0)&(labels!=i)
            if not ring.any() or float((a.min(axis=2)[ring]<130).mean())<.77:
                rejected.append((int(x),int(y),int(bw),int(bh),i));continue
            boxes.append((int(x),int(y),int(bw),int(bh),i))
        if not boxes:continue
        # A line must have a common baseline and height. Artwork fragments are
        # not joined into a number just because they happen to be pale.
        for anchor in boxes:
            _,ay,_,ah,_=anchor
            line=sorted([b for b in boxes if abs((b[1]+b[3])-(ay+ah))<=max(3,ah*.18) and .55*ah<=b[3]<=1.5*ah],key=lambda b:b[0])
            if not line or len(line)>7:continue
            tallest=max(b[3] for b in line)
            # × is shorter and centred beside the digits in both game styles.
            if len(line)>1 and line[0][3]<tallest*.85:line=line[1:]
            if not line or len(line)>6 or any(b[3]<tallest*.82 for b in line):continue
            if any(line[i+1][0]-line[i][0]-line[i][2]>tallest*.85 for i in range(len(line)-1)):continue
            if len({b[4] for b in line})!=len(line):continue
            # If another text-sized component on this baseline was uncertain,
            # hold the entire number instead of silently dropping a digit.
            if any(.82*tallest<=b[3]<=1.3*tallest and abs(b[1]+b[3]-line[-1][1]-line[-1][3])<=max(3,tallest*.18)
                   and b[0]+b[2]>=line[0][0]-tallest*.85 and b[0]<=line[-1][0]+line[-1][2]+tallest*.85 for b in rejected):continue
            glyphs=[]
            for x,y,bw,bh,component in line:
                ink=(labels[y:y+bh,x:x+bw]==component).astype('uint8')*255
                # Preserve aspect: a narrow 1 must not be stretched into a 7.
                scale=min(20/bw,28/bh);size=(max(1,round(bw*scale)),max(1,round(bh*scale)))
                glyph=Image.fromarray(ink).resize(size,Image.Resampling.BILINEAR)
                canvas=Image.new('L',(24,32));canvas.paste(glyph,((24-size[0])//2,(32-size[1])//2))
                glyphs.append(np.asarray(canvas,dtype=np.uint8))
            key=hashlib.sha256(b''.join(g.tobytes() for g in glyphs)).hexdigest()
            score=(len(line),sum(b[2]*b[3] for b in line))
            candidates.append((score,key,glyphs))
    if not candidates:return None
    candidates.sort(key=lambda row:row[0],reverse=True)
    # Multiple colour layers that imply different equally plausible lines hold.
    best=candidates[0]
    competitors={row[1] for row in candidates if row[0][0]==best[0][0] and row[0][1]>=best[0][1]*.85}
    if len(competitors)>1:return None
    return best[1],best[2]


def _encode_glyph(glyph):
    return base64.b85encode(zlib.compress(glyph.tobytes())).decode('ascii')


def _digit_prediction(model,image):
    normalized=_number_glyphs(image)
    if normalized is None:return None,0.0
    key,glyphs=normalized
    known={int(row['value']) for row in model.get('numbers',[]) if row.get('key')==key}
    if len(known)==1:return next(iter(known)),1.0
    characters=[];confidence=1.0
    for glyph in glyphs:
        ranks=[]
        for sample in model.get('digits',[]):
            try:
                digit=str(sample['digit'])
                if not re.fullmatch(r'\d',digit):continue
                ref=np.frombuffer(zlib.decompress(base64.b85decode(sample['pixels'])),dtype=np.uint8).reshape(32,24)
                distance=float(np.abs(glyph.astype(np.float32)-ref.astype(np.float32)).mean()/255)
                ranks.append((distance,digit))
            except (KeyError,ValueError,TypeError,zlib.error):continue
        if not ranks:return None,0.0
        ranks.sort();distance,digit=ranks[0]
        alternative=min((d for d,v in ranks if v!=digit),default=1.0)
        threshold=.035 if model.get('allow_digit_approximation',False) else .004
        if distance>threshold or alternative-distance<.06:return None,max(0.0,1-distance)
        characters.append(digit);confidence=min(confidence,1-distance)
    if len(characters)>1 and characters[0]=='0':return None,0.0
    return _value(''.join(characters)),confidence


def quantity_model_status(root):
    model=_load_json_dict(quantity_root(root)/'model.json')
    return dict(schema=model.get('schema'),examples=len(model.get('samples',[])),
                digits=len({str(s.get('digit')) for s in model.get('digits',[])}),
                reviewed=int(model.get('reviewed',len(model.get('samples',[])))),
                validation=model.get('validation',{}))


def _reuse_quantity_record(folder,record,value,reason,context):
    if context is not None and not (folder/'context.png').is_file():
        context.convert('RGB').save(folder/'context.tmp',format='PNG')
        (folder/'context.tmp').replace(folder/'context.png')
    if record.get('status')!='confirmed' and reason.startswith('모델·OCR 일치'):
        record.setdefault('initial_predicted',record.get('predicted'))
        record.update(predicted=value,status='recognized',reason=reason)
        _atomic_json_write(folder/'record.json',record)
    return record


def _review_index(root):
    key=str(quantity_root(root).resolve())
    if key not in _REVIEW_INDEX:
        rows={}
        for path in (quantity_root(root)/'samples').glob('*/record.json'):
            record=_load_json_dict(path)
            if record.get('normalized_key'):rows.setdefault((record.get('kind'),record['normalized_key']),[]).append(record['id'])
        _REVIEW_INDEX[key]=rows
    return _REVIEW_INDEX[key]


def quantity_record(root, kind, image, value=None, reason='', card=None, context=None):
    if kind not in QUANTITY_KINDS:
        raise ValueError('알 수 없는 수량 구분입니다.')
    image = image.convert('RGB')
    if image.width < 4 or image.height < 4:
        return None
    if value is not None:
        value = _value(value)
    digest = hashlib.sha256(kind.encode()+str(image.size).encode()+image.tobytes()).hexdigest()
    normalized=_number_glyphs(image)
    normalized_key=normalized[0] if normalized is not None else None
    with QUANTITY_LOCK:
        if normalized_key:
            for existing in _review_index(root).get((kind,normalized_key),[]):
                previous=_load_json_dict(_sample_folder(root,existing)/'record.json')
                old=previous.get('truth') if previous.get('status')=='confirmed' else previous.get('predicted')
                if '불일치' in str(reason) or (value is not None and old is not None and value!=old):continue
                if previous:
                    return _reuse_quantity_record(_sample_folder(root,existing),previous,value,reason,context)
        folder = quantity_root(root) / 'samples' / digest
        record = _load_json_dict(folder / 'record.json')
        if context is not None and not (folder / 'context.png').is_file():
            # Review context is separate from original card/number pixels and labels.
            folder.mkdir(parents=True, exist_ok=True)
            temporary=folder / 'context.tmp'
            context.convert('RGB').save(temporary,format='PNG')
            temporary.replace(folder / 'context.png')
        if record:
            return _reuse_quantity_record(folder,record,value,reason,context)
        folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / 'number.png')
        if card is not None:
            card.convert('RGB').save(folder / 'card.png')
        record = dict(id=digest, kind=kind, predicted=value, reason=str(reason),
                      created=time.time(), status='recognized' if reason.startswith('모델·OCR 일치') else 'pending', truth=None,normalized_key=normalized_key)
        _atomic_json_write(folder / 'record.json', record)
        if normalized_key:_review_index(root).setdefault((kind,normalized_key),[]).append(digest)
        return record


def _sample_folder(root, sample_id):
    if not re.fullmatch('[a-f0-9]{64}', str(sample_id)):
        raise ValueError('잘못된 수량 기록입니다.')
    return quantity_root(root) / 'samples' / sample_id


def quantity_list(root, mode='전체', limit=200):
    with QUANTITY_LOCK:
        records = []
        model=_load_json_dict(quantity_root(root)/'model.json')
        trained={(row.get('sample_id'),row.get('kind'),row.get('value')) for row in model.get('samples',[])}
        for path in (quantity_root(root) / 'samples').glob('*/record.json'):
            record = _load_json_dict(path)
            if record.get('kind') not in QUANTITY_KINDS:
                continue
            if mode == '미확인' and record.get('status') != 'pending':
                continue
            if mode == '확인 완료' and record.get('status') != 'confirmed':
                continue
            record['model_included']=(record.get('id'),record.get('kind'),record.get('truth')) in trained
            records.append(record)
        return sorted(records, key=lambda r:r.get('created', 0), reverse=True)[:limit]


def quantity_confirm(root, sample_id, truth):
    truth = _value(truth)
    with QUANTITY_LOCK:
        folder = _sample_folder(root, sample_id)
        record = _load_json_dict(folder / 'record.json')
        if not record:
            raise ValueError('수량 기록을 찾지 못했습니다.')
        record.pop('model_included',None)
        record.update(status='confirmed', truth=truth, confirmed=time.time())
        _atomic_json_write(folder / 'record.json', record)
        return record


def quantity_train(root,cancel=None):
    """Publish a model only after checking contradictory human labels."""
    with QUANTITY_LOCK:
        samples = []
        records = quantity_list(root, '확인 완료', limit=None)
        digits=[];glyph_labels={};sources=[];numbers={};strip_labels={};normal_keys={}
        for record in records:
            if cancel is not None and cancel.is_set():raise RuntimeError("수량 학습을 중지했습니다.")
            folder = _sample_folder(root, record['id'])
            with Image.open(folder / 'number.png') as image:
                feature = _feature(image)
                if float(feature.std()) < 3:
                    raise ValueError('숫자 획을 확인할 수 없는 이미지가 있습니다.')
                aspect = image.width / image.height
            truth = _value(record['truth'])
            with Image.open(folder/'number.png') as original:normalized=_number_glyphs(original)
            if normalized is not None and len(normalized[1])==len(str(truth)):
                sources.append((record['id'],normalized[1],truth))
                numbers.setdefault(normalized[0],set()).add(truth)
                normal_keys[record['id']]=normalized[0]
                for glyph,digit in zip(normalized[1],str(truth)):
                    encoded_glyph=_encode_glyph(glyph)
                    glyph_labels.setdefault(encoded_glyph,set()).add(digit)

            encoded = base64.b85encode(zlib.compress(feature.tobytes())).decode('ascii')
            identity=(record['kind'],encoded)
            if identity in strip_labels and strip_labels[identity]!=truth:
                raise ValueError('같은 숫자 이미지에 서로 다른 정답이 있습니다. 정답을 다시 확인해 주세요.')
            strip_labels[identity]=truth
            samples.append(dict(kind=record['kind'], value=truth, aspect=aspect,
                                pixels=encoded, sample_id=record['id']))
        if not samples:
            raise ValueError('확인 완료한 수량 정답이 없습니다.')
        # Keep varied approved strips, not only the most recent hundred.
        # Compact glyph templates are deduplicated, balanced across 0..9, and
        # a visually identical glyph with contradictory labels is excluded.
        per_digit={str(i):[] for i in range(10)}
        for encoded,labels in glyph_labels.items():
            if len(labels)==1:per_digit[next(iter(labels))].append(encoded)
        for digit,examples in per_digit.items():
            chosen=[]
            for encoded in examples:
                if len(chosen)>=16:break
                glyph=np.frombuffer(zlib.decompress(base64.b85decode(encoded)),dtype=np.uint8).reshape(32,24)
                if any(float(np.abs(glyph.astype(float)-old).mean()/255)<.008 for old in chosen):continue
                chosen.append(glyph.astype(float));digits.append(dict(digit=digit,pixels=encoded))
        # Exact templates are allowed from explicit human labels. Approximate
        # digit matches must additionally pass independent number-image checks.
        validation=dict(tested=0,wrong=0,accepted=0)
        for cid,glyphs,truth in sources:
            candidate=[row for row in digits if row['pixels'] not in {_encode_glyph(g) for g in glyphs}]
            probe=dict(digits=candidate,allow_digit_approximation=True)
            # Leave out this source's exact glyphs; no augmentation leakage.
            with Image.open(_sample_folder(root,cid)/'number.png') as original:
                value,_=_digit_prediction(probe,original)
            validation['tested']+=1
            if value is not None:
                validation['accepted']+=1
                if value!=truth:validation['wrong']+=1
        if validation['wrong']:
            digits=[]  # Keep existing OCR/approved-strip fallback on any false acceptance.
        model = dict(schema=2, id=uuid.uuid4().hex, trained=time.time(), samples=samples,
                     digits=digits,reviewed=len(records),validation=validation,
                     numbers=[dict(key=key,value=next(iter(values))) for key,values in numbers.items() if len(values)==1],
                     allow_digit_approximation=validation['tested']>=10 and validation['accepted']>=5 and not validation['wrong'])

        if cancel is not None and cancel.is_set():raise RuntimeError('수량 학습을 중지했습니다.')
        folder = quantity_root(root);path = folder / 'model.json'
        previous = _load_json_dict(path)
        if previous:
            _atomic_json_write(folder / 'previous_model.json', previous)
        _atomic_json_write(path, model)
        for record in records:
            if record['id'] in normal_keys:
                record.pop('model_included',None)
                record['normalized_key']=normal_keys[record['id']]
                _atomic_json_write(_sample_folder(root,record['id'])/'record.json',record)
        _REVIEW_INDEX.pop(str(folder.resolve()),None)
        return len(samples)


def quantity_predict(root, kind, image):
    path = quantity_root(root) / 'model.json'
    with QUANTITY_LOCK:
        try:
            stat = path.stat(); key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            return None, 0.0
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = _load_json_dict(path)
            _MODEL_CACHE.clear(); _MODEL_CACHE[key] = model
    if model.get('schema') not in (1,2):
        return None, 0.0
    if model.get('schema')==2:
        value,confidence=_digit_prediction(model,image)
        if value is not None:return value,confidence
    feature = _feature(image).astype(np.float32) / 255
    ranked = []
    for sample in model.get('samples', []):
        if sample.get('kind') != kind:
            continue
        try:
            aspect = float(sample['aspect'])
            if abs(image.width/image.height/aspect - 1) > .025:
                continue
            ref = np.frombuffer(zlib.decompress(base64.b85decode(sample['pixels'])),dtype=np.uint8).reshape(40,96,3).astype(np.float32)/255
            diff = np.abs(feature-ref)
            # A changed digit must not be diluted by the surrounding card art.
            blocks = diff.reshape(5,8,12,8,3).mean(axis=(1,3,4))
            if float(blocks.max()) > .09:
                continue
            ranked.append((float(diff.mean()), _value(sample['value'])))
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            continue
    if not ranked:
        return None, 0.0
    ranked.sort();distance, value = ranked[0]
    alternative = min((d for d,v in ranked if v != value), default=1.0)
    if distance <= .012 and alternative-distance >= .02:
        return value, 1-distance
    return None, max(0.0, 1-distance)


def quantity_resolve(root, kind, image, read, card=None, context=None):
    """Collect successful/failed OCR and reject disagreements with the model."""
    learned, confidence = quantity_predict(root, kind, image)
    reason = 'OCR 판독';value = None
    try:
        value = read()
        if value is not None:
            value = _value(value)
    except ValueError as exc:
        reason = str(exc)
    if learned is not None and value is not None and learned==value:
        reason=f'모델·OCR 일치 ({confidence:.3f})'
    if learned is not None and value is not None and learned != value:
        reason = f'OCR {value} / 학습 이미지 {learned} 불일치'
        value = None
    elif value is None and learned is not None:
        value = learned;reason = f'확인 이미지 학습 판독 ({confidence:.3f})'
    elif value is None:
        reason = '수량 미확인 · '+reason
    quantity_record(root, kind, image, value, reason, card, context=context)
    if value is None:
        raise ValueError(reason)
    return value


def quantity_cleanup_training_data(root,protected_ids=(),cancel=None):
    """Remove only exact duplicate confirmed images after their model was saved.

    Keep unique examples (including rare digits) for the next learning run.
    Pending/unconfirmed records and any unknown files remain untouched.
    """
    with QUANTITY_LOCK:
        model=_load_json_dict(quantity_root(root)/'model.json')
        trained={(row.get('sample_id'),row.get('kind'),row.get('value')) for row in model.get('samples',[])}
        if not trained:raise ValueError('저장된 수량 모델을 확인하지 못해 원본을 정리하지 않았습니다.')
        seen=set();deleted=0;protected=set(protected_ids)
        for record in quantity_list(root,'확인 완료',limit=None):
            if cancel is not None and cancel.is_set():return dict(deleted=deleted,cancelled=True)
            cid=record.get('id');folder=_sample_folder(root,cid)
            if (cid,record.get('kind'),record.get('truth')) not in trained or folder.is_symlink():continue
            image=folder/'number.png'
            if not image.is_file() or image.is_symlink():continue
            identity=(record['kind'],record['truth'],hashlib.sha256(image.read_bytes()).hexdigest())
            if identity not in seen:seen.add(identity);continue
            if cid in protected:continue
            contents=list(folder.iterdir())
            if any(not path.is_file() or path.is_symlink() or path.name not in ('record.json','number.png','card.png','context.png') for path in contents):continue
            for path in contents:path.unlink()
            folder.rmdir();deleted+=1
        _REVIEW_INDEX.pop(str(quantity_root(root).resolve()),None)
        return dict(deleted=deleted,cancelled=False)
