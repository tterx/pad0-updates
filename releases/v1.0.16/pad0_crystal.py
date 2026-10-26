"""Crystal identities, inventory, reward merging and farming decisions.

Independent of the overlay, Tk, OCR engines and Android input. Public functions
return updated JSON-compatible documents without changing the caller's data.
Image routines load NumPy/OpenCV only when called. The application owns disk
commits, capture/input, cancellation and all window operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from types import SimpleNamespace


def _compact_ocr_text(text):
    return re.sub(r"\s+", "", str(text)).lower().replace("：", ":")


def _title_token(text, score):
    # Only the title parser's token protocol is needed, not the GUI OCR type.
    return SimpleNamespace(text=text, score=score, cy=15, x1=0, height=30)


def crystal_detail_name(tokens):
    """Only accept an explicit '…의 크리스탈' title, never a breakthrough owner."""
    rows=[]
    for token in sorted((t for t in tokens if t.score >= .60),key=lambda t:(t.cy,t.x1)):
        row=next((r for r in rows if abs(r[0].cy-token.cy)<max(10,token.height*.6)),None)
        if row is None: rows.append([token])
        else: row.append(token)
    for row in rows:
        text=' '.join(t.text.strip() for t in sorted(row,key=lambda t:t.x1)).strip()
        match=re.fullmatch(r'(.+?)\s*(?:의\s*)?크\s*리\s*스\s*[탈털]',text)
        if match:
            owner=re.sub(r'\s+',' ',match.group(1)).strip()
            if owner and _compact_ocr_text(owner) not in {'의','크리스탈','획득','결과'}:
                return owner+'의 크리스탈'
    return None


def crystal_material_name(name):
    """Database/display identity is the character name, without the crystal suffix."""
    name=re.sub(r'\s+',' ',str(name or '')).strip()
    return re.sub(r'\s*(?:의\s*)?크\s*리\s*스\s*[탈털]$','',name).strip()


def confirmed_detail_readings(readings,known_names=()):
    """Accept exact agreement, or a registered owner's 의/외 particle OCR error.

    Never repair the owner's letters. One explicit correct reading must be
    >=.97 and the corroborating reading >=.85, both with the same full owner
    and exact crystal suffix. An unregistered name cannot use this correction.
    """
    if len(readings)!=2 or min(score for _,score in readings)<.85: return None
    texts=[_compact_ocr_text(text) for text,_ in readings]
    parsed=[crystal_detail_name([_title_token(text,score)]) for text,score in readings]
    # Both crystal suffix spellings are already supported by the parser.
    # Compare the complete owner identity, never repair its Korean letters.
    if all(parsed) and crystal_name_key(parsed[0])==crystal_name_key(parsed[1]):return parsed[0]
    if texts[0]==texts[1]:
        return crystal_detail_name([_title_token(readings[0][0],min(s for _,s in readings))])
    matches=[]
    for name in known_names:
        owner=crystal_material_name(name);key=_compact_ocr_text(owner)
        if not key: continue
        normal=key+'의크리스탈'
        supported={normal,key+'외크리스탈'}
        if (all(text in supported for text in texts)
                and any(text==normal and score>=.97 for text,(_,score) in zip(texts,readings))):
            matches.append(owner)
    matches=list(dict.fromkeys(matches))
    return matches[0]+'의 크리스탈' if len(matches)==1 else None


def single_owner_material_binding(ledger, title, previous, current, now):
    """One confirmed owner + one material + matching explicit title is unambiguous."""
    if not previous or not current: return None
    if not _compact_ocr_text(title).endswith('크리스탈'): return None
    if not previous.get('hwnd') or len(previous.get('game_rect',()))!=4: return None
    if not 0<=now-previous.get('at',-1000)<=300: return None
    key=previous.get('key')
    if ledger.get('active_farming_group')!=key: return None
    group=ledger.get('farming_groups',{}).get(key,{})
    rows=group.get('materials',[])
    if len(rows)!=1 or group.get('revision')!=previous.get('revision'): return None
    if crystal_name_key(title)!=crystal_name_key(group.get('name','')): return None
    if (previous.get('hwnd')!=current.get('hwnd') or
            tuple(previous.get('game_rect',()))!=tuple(current.get('game_rect',()))): return None
    return key,0,rows[0]['id'],group['revision']


def known_crystal_name(entry):
    name=str(entry.get('crystal_name') or entry.get('label') or '').strip()
    if not name or re.fullmatch(r'(?:크리스탈\s*\d+|C\d+)',name): return ''
    return crystal_material_name(name)


def crystal_name_key(name):
    return _compact_ocr_text(crystal_material_name(name))


def link_crystal_monsters(database,settings,monsters):
    """Exact names connect identities; crystal IDs, artwork and counts never change."""
    result=json.loads(json.dumps(database));index={};display={}
    def add(number,name):
        key=crystal_name_key(name)
        if key and key not in ('미확인','확인필요') and not re.fullmatch(r'파티원\d+',key) and str(number).isdigit():index.setdefault(key,set()).add(int(number));display[int(number)]=crystal_material_name(name)
    for row in monsters:add(row['number'],row.get('name_ko',''))
    for number,name in settings.get('monster_korean_names',{}).items():add(number,name)
    for number,entry in settings.get('monster_overrides',{}).items():add(number,entry.get('name',''))
    for entry in settings.get('background_party_profiles',[]):
        if entry.get('number'):add(entry['number'],entry.get('name',''))
    for entry in result.get('crystals',[]):
        if entry.get('monster_number') and entry.get('monster_link_source')!='name_match':continue
        name=known_crystal_name(entry);matches=index.get(crystal_name_key(name),set()) if name else set()
        if len(matches)==1:
            number=next(iter(matches));entry.update(monster_number=number,monster_name_ko=name,monster_link_source='name_match',monster_link_status='linked',monster_link_name_key=crystal_name_key(name))
        else:
            if entry.get('monster_link_source')=='name_match':
                for key in ('monster_number','monster_name_ko','monster_link_source','monster_link_name_key'):entry.pop(key,None)
            entry['monster_link_status']='ambiguous' if len(matches)>1 else 'unmatched' if name else 'unnamed'
    return result


def crystal_display_name(entry, row, index):
    return crystal_material_name(entry.get('crystal_name') or row.get('crystal_name') or entry.get('label')
            or f'크리스탈 {index}')


def register_farming_owner(ledger, name):
    name=str(name or '').strip()[:60]
    if not name: raise ValueError('돌파 대상 이름이 필요합니다.')
    updated=json.loads(json.dumps(ledger))
    groups=updated.setdefault('farming_groups',{})
    key=next((k for k,g in groups.items() if _compact_ocr_text(g['name'])==_compact_ocr_text(name)),
             hashlib.sha256(name.encode('utf-8')).hexdigest()[:24])
    groups.setdefault(key,{'name':name,'revision':uuid.uuid4().hex,'materials':[]})
    updated['active_farming_group']=key
    return updated,key


def name_crystal_material(ledger, database, binding, name, image_file):
    """Reuse the selected icon ID; a verified material title must not rename its owner."""
    name=crystal_material_name(name)
    if not name: raise ValueError('재료 이름을 확인하지 못했습니다.')
    key,index,expected_id,revision=binding
    group=ledger.get('farming_groups',{}).get(key)
    if not group or group['revision']!=revision or not 0<=index<len(group['materials']):
        raise ValueError('재료 구성이 바뀌어 이름 연결을 보류했습니다. 재료를 다시 클릭해 주세요.')
    source=group['materials'][index]
    if source['id']!=expected_id: raise ValueError('선택한 재료가 바뀌었습니다.')
    entries=database.get('crystals',[])
    entry=next((r for r in entries if str(r['id'])==expected_id),None)
    matching=[r for r in entries if crystal_name_key(r.get('crystal_name') or r.get('label',''))==crystal_name_key(name)]
    if entry is not None and not known_crystal_name(entry) and len(matching)==1:
        canonical=str(matching[0]['id'])
    elif entry is not None:
        if matching and any(r['id']!=entry['id'] for r in matching):
            raise ValueError('같은 이름의 다른 이미지가 이미 있습니다. 중복 등록하지 않았습니다.')
        canonical=str(entry['id'])
    elif len(matching)==1:
        entry=matching[0]; canonical=str(entry['id'])
    elif matching:
        raise ValueError('같은 이름의 기존 이미지가 여러 개라 등록을 보류했습니다.')
    else:
        numbers=[int(m.group(1)) for row in entries if (m:=re.search(r'(\d+)$',str(row['id'])))]
        canonical=f'C{max(numbers,default=0)+1:04d}'
    updated=json.loads(json.dumps(ledger)); db=json.loads(json.dumps(database))
    target=next((r for r in db.get('crystals',[]) if str(r['id'])==canonical),None)
    if target is None:
        target={'id':canonical,'image_file':image_file,'first_seen':time.strftime('%Y-%m-%d %H:%M:%S')}
        db.setdefault('crystals',[]).append(target)
    had_image=bool(target.get('image_file'))
    if not had_image: target['image_file']=image_file
    target.update(crystal_name=name,label=name,card_name=name)
    if canonical!=expected_id:
        db['crystals']=[row for row in db['crystals'] if str(row['id'])!=expected_id]
    stocks=updated.setdefault('stocks',{})
    if canonical not in stocks or not had_image:
        stocks[canonical]=int(stocks.get(expected_id,stocks.get(canonical,0)))
    for other in updated['farming_groups'].values():
        changed=False
        for row in other['materials']:
            if row['id']==expected_id or row['id']==canonical:
                row.update(id=canonical,confirmed=True,crystal_name=name,image_file=target['image_file'])
                changed=True
        if crystal_name_key(other['name'])==crystal_name_key(name):
            other.update(crystal_id=canonical,image_file=target['image_file'])
            changed=True
        if changed:
            ids=[r['id'] for r in other['materials']]
            if len(ids)!=len(set(ids)): raise ValueError('한 대상의 서로 다른 재료를 같은 이미지로 합칠 수 없습니다.')
            other['revision']=uuid.uuid4().hex
    if canonical!=expected_id:
        stocks.pop(expected_id,None)
        counts=updated.setdefault('counts',{})
        if expected_id in counts:
            counts[canonical]=max(int(counts.get(canonical,0)),int(counts.pop(expected_id)))
        receipts=[updated.get('reward_session',{})]+updated.get('events',[])
        for receipt in receipts:
            amounts=receipt.get('amounts',{})
            if expected_id in amounts:
                amounts[canonical]=max(int(amounts.get(canonical,0)),int(amounts.pop(expected_id)))
        active=updated.get('active_target')
        if active and str(active.get('id'))==expected_id:
            active.update(id=canonical,label=name)
    return updated,db,canonical


def register_farming_group(ledger, name, materials):
    """One complete F7 snapshot; stocks and group definition share one commit."""
    name = str(name or '').strip()
    if not name or len(name) > 60:
        raise ValueError('돌파 대상 이름을 확인해 주세요.')
    if not 1 <= len(materials) <= 5:
        raise ValueError('재료 전체를 읽어야 등록할 수 있습니다.')
    ids = [str(row['id']) for row in materials]
    if len(set(ids)) != len(ids):
        raise ValueError('서로 다른 재료가 같은 아이콘으로 인식되어 등록을 보류했습니다.')
    updated = json.loads(json.dumps(ledger))
    stocks = updated.setdefault('stocks', {})
    rows = []
    for index, row in enumerate(materials, 1):
        required, owned = int(row['required']), int(row['current'])
        if not (0 <= required <= 999999 and 0 <= owned <= 999999):
            raise ValueError('재료 수량이 올바르지 않습니다.')
        cid = str(row['id'])
        stocks[cid] = owned
        rows.append(dict({'id': cid, 'index': index, 'required': required},
                         **{k:row[k] for k in ('confirmed','crystal_name','image_file') if k in row}))
    groups = updated.setdefault('farming_groups', {})
    key = hashlib.sha256(name.encode('utf-8')).hexdigest()[:24]
    owner_link={k:v for k,v in groups.get(key,{}).items() if k in ('crystal_id','image_file')}
    groups[key] = dict(owner_link,name=name,revision=uuid.uuid4().hex,materials=rows)
    updated['active_farming_group'] = key
    updated['last_confirmed_farming_group'] = key
    return updated, key


def farming_group_rows(ledger, key):
    group = ledger.get('farming_groups', {}).get(key, {})
    stocks = ledger.get('stocks', {})
    awarded = ledger.get('reward_session', {}).get('amounts', {})
    return [dict(row, owned=int(stocks.get(row['id'], 0)),
                 gained=int(awarded.get(row['id'], 0)),
                 deficit=max(0, int(row['required'])-int(stocks.get(row['id'], 0))))
            for row in group.get('materials', [])]


def credit_farming_result(ledger, session_id, amounts, groups_revision):
    """Idempotent receipt and all inventory changes in one atomic JSON document."""
    session = ledger.get('reward_session', {})
    if session.get('id') != session_id or not session.get('open'):
        raise ValueError('전투 결과가 바뀌어 이번 인식을 저장하지 않았습니다.')
    if session.get('credited'):
        return ledger, False
    if ledger.get('farming_groups', {}) != groups_revision:
        raise ValueError('인식 중 재료 등록이 바뀌어 집계를 보류했습니다.')
    known = {row['id'] for group in groups_revision.values() for row in group['materials'] if row.get('confirmed',True)}
    if any(cid not in known or type(amount) is not int or not 0 <= amount <= 999999
           for cid, amount in amounts.items()):
        raise ValueError('확인되지 않은 재료 또는 수량입니다.')
    updated = json.loads(json.dumps(ledger))
    for cid, amount in amounts.items():
        updated.setdefault('stocks', {})[cid] = int(updated.get('stocks', {}).get(cid, 0))+amount
        updated.setdefault('counts', {})[cid] = int(updated.get('counts', {}).get(cid, 0))+amount
    updated['reward_session'].update(credited=True, amounts=amounts,
                                     time=time.strftime('%Y-%m-%d %H:%M:%S'))
    updated.setdefault('events', []).append({'kind': 'farming_result', 'session_id': session_id,
                                            'amounts': amounts, 'time': updated['reward_session']['time']})
    updated['events'] = updated['events'][-300:]
    return updated, True


def reward_viewport(image):
    return image.crop((round(image.width*.29), round(image.height*.245),
                       round(image.width*.705), round(image.height*.763))).convert('L')


def reward_scrollbar_position(image):
    """Return (at top, at bottom) for the orange thumb, or None for no thumb."""
    import numpy as np
    rgb = np.asarray(image.convert('RGB'), dtype=np.int16)
    h, w = rgb.shape[:2]
    lane = rgb[round(h*.244):round(h*.740), round(w*.749):round(w*.759)]
    orange = ((lane[...,0] > 195) & (lane[...,1] >= 65)
              & (lane[...,1] < 190) & (lane[...,2] < 85))
    occupied = np.where(np.mean(orange, axis=1) >= .35)[0]
    if len(occupied) < h*.025:
        return None
    tolerance = max(6, h*.014)
    return bool(occupied[0] <= tolerance), bool(occupied[-1] >= lane.shape[0]-1-tolerance)


def crystal_art_signatures(image):
    """Compare artwork and colored borders, excluding each card's amount row."""
    import numpy as np
    import cv2
    rgb = np.asarray(image.convert('RGB'))
    h, w = rgb.shape[:2]
    signatures = []
    for left, top, right, bottom in ((.08,.06,.92,.76), (.13,.10,.87,.74), (.04,.03,.96,.80)):
        patch = rgb[round(h*top):round(h*bottom), round(w*left):round(w*right)]
        if min(patch.shape[:2]) < 5:
            raise ValueError('재료 아이콘이 너무 작습니다.')
        signatures.append(cv2.cvtColor(cv2.resize(patch, (48,64), interpolation=cv2.INTER_AREA),
                                       cv2.COLOR_RGB2LAB).astype(np.int16))
    return signatures


