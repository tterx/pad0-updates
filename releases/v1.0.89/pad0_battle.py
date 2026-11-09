"""Visible defeat acknowledgement before returning to the existing result flow."""
from __future__ import annotations
import hashlib
import re
import time
import uuid
import numpy as np
from PIL import Image
from pad0_common import _atomic_json_write
from pad0_crystal import credit_farming_result, farming_group_rows, background_target_key


def defeat_dialog_regions(frame):
    """Find the pale TIPS header/body pair and its separate white button."""
    if frame.size!=(1600,940):return None
    import cv2
    rgb=np.asarray(frame.convert('RGB'));area=rgb[200:800,250:1350]
    pale=(area.min(2)>210)&(np.ptp(area.astype('int16'),axis=2)<22)
    _,_,stats,_=cv2.connectedComponentsWithStats(pale.astype('uint8'),8)
    plates=[tuple(map(int,(x+250,y+200,w,h,n))) for x,y,w,h,n in stats[1:]
            if 600<=w<=1000 and n>.85*w*h]
    pairs=[(a,b) for a in plates for b in plates
           if 30<=a[3]<=80 and 140<=b[3]<=330 and 35<=b[1]-a[1]-a[3]<=140
           and abs(a[0]-b[0])<=6 and abs(a[2]-b[2])<=12]
    if len(pairs)!=1:return None
    (x,y,w,h,_),(bx,by,bw,bh,_)=pairs[0]
    if not 280<=y<=420:return None
    body=rgb[by:by+bh,bx:bx+bw]
    white=(body.min(2)>=245)&(np.ptp(body.astype('int16'),axis=2)<18)
    _,_,buttons,_=cv2.connectedComponentsWithStats(white.astype('uint8'),8)
    boxes=[]
    for xx,yy,ww,hh,n in buttons[1:]:
        if (270<=ww<=500 and 40<=hh<=105 and n>.70*ww*hh
                and abs(xx+ww/2-bw/2)<bw*.10 and yy>bh*.40 and yy+hh<bh):
            boxes.append(tuple(map(int,(bx+xx,by+yy,bx+xx+ww,by+yy+hh))))
    return dict(header=(round(x+w*.36),y,round(x+w*.64),y+h),
                panel=(x,y,bx+bw,by+bh),button=boxes[0] if len(boxes)==1 else None)


def defeat_hp_view(frame):
    """Lift even dim red HP digits behind the defeat scrim, never infer zero."""
    import cv2
    rgb=np.asarray(frame.crop((580,875,855,930)).convert('RGB'),dtype='int16')
    red=(rgb[:,:,0]>15)&(rgb[:,:,0]>rgb[:,:,1]*1.6)&(rgb[:,:,0]>rgb[:,:,2]*1.5)
    yy,xx=np.where(red)
    if len(xx)<20:return None
    ink=255-red[yy.min():yy.max()+1,xx.min():xx.max()+1].astype('uint8')*255
    if ink.shape[0]<10 or ink.shape[1]<40:return None
    ink=cv2.copyMakeBorder(ink,6,6,10,10,cv2.BORDER_CONSTANT,value=255)
    return Image.fromarray(ink).convert('RGB')


