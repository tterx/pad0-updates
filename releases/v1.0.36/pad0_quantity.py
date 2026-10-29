"""Runtime red/white quantity OCR and legacy development-only review utilities.

The application calls read_material_number without reading or saving local
training data. The older supervised utilities are retained for developer tools.
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


def material_number_line(image, *, multiplier=False):
    """Isolate a complete red/white quantity line; never learn from user pixels.

    The caller supplies a badge interior or the full-width bottom of one card.
    Separate colour masks keep crystal highlights out of red shortage numbers.
    All digits must have a common baseline, a dark surround and intact edges.
    """
    import cv2
    rgb = np.asarray(image.convert('RGB'), dtype=np.int16)
    h, w = rgb.shape[:2]
    if min(h, w) < 8:
        raise ValueError('수량 영역이 너무 작습니다.')
    # Fire-card orange facets are not red shortage text. Their clipped
    # borders must not veto an otherwise complete red multiplication line.
    masks = [((rgb[..., 0] >= 125) & (rgb[..., 0] > rgb[..., 1]+40)
              & (rgb[..., 0] > rgb[..., 2]+30)
              & (np.abs(rgb[..., 1]-rgb[..., 2]) <= 65)),
             ((rgb.min(axis=2) >= 155) & (rgb.max(axis=2)-rgb.min(axis=2) < 65))]
    lines = []
    for mask in masks:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype('uint8'), 8)
        boxes = []; rejected = []; clipped = []
        for index in range(1, count):
            x, y, bw, bh, area = map(int, stats[index])
            if area >= 4 and (x == 0 or x+bw == w or y == 0 or y+bh == h):
                clipped.append((x, y, bw, bh, index))
            if bh < (max(5, h*.12) if multiplier else max(7, h*.25)) or bw < 2 or area < 8:
                continue
            component = (labels == index).astype('uint8')
            ring = (cv2.dilate(component, np.ones((3, 3), dtype='uint8')) > 0) & (component == 0)
            if not ring.any() or float((rgb.min(axis=2)[ring] < 155).mean()) < .55:
                rejected.append((x, y, bw, bh, index))
                continue
            boxes.append((x, y, bw, bh, index))
        for anchor in boxes:
            _, ay, _, ah, _ = anchor
            line = sorted([b for b in boxes if abs(b[1]+b[3]-(ay+ah)) <= max(3, ah*(.30 if multiplier else .22))
                           and .55*ah <= b[3] <= 1.35*ah])
            if multiplier:
                # Anchor owned counts on the visible multiplication sign;
                # crystal facets to its left are not leading digits.
                marks=[i for i,(x,y,bw,bh,index) in enumerate(line)
                       if _is_quantity_multiplier((labels[y:y+bh,x:x+bw]==index).astype('uint8'))]
                if len(marks)!=1:continue
                line=line[marks[0]:]
            if not 1 <= len(line) <= (4 if multiplier else 3):
                continue
            height = max(b[3] for b in line)
            # Top-clipped portrait artwork can overlap only the glyph's top.
            # Real horizontally clipped digits still veto the complete line.
            if any(.3*height <= b[3] <= 1.4*height
                   and (b[0]==0 or b[0]+b[2]==w
                        or abs(b[1]+b[3]-(ay+ah)) <= max(3,height*.30))
                   and b[1] < ay+ah and b[1]+b[3] > ay
                   and b[0]+b[2] >= line[0][0]-height*.65
                   and b[0] <= line[-1][0]+line[-1][2]+height*.65 for b in clipped):
                continue
            if any(.8*height <= b[3] <= 1.3*height
                   and abs(b[1]+b[3]-(ay+ah)) <= max(3, height*.22)
                   and b[0]+b[2] >= line[0][0]-height*.65
                   and b[0] <= line[-1][0]+line[-1][2]+height*.65 for b in rejected):
                continue
            if any(b[0] <= 0 or b[1] <= 0 or b[0]+b[2] >= w or b[1]+b[3] >= h for b in line):
                continue
            if any(line[i+1][0]-line[i][0]-line[i][2] > height*.65 for i in range(len(line)-1)):
                continue
            glyphs = [(labels[y:y+bh, x:x+bw] == index).astype('uint8')
                      for x, y, bw, bh, index in line]
            has_mark = multiplier and len(line) >= 2 and _is_quantity_multiplier(glyphs[0])
            digits = [part for glyph in (glyphs[1:] if has_mark else glyphs)
                      for part in _split_quantity_digits(glyph)]
            if not 1 <= len(digits) <= 3 or any(g.shape[0] < height*.80 for g in digits):
                continue
            # A stock line must start with the visible multiplication mark.
            # This prevents accepting a clipped hundreds digit as a smaller stock.
            if multiplier and not has_mark:
                continue
            selected = np.isin(labels, [b[4] for b in (line[1:] if has_mark else line)])
            yy, xx = np.where(selected)
            ink = selected[yy.min():yy.max()+1, xx.min():xx.max()+1].astype('uint8')
            lines.append((ink, digits))
    if not lines:
        raise ValueError('흰색/빨간색 수량의 전체 숫자 획을 확인하지 못했습니다.')
    # Duplicate anchors yield the same line; distinct plausible lines are ambiguous.
    unique = {(row[0].shape, row[0].tobytes()): row for row in lines}
    if len(unique) != 1:
        raise ValueError('수량 영역에 여러 숫자 줄이 있어 확정하지 못했습니다.')
    return next(iter(unique.values()))


def _split_quantity_digits(glyph):
    """Separate thin bridges (111 or 54); never cut a zero's two-sided ring."""
    h, w = glyph.shape
    if w <= h*.95:
        return [glyph]
    bridges = []
    for x in range(2, w-2):
        ys = np.flatnonzero(glyph[:, x])
        if 0 < len(ys) <= max(1, round(h*.18)) and ys[-1]-ys[0]+1 == len(ys):
            bridges.append(x)
    runs = []
    for x in bridges:
        if runs and x == runs[-1][-1]+1:runs[-1].append(x)
        else:runs.append([x])
    candidates = [run[len(run)//2] for run in runs]
    cuts = [0]+[x for x in candidates if np.count_nonzero(glyph[:, :x].any(axis=1)) >= h*.8
                and np.count_nonzero(glyph[:, x:].any(axis=1)) >= h*.8]+[w]
    parts = [glyph[:, a:b] for a, b in zip(cuts, cuts[1:])]
    if len(parts) <= 1 or any(part.shape[1] < 2 or part.shape[1] > h*.95
                              or np.count_nonzero(part.any(axis=1)) < h*.8 for part in parts):
        return [glyph]
    return parts


def _is_quantity_multiplier(glyph):
    """Identify × by its diagonal strokes, not merely a square bounding box."""
    h, w = glyph.shape
    if not .65 <= w/h <= 1.4:
        return False
    yy, xx = np.where(glyph)
    x = xx/max(1, w-1); y = yy/max(1, h-1)
    diagonal = np.minimum(abs(x-y), abs(x+y-1))
    return len(xx) >= 8 and float((diagonal < .23).mean()) >= .88


def material_number_views(ink, height=64):
    import cv2
    h, w = ink.shape
    scaled = cv2.resize(255-ink*255, (max(1, round(w*height/h)), height), interpolation=cv2.INTER_NEAREST)
    padded = cv2.copyMakeBorder(scaled, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    return [Image.fromarray(a).convert('RGB') for a in (padded, cv2.GaussianBlur(padded, (3, 3), .6))]


def read_material_number(image, recognize_number, *, multiplier=False):
    """Read 0..999 with digit-count proof and two agreeing OCR views.

    A complete number is preferred. Single-digit recognition is a fallback for
    OCR that cannot read a short line; contrary confident readings never vanish.
    Required amounts are range-checked separately by the registration caller.
    """
    ink, digits = material_number_line(image, multiplier=multiplier)
    readings = [recognize_number(view) for view in material_number_views(ink)]
    def valid(row, count, threshold=.85):
        value, score, text = row
        return (type(value) is int and 0 <= value <= 999 and score >= threshold
                and re.fullmatch(r'[0-9]{'+str(count)+r'}', str(text).strip()) is not None
                and str(value) == str(text).strip())
    accepted = [r[0] for r in readings if valid(r, len(digits))]
    if len(accepted) == 2 and len(set(accepted)) == 1:
        return accepted[0], '흰색/빨간색 숫자 분리 · 자릿수와 두 영상 일치'
    if len(set(accepted)) > 1:
        raise ValueError('수량 OCR 결과가 서로 다릅니다.')
    # Small, jagged zeroes can be less legible when over-enlarged. A second
    # scale must agree with both weak readings and still meet normal confidence.
    if all(valid(r, len(digits), .65) for r in readings) and readings[0][0] == readings[1][0]:
        alternate = [recognize_number(view) for view in material_number_views(ink, height=32)]
        if all(valid(r, len(digits)) and r[0] == readings[0][0] for r in alternate):
            return alternate[0][0], '작은 숫자 재확대 · 자릿수와 네 영상 일치'
        if any(valid(r, len(digits)) and r[0] != readings[0][0] for r in alternate):
            raise ValueError('수량 확대 배율별 판독이 서로 다릅니다.')
    def recover_small(glyph, primary):
        # A tiny3 can become a Hangul-like block when enlarged to64px.
        # Keep all contrary confident observations; never prefer a new guess.
        confident=[r[0] for r in primary if valid(r,1)]
        for height in (32,24):
            pair=[recognize_number(view) for view in material_number_views(glyph,height=height)]
            values=[r[0] for r in pair if valid(r,1)]
            if len(set(confident+values))>1:
                raise ValueError('수량 확대 배율별 판독이 서로 다릅니다.')
            confident+=values
            if len(values)==2 and values[0]==values[1]:return values[0]
        raise ValueError('수량의 각 자릿수를 확정하지 못했습니다.')
    if len(digits)==1:
        return recover_small(digits[0],readings),'작은 숫자32/24px · 자릿수와 두 영상 일치'
    numbers = []
    for digit in digits:
        pair = [recognize_number(view) for view in material_number_views(digit)]
        if not all(valid(row, 1) for row in pair) or pair[0][0] != pair[1][0]:
            if len({row[0] for row in pair if valid(row,1)})>1:raise ValueError('수량 OCR 결과가 서로 다릅니다.')
            numbers.append(str(recover_small(digit,pair)))
        else:numbers.append(str(pair[0][0]))
    text = ''.join(numbers)
    if len(text) > 1 and text[0] == '0':
        raise ValueError('수량 앞자리를 확정하지 못했습니다.')
    value = int(text)
    if accepted and any(old != value for old in accepted):
        raise ValueError('수량 전체와 자릿수 판독이 서로 다릅니다.')
    return value, '흰색/빨간색 각 자릿수 두 영상 일치'


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