def reward_template_scroll_delta(previous,current):
    """Measure scroll by unique artwork strips when sparse optical flow fails.

    Multiple independent strips must agree on one displacement; repeated rows
    or a gray viewport never supply an invented movement amount.
    """
    import numpy as np
    import cv2
    a,b=np.asarray(reward_viewport(previous)),np.asarray(reward_viewport(current))
    if a.shape!=b.shape: return None
    height,width=a.shape
    strip=max(32,round(height*.18))
    estimates=[]
    for top in np.linspace(0,height-strip,7).round().astype(int):
        patch=a[top:top+strip]
        if float(patch.std())<10: continue
        scores=cv2.matchTemplate(b,patch,cv2.TM_CCOEFF_NORMED).ravel()
        if not len(scores): continue
        at=int(np.argmax(scores));score=float(scores[at])
        competitors=scores.copy();competitors[max(0,at-5):min(len(scores),at+6)]=-1
        if score<.94 or score-float(competitors.max())<.025: continue
        delta=float(top-at)
        if abs(delta)<=height*.65: estimates.append((delta,score))
    if len(estimates)<2: return None
    cluster=max(([item for item in estimates if abs(item[0]-delta)<=2] for delta,_ in estimates),key=len)
    if len(cluster)<2 or len(cluster)<len(estimates)*.75: return None
    return float(np.median([delta for delta,_ in cluster]))


