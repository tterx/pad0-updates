"""Local supervised quantity-image classifier and review store; no UI/devices.

Only explicitly confirmed examples enter the model. This conservative nearest
example classifier recognises learned number strips, not unseen number strings.
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


def quantity_record(root, kind, image, value=None, reason='', card=None, context=None):
    if kind not in QUANTITY_KINDS:
        raise ValueError('알 수 없는 수량 구분입니다.')
    image = image.convert('RGB')
    if image.width < 4 or image.height < 4:
        return None
    if value is not None:
        value = _value(value)
    digest = hashlib.sha256(kind.encode()+str(image.size).encode()+image.tobytes()).hexdigest()
    folder = quantity_root(root) / 'samples' / digest
    with QUANTITY_LOCK:
        record = _load_json_dict(folder / 'record.json')
        if context is not None and not (folder / 'context.png').is_file():
            # Review context is separate from original card/number pixels and labels.
            folder.mkdir(parents=True, exist_ok=True)
            temporary=folder / 'context.tmp'
            context.convert('RGB').save(temporary,format='PNG')
            temporary.replace(folder / 'context.png')
        if record:
            return record  # Preserve the original prediction and human feedback.
        folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / 'number.png')
        if card is not None:
            card.convert('RGB').save(folder / 'card.png')
        record = dict(id=digest, kind=kind, predicted=value, reason=str(reason),
                      created=time.time(), status='pending', truth=None)
        _atomic_json_write(folder / 'record.json', record)
        return record


def _sample_folder(root, sample_id):
    if not re.fullmatch('[a-f0-9]{64}', str(sample_id)):
        raise ValueError('잘못된 수량 기록입니다.')
    return quantity_root(root) / 'samples' / sample_id


def quantity_list(root, mode='전체', limit=200):
    with QUANTITY_LOCK:
        records = []
        for path in (quantity_root(root) / 'samples').glob('*/record.json'):
            record = _load_json_dict(path)
            if record.get('kind') not in QUANTITY_KINDS:
                continue
            if mode == '미확인' and record.get('status') != 'pending':
                continue
            if mode == '확인 완료' and record.get('status') != 'confirmed':
                continue
            records.append(record)
        return sorted(records, key=lambda r:r.get('created', 0), reverse=True)[:limit]


def quantity_confirm(root, sample_id, truth):
    truth = _value(truth)
    with QUANTITY_LOCK:
        folder = _sample_folder(root, sample_id)
        record = _load_json_dict(folder / 'record.json')
        if not record:
            raise ValueError('수량 기록을 찾지 못했습니다.')
        record.update(status='confirmed', truth=truth, confirmed=time.time())
        _atomic_json_write(folder / 'record.json', record)
        return record


def quantity_train(root):
    """Publish a model only after checking contradictory human labels."""
    with QUANTITY_LOCK:
        samples = []
        records = quantity_list(root, '확인 완료', limit=100)
        for record in records:
            folder = _sample_folder(root, record['id'])
            with Image.open(folder / 'number.png') as image:
                feature = _feature(image)
                if float(feature.std()) < 3:
                    raise ValueError('숫자 획을 확인할 수 없는 이미지가 있습니다.')
                aspect = image.width / image.height
            truth = _value(record['truth'])
            encoded = base64.b85encode(zlib.compress(feature.tobytes())).decode('ascii')
            for sample in samples:
                if sample['kind'] == record['kind'] and sample['pixels'] == encoded and sample['value'] != truth:
                    raise ValueError('같은 숫자 이미지에 서로 다른 정답이 있습니다. 정답을 다시 확인해 주세요.')
            samples.append(dict(kind=record['kind'], value=truth, aspect=aspect,
                                pixels=encoded, sample_id=record['id']))
        if not samples:
            raise ValueError('확인 완료한 수량 정답이 없습니다.')
        model = dict(schema=1, id=uuid.uuid4().hex, trained=time.time(), samples=samples)
        folder = quantity_root(root);path = folder / 'model.json'
        previous = _load_json_dict(path)
        if previous:
            _atomic_json_write(folder / 'previous_model.json', previous)
        _atomic_json_write(path, model)
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
    if model.get('schema') != 1:
        return None, 0.0
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