class DefeatRecovery:
    def __init__(self,guard,line_views):
        self.guard=guard;self.line_views=line_views
        self.reset()

    def reset(self):
        self.since=None;self.sent=None;self.previous=None
        self.confirmed=False;self.cache={};self.decision={}
        self.saw_crystals=False;self.mana_session=None;self.mana_frame=None
        self.lobby_previous=None;self.lobby_since=None

    def step(self,app,device,frame):
        self.guard(app)
        if app._background_cancel.is_set():raise RuntimeError('패배 처리를 중지했습니다.')
        regions=defeat_dialog_regions(frame)
        if regions is None:
            self.previous=None;self.since=None
            return False
        now=time.monotonic()
        if self.since is None:self.since=now
        decision=dict(regions=regions,confirmed=self.confirmed,action='waiting')
        self.decision=decision
        snapshot=getattr(app,'_farming_error_snapshot',None)
        if not isinstance(snapshot,dict):snapshot={};app._farming_error_snapshot=snapshot
        snapshot.update(phase='background_defeat',failure_image=frame.copy(),defeat_recovery=decision)
        if self.sent is not None:
            decision['action']='waiting_for_result'
            decision.update(readings=next(iter(self.cache.values())),readings_scope='confirmed_before_ok')
            if now-self.sent>8:raise RuntimeError('패배 OK 입력 후 화면이 바뀌지 않아 추가 입력 없이 중지했습니다.')
            return True
        if now-self.since>10:raise RuntimeError('패배 안내의 TIPS·HP 0·OK를 확인하지 못해 입력 없이 중지했습니다.')
        hp=defeat_hp_view(frame);box=regions['button']
        if hp is None or box is None:return True
        x,y,right,bottom=box;w=right-x;h=bottom-y
        button=frame.crop((round(x+w*.32),round(y+h*.08),round(x+w*.68),round(bottom-h*.08)))
        title=frame.crop(regions['header'])
        key=hashlib.sha256(title.tobytes()+button.tobytes()+hp.tobytes()).digest()
        proof=self.cache.get(key)
        if proof is None:
            def read(crop,tight=False):
                views=self.line_views(crop)[1] if tight else [crop.resize((crop.width*s,crop.height*s),Image.Resampling.LANCZOS) for s in (2,3)]
                return [tuple(app._recognize_title_line(view)) for view in views]
            proof=dict(title=read(title,True),button=read(button,True),hp=read(hp))
            self.cache={key:proof}
        decision['readings']=proof
        exact=lambda rows,text:len(rows)==2 and all(.90<=score<=1 and re.sub(r'\s','',value).upper()==text for value,score in rows)
        numbers=[re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*',text) if .90<=score<=1 else None for text,score in proof['hp']]
        values=[tuple(map(int,m.groups())) if m else None for m in numbers]
        valid=exact(proof['title'],'TIPS') and exact(proof['button'],'OK') and len(values)==2 and values[0] is not None and values[0]==values[1] and values[0][0]==0 and values[0][1]>0
        pixels=np.asarray(frame.crop(regions['panel']).resize((64,28)).convert('RGB'),dtype='int16')
        stable=(valid and self.previous is not None and self.previous[0]==box
                and float(np.abs(self.previous[1]-pixels).mean())<=3)
        self.previous=(box,pixels) if valid else None
        decision.update(proven=bool(valid),stable=bool(stable))
        if not stable:return True
        self.guard(app)
        if app._background_cancel.is_set():raise RuntimeError('패배 처리를 중지했습니다.')
        device.tap((x+right)/2,(y+bottom)/2)
        self.sent=time.monotonic();self.confirmed=True
        decision.update(action='ok_sent',confirmed=True)
        diagnostics=getattr(app,'_background_diagnostics',None)
        if isinstance(diagnostics,dict):diagnostics['defeat_count']=diagnostics.get('defeat_count',0)+1
        app._background_queue.put(('status','패배 확인 · OK 전달 · 결과·크리스탈 집계 후 연전 재도전 · F10 중지'))
        return True

    def ensure_session(self,app,session,material_mode):
        if session is not None:return session
        self.guard(app)
        session=uuid.uuid4().hex
        if material_mode:
            with app._crystal_db_lock:
                ledger=app._load_crystal_ledger()
                ledger['reward_session']=dict(id=session,open=True,credited=False,amounts={},source='background_defeat')
                _atomic_json_write(app.data_paths['ledger'],ledger)
        return session

    def note_mana_next(self,session,receipt,frame):
        if self.confirmed and receipt.get('source')=='result_mana' and receipt.get('amount_basis')=='displayed_final':
            self.mana_session=session;self.mana_frame=frame.copy()

    def empty_result(self,app,frame,session,key,groups):
        """Only an owned defeat + acknowledged mana Next + stable lobby proves this no-crystal exit."""
        self.guard(app)
        if app._background_cancel.is_set():raise RuntimeError('패배 결과 처리를 중지했습니다.')
        if not self.confirmed or not session or self.mana_session!=session or self.saw_crystals:return None
        if self.lobby_since is None:self.lobby_since=time.monotonic()
        if time.monotonic()-self.lobby_since>10:raise RuntimeError('패배 후 연전 확인 버튼을 확정하지 못해 입력 없이 중지했습니다.')
        point=app._background_replay_button(frame,False)
        if point is None:self.lobby_previous=None;return False
        pixels=np.asarray(frame.crop((350,600,1250,850)).resize((90,25)).convert('RGB'),dtype='int16')
        stable=(self.lobby_previous is not None and max(abs(a-b) for a,b in zip(point,self.lobby_previous[0]))<=3
                and float(np.abs(pixels-self.lobby_previous[1]).mean())<=3)
        self.lobby_previous=(point,pixels)
        if not stable:return False
        proof=dict(source='defeat_mana_to_lobby_without_crystal_page',session_id=session,defeat_confirmed=True,
                   mana_next_sent=True,crystal_page_seen=False,stable_lobby=True,amounts={})
        snapshot=getattr(app,'_farming_error_snapshot',None)
        if not isinstance(snapshot,dict):snapshot={};app._farming_error_snapshot=snapshot
        snapshot.update(phase='background_defeat_result',defeat_result=proof)
        if key is not None:
            self.guard(app)
            if app._background_cancel.is_set():raise RuntimeError('패배 결과 처리를 중지했습니다.')
            with app._crystal_db_lock:
                ledger=app._load_crystal_ledger()
                if background_target_key(ledger)!=key:raise RuntimeError('집계 중 목표 재료가 바뀌었습니다.')
                ledger,changed=credit_farming_result(ledger,session,{},groups,diagnostics=proof)
                if changed:_atomic_json_write(app.data_paths['ledger'],ledger)
            selected=getattr(app,'_background_selected_material_ids',None)
            rows=[r for r in farming_group_rows(ledger,key) if selected is None or r['id'] in selected]
            snapshot.update(receipt_amounts={},receipt_inventory=rows,receipt_trace=proof,
                receipt_image=self.mana_frame.copy(),receipt_page_kind='mana_only_defeat',receipt_session=session)
            app._background_last_receipt={k:v for k,v in snapshot.items() if k.startswith('receipt_')}
            app._background_queue.put(('credited',{}))
        return True


def read_mana_result(frame,tokens,recognize,line_views,compact):
    rgb=np.asarray(frame.convert('RGB'))
    cap=rgb[170:220,460:1140]
    white_cap=bool(cap.size and float((cap.min(axis=2)>225).mean())>.65)
    title=any(t.score>=.85 and compact(t.text)=='획득' and 160<t.cy<245 for t in tokens)
    if not title and white_cap:
        crop=frame.crop((730,165,870,225))
        _,views=line_views(crop)
        if len(views)!=2:views=[crop.resize((crop.width*k,crop.height*k),Image.Resampling.LANCZOS) for k in (2,3)]
        readings=[recognize(view) for view in views]
        title=all(compact(text)=='획득' and score>=.90 for text,score in readings)
    if not title:return None
    result=dict(amount=None,multiplier=None,source='result_mana')
    if not white_cap:return result
    def read_number(box,pattern):
        crop=frame.crop(box);values=[]
        if box==(760,270,940,355):
            pixels=np.asarray(crop);ink=((pixels[...,0]>180)&(pixels[...,1]>65)&(pixels[...,1]<215)&(pixels[...,2]<110))
            ys,xs=np.where(ink)
            if len(xs)>15:crop=crop.crop((max(0,int(xs.min())-4),max(0,int(ys.min())-4),min(crop.width,int(xs.max())+5),min(crop.height,int(ys.max())+5)))
        for k in (2,3):
            text,score=recognize(crop.resize((crop.width*k,crop.height*k),Image.Resampling.LANCZOS))
            match=re.fullmatch(pattern,compact(text).replace(',',''))
            values.append(int(match[1]) if match and score>=.92 else None)
        return values[0] if values[0] is not None and values[0]==values[1] else None
    result['amount']=read_number((760,270,940,355),r'(\d{1,9})')
    # The number is already the credited amount, even beside an ×2 badge.
    # The badge is decorative for accounting; do not spend two OCR calls on it.
    result['amount_basis']='displayed_final'
    return result