def reward_scroll_delta(previous, current):
    """Measure actual content translation; never invent a scroll distance."""
    import numpy as np
    import cv2
    a, b = np.asarray(reward_viewport(previous)), np.asarray(reward_viewport(current))
    if a.shape != b.shape:
        raise ValueError('집계 중 게임 창 크기가 바뀌었습니다.')
    if float(np.abs(a.astype(np.int16)-b.astype(np.int16)).mean()) < .7:
        return 0.0
    template_delta=reward_template_scroll_delta(previous,current)
    if template_delta is not None: return template_delta
    points = cv2.goodFeaturesToTrack(a, maxCorners=240, qualityLevel=.03, minDistance=7)
    if points is None or len(points) < 12:
        raise ValueError('스크롤 전후의 겹친 카드를 확인할 수 없습니다.')
    moved, valid, _ = cv2.calcOpticalFlowPyrLK(a, b, points, None, winSize=(25, 25), maxLevel=3)
    if moved is None:
        raise ValueError('스크롤 이동량을 확인할 수 없습니다.')
    returned, reverse_valid, _ = cv2.calcOpticalFlowPyrLK(b, a, moved, None, winSize=(25, 25), maxLevel=3)
    if returned is None:
        raise ValueError('스크롤 겹침 재확인 실패')
    shift = (moved-points).reshape(-1, 2)
    back_error = np.linalg.norm((returned-points).reshape(-1, 2), axis=1)
    good = ((valid.ravel() != 0) & (reverse_valid.ravel() != 0) & (back_error < 1.8)
            & (np.abs(shift[:, 0]) < 2.5) & (np.abs(shift[:, 1]) < previous.height*.28))
    dy = shift[good, 1]
    if len(dy) < 12:
        raise ValueError('스크롤 중 카드 겹침이 부족해 저장하지 않았습니다.')
    median = float(np.median(dy))
    if float(np.mean(np.abs(dy-median) < 2.5)) < .72:
        raise ValueError('카드 이동이 불안정해 집계를 보류했습니다.')
    return -median


class RewardQuantityPending(ValueError):
    """Unread quantities may be resolved by an overlapping scroll view."""
    def __init__(self,items,missing):
        super().__init__('카드 아래 획득 수량을 전부 읽지 못해 집계를 보류했습니다.')
        self.items=items;self.missing=missing


def merge_reward_page(aggregate, page, offset, image_height):
    """Physical card identity is column + measured virtual row, not just artwork."""
    result = list(aggregate)
    for item in page:
        candidate = dict(item, virtual_y=float(item['y'])+offset)
        duplicate = next((old for old in result if abs(old['x']-candidate['x']) < image_height*.035
                          and abs(old['virtual_y']-candidate['virtual_y']) < image_height*.035), None)
        if duplicate is not None:
            if int(duplicate['amount']) != int(candidate['amount']):
                raise ValueError('같은 카드의 획득 수량이 다르게 읽혀 집계를 보류했습니다.')
        else:
            result.append(candidate)
    return result


def background_target_key(ledger):
    """A statistics dropdown must not replace the last complete F7 snapshot."""
    selection=configured_farming_selection(ledger)
    if selection is not None:return selection['key']
    return ledger.get('last_confirmed_farming_group') or ledger.get('active_farming_group')


def configured_farming_selection(ledger):
    """Only an explicit, current material selection enables receipt accounting."""
    selection=ledger.get('farming_selection')
    if not isinstance(selection,dict):return None
    key=selection.get('key');ids=selection.get('material_ids')
    if not isinstance(key,str):return None
    group=ledger.get('farming_groups',{}).get(key)
    if (not group or not isinstance(ids,list) or not ids or any(not isinstance(cid,str) for cid in ids)
            or len(set(ids))!=len(ids)):return None
    if selection.get('revision')!=group.get('revision'):return None
    known={row['id'] for row in group.get('materials',[]) if row.get('confirmed',True)}
    if not set(ids).issubset(known):return None
    return dict(key=key,material_ids=list(ids),revision=group.get('revision'))


def selected_farming_groups(ledger):
    selection=configured_farming_selection(ledger)
    if selection is None:return {}
    key=selection['key'];group=ledger['farming_groups'][key]
    return {key:dict(group,materials=[row for row in group['materials'] if row['id'] in selection['material_ids']])}


def save_farming_selection(ledger,key,requirements,selected):
    updated,chosen=prepare_farming_selection(ledger,key,requirements,selected,require_target=False,require_shortage=False)
    group=updated['farming_groups'][key]
    if any(row['id'] in chosen and not row.get('confirmed',True) for row in group['materials']):
        raise ValueError('선택한 재료의 이름/이미지 등록을 먼저 완료해 주세요.')
    updated['farming_selection']=dict(key=key,material_ids=chosen,revision=group.get('revision'))
    updated['active_farming_group']=key
    return updated,chosen


def background_goal_complete(ledger,key,revision=None,material_ids=None):
    group=ledger.get('farming_groups',{}).get(key)
    if not group or not group.get('materials'):raise RuntimeError('마지막으로 확인한 재료 목록이 없습니다. F7 또는 재료확인을 먼저 사용하세요.')
    if revision is not None and group.get('revision')!=revision:raise RuntimeError('파밍 중 목표 재료가 바뀌었습니다.')
    rows=farming_group_rows(ledger,key)
    if material_ids is not None:
        chosen=set(material_ids)
        if not chosen or not chosen.issubset({row['id'] for row in rows}):raise RuntimeError('파밍할 재료를 다시 선택해 주세요.')
        rows=[row for row in rows if row['id'] in chosen]
    if any(not row.get('confirmed',True) for row in rows):raise RuntimeError('마지막 재료 목록의 이름/이미지 등록을 완료한 뒤 파밍을 시작해 주세요.')
    return all(row['deficit']==0 for row in rows)


def prepare_farming_selection(ledger,key,requirements,selected,require_target=True,require_shortage=True):
    updated=json.loads(json.dumps(ledger));group=updated.get('farming_groups',{}).get(key)
    if not group or require_target and background_target_key(updated)!=key:raise ValueError('돌파 대상이 바뀌었습니다. 창을 다시 열어주세요.')
    ids={row['id'] for row in group['materials']}
    if set(requirements)!=ids or not set(selected).issubset(ids):raise ValueError('재료 목록이 바뀌었습니다.')
    changed=False
    for row in group['materials']:
        value=str(requirements[row['id']]).strip()
        if not re.fullmatch(r'\d{1,6}',value):raise ValueError('필요 수량은 0~999999의 정수로 입력하세요.')
        number=int(value);changed=changed or row['required']!=number;row['required']=number
    chosen=[row['id'] for row in farming_group_rows(updated,key) if row['id'] in selected and (not require_shortage or row['deficit']>0)]
    if require_shortage and not chosen:raise ValueError('부족한 재료를 하나 이상 선택해 주세요.')
    if changed:group['revision']=uuid.uuid4().hex
    return updated,chosen


def farming_stage_number(text, full=False):
    import unicodedata
    text=unicodedata.normalize('NFKC',str(text))
    text = str(text).strip()
    numeric = r'[★☆]?\s*(\d{1,2})\s*[-－–ー]\s*(\d{1,2})'
    match = re.fullmatch(numeric, text) if full else re.search(numeric, text)
    if match: return '-'.join(match.groups())
    special = r'(?<![A-Za-z0-9])S\s*P\s*(\d{1,3})(?![A-Za-z0-9])'
    match = re.search(special, text, re.I)
    if match and (not full or not re.search(r'[A-Za-z0-9가-힣]', text[:match.start()]+text[match.end():])):
        return 'SP'+str(int(match.group(1)))
    return None


def joined_stage_tokens(tokens):
    """Join adjacent SP/number OCR pieces without guessing missing digits."""
    from types import SimpleNamespace
    result=list(tokens)
    ordered=sorted(tokens,key=lambda token:(token.cy,token.x1))
    for first in ordered:
        text=re.sub(r'\s+','',str(first.text)).upper()
        if text not in ('S','SP') or first.score<.9:continue
        last=first;parts=[first]
        for _ in range(2):
            height=max(first.y2-first.y1,last.y2-last.y1)
            nearby=[token for token in ordered if token.x1>=last.x2-2
                    and token.x1-last.x2<=height*1.2 and abs(token.cy-first.cy)<=height*.3
                    and token.score>=.9 and token not in parts
                    and re.fullmatch('P' if text=='S' else r'\d{1,3}',re.sub(r'\s+','',str(token.text)).upper())]
            if len(nearby)!=1:break
            last=nearby[0];parts.append(last)
            text+=re.sub(r'\s+','',str(last.text)).upper()
            if re.fullmatch(r'SP\d{1,3}',text):
                result.append(SimpleNamespace(text=text,score=min(token.score for token in parts),
                    x1=first.x1,x2=last.x2,y1=min(token.y1 for token in parts),y2=max(token.y2 for token in parts),
                    cx=(first.x1+last.x2)/2,cy=(min(token.y1 for token in parts)+max(token.y2 for token in parts))/2))
                break
            if text!='SP':break
    return result


def stage_list_medals(frame):
    """Visible gold buttons in the dungeon list, in reading order.

    These are anchors for reading the label below a portrait, not evidence of
    any particular stage number. Never infer a number from a medal or the DB.
    """
    import numpy as np
    import cv2
    rgb=np.asarray(frame.convert('RGB'));top=220;bottom=min(650,frame.height)
    if bottom<=top:return []
    patch=rgb[top:bottom,70:min(1530,frame.width)]
    if not patch.size:return []
    mask=((patch[...,0]>205)&(patch[...,1]>125)&(patch[...,1]<225)&(patch[...,2]<100)).astype(np.uint8)
    _,_,stats,centers=cv2.connectedComponentsWithStats(mask,8)
    medals=[]
    for index,(x,y,w,h,area) in enumerate(stats[1:],1):
        if 45<=w<=140 and 15<=h<=55 and 1.7<w/h<6 and area>350:
            cx,cy=centers[index];medals.append((70+float(cx),top+float(cy)))
    return sorted(medals,key=lambda point:(round(point[1]/60),point[0]))


def stage_list_entries(frame,tokens,database=None):
    import numpy as np
    import cv2
    entries=[];rgb=np.asarray(frame.convert('RGB'))
    for token in joined_stage_tokens(tokens):
        number=farming_stage_number(token.text,full=True)
        if not number or token.score<.9 or not 300<token.cy<850:continue
        x0=max(0,int(token.cx-100));x1=min(frame.width,int(token.cx+100))
        y0=max(40,int(token.cy-260));y1=max(y0+1,int(token.cy-150))
        patch=rgb[y0:y1,x0:x1]
        mask=((patch[...,0]>205)&(patch[...,1]>125)&(patch[...,1]<225)&(patch[...,2]<100)).astype(np.uint8)
        count,labels,stats,centers=cv2.connectedComponentsWithStats(mask,8)
        medals=[]
        for index,(x,y,w,h,area) in enumerate(stats[1:],1):
            if 45<=w<=140 and 15<=h<=55 and 1.7<w/h<6 and area>350:
                cx,cy=centers[index];medals.append((abs(x0+cx-token.cx),x0+cx,y0+cy))
        registered=bool((database or {}).get('stages',{}).get(number,{}).get('preview_verified'))
        if not medals and not registered:raise RuntimeError('스테이지 '+ number+'의 훈장 버튼을 확인하지 못했습니다.')
        medal=min(medals) if medals else None
        entries.append(dict(number=number,token=token,medal=(medal[1],medal[2]) if medal else None))
    if len({entry['number'] for entry in entries})!=len(entries):raise RuntimeError('스테이지 번호가 중복 판독되었습니다.')
    return entries


def stage_has_shortage(database,number,deficits):
    row=database.get('stages',{}).get(number,{})
    return bool(row.get('preview_verified') and any(drop.get('id') is not None and deficits.get(str(drop['id']),0)>0 for drop in row.get('drops',[])))


def choose_farming_stage(database,deficits,available=None):
    allowed=set(available) if available is not None else None
    ranks=[]
    for number,row in database.get('stages',{}).items():
        if allowed is not None and number not in allowed:continue
        if not row.get('preview_verified'):continue
        ids={str(drop.get('id')) for drop in row.get('drops',[]) if drop.get('id') is not None}
        wanted={cid for cid,amount in deficits.items() if amount>0}
        overlap=ids&wanted
        if overlap:ranks.append((-len(overlap),int(row.get('battles') or 999),number))
    return min(ranks)[2] if ranks else None
