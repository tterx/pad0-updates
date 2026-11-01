"""Puzzle evaluation and route search, independent of GUI, OCR and input.

The caller supplies board/goals/spikes/cancellation and owns capture, condition
recognition, input, UI and saved settings. Search budgets, caches, seed behavior,
certified existing-orb cascades and orthogonal movement policy are unchanged.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
from itertools import combinations
import math
import os
import random
import sys
import time
from typing import Optional

from pad0_common import ROWS, COLS, COLOR_ORDER, PRIMARY_ATTRIBUTES, combo_signature

import numpy as np





SOLVER_MAX_MOVES = None

SOLVER_TIME_BUDGET = 5.0

SOLVER_BEAM_WIDTH = 850

FIVE_ATTR_TIME_BUDGET = 3.0  # Fail promptly; enemy requirements remain mandatory.

FIVE_ATTR_BEAM_WIDTH = 180

FIVE_ATTR_DEFAULT_MOVE_CAP = 78

PRESEARCH_WAIT_LIMIT = 3.0  # Total waiting, including pauses; parent enforces the same ceiling.

def five_attribute_inventory(board):
    """Count normal attack orbs before launching expensive 5-attribute search."""
    return {key: sum(orb == key for orb in board) for key in ("R", "B", "G", "Y", "P")}

def five_attribute_possible_by_count(board):
    return all(count >= 3 for count in five_attribute_inventory(board).values())

def inventory_route_goals(board, requested):
    """Explicit calculation on an impossible five-color inventory uses combos.

    No hard 10-combo target is added; preserve every other mode unchanged.
    """
    goals=tuple(bool(v) for v in requested[:7])+tuple(requested[7:])
    fallback=(len(board)==ROWS*COLS and all(v in COLOR_ORDER for v in board)
              and len(goals)>2 and goals[2] and sum(goals[:7])==1 and not master_multicolor(goals)
              and not five_attribute_possible_by_count(board))
    return ((False,)*len(goals) if fallback else goals),bool(fallback)

_BATCH_POPCOUNT=np.array([i.bit_count() for i in range(256)],np.uint8)

_BATCH_LEFT=np.uint32(0x01041041);_BATCH_RIGHT=_BATCH_LEFT<<np.uint32(5);_BATCH_HSTART=np.uint32(sum(1<<(r*COLS+c) for r in range(ROWS) for c in range(COLS-2)));_BATCH_HPAIR=np.uint32(sum(1<<(r*COLS+c) for r in range(ROWS) for c in range(COLS-1)))

_BATCH_ADJ=np.full((30,4),-1,np.int16)

for i in range(30):
 n=[j for j in (i-COLS,i+COLS,i-1,i+1) if 0<=j<30 and abs(i//COLS-j//COLS)+abs(i%COLS-j%COLS)==1];_BATCH_ADJ[i,:len(n)]=n

def _batch_popcount(b):
 """Portable vector popcount; does not require NumPy 2 bitwise_count."""
 return _BATCH_POPCOUNT[np.ascontiguousarray(b).view(np.uint8).reshape(b.shape+(4,))].sum(axis=-1,dtype=np.int16)

def _batch_match_features(bits):
 """Exact orthogonal match masks and connected combo counts for a batch."""
 h=bits&(bits>>np.uint32(1))&(bits>>np.uint32(2))&_BATCH_HSTART;v=bits&(bits>>np.uint32(6))&(bits>>np.uint32(12))
 marked=h|(h<<np.uint32(1))|(h<<np.uint32(2))|v|(v<<np.uint32(6))|(v<<np.uint32(12))
 remaining=marked.copy();groups=np.zeros(bits.shape,np.int16)
 while remaining.any():
  frontier=remaining&(~remaining+np.uint32(1));groups+=(frontier!=0);remaining^=frontier
  while frontier.any():
   spread=((frontier&~_BATCH_RIGHT)<<np.uint32(1))|((frontier&~_BATCH_LEFT)>>np.uint32(1))|(frontier<<np.uint32(6))|(frontier>>np.uint32(6))
   frontier=spread&remaining;remaining^=frontier
 return marked,groups

def _batch_known_gravity_features(boards,marked):
 """First known gravity wave only; return its distinct colour count/mask.

 Unknown top cells are 255 and cannot produce matches. Distinct colours give
 a safe combo lower bound even when arbitrary refill merges same-colour groups.
 """
 cleared=np.bitwise_or.reduce(marked,axis=1)
 live=(cleared[:,None]&(np.uint32(1)<<np.arange(30,dtype=np.uint32)))==0
 known=np.full(boards.shape,255,np.uint8)
 for col in range(6):
  present=live[:,col::6]
  destinations=5-np.cumsum(present[:,::-1],axis=1)[:,::-1]
  rows,source=np.nonzero(present)
  known[rows,destinations[rows,source]*6+col]=boards[rows,source*6+col]
 powers=np.uint32(1)<<np.arange(30,dtype=np.uint32)
 masks=np.stack([np.sum((known==c)*powers,axis=1,dtype=np.uint32) for c in range(8)],axis=1)
 found=((masks&(masks>>np.uint32(1))&(masks>>np.uint32(2))&_BATCH_HSTART)|(masks&(masks>>np.uint32(6))&(masks>>np.uint32(12))))!=0
 return found.sum(axis=1),((found[:,:5])*(1<<np.arange(5))).sum(axis=1)

def _batch_three_distance(b):
 """Vector equivalent of the scalar three-orb Manhattan completion distance."""
 positions=[];b=b.copy()
 for _ in range(3):
  low=b&(~b+np.uint32(1));positions.append(np.log2(low).astype(np.int16));b^=low
 pos=np.stack(positions,axis=1);r=pos//6;c=np.sort(pos%6,axis=1)
 a=np.sort(c-np.array([0,1,2]),axis=1)[:,1].clip(0,3)
 horiz=r[:,2]-r[:,0]+np.abs(c-a[:,None]-np.array([0,1,2])).sum(axis=1)
 a=np.sort(r-np.array([0,1,2]),axis=1)[:,1].clip(0,2)
 vert=c[:,2]-c[:,0]+np.abs(r-a[:,None]-np.array([0,1,2])).sum(axis=1)
 return np.minimum(horiz,vert)

def _solve_master_batch_round(board,budget,cap,width,seed,mode,required_total,combo_bonus,cancel_event=None,require_heal=False):
 """Evaluate candidates in arrays and reserve a beam quota per held colour.

 A rare carried orb must not crowd out common-colour routes needed to arrange
 several separate groups. Full replay and a one-wave lower bound gate success.
 """
 deadline=time.perf_counter()+budget;encoded=np.array([COLOR_ORDER.index(c) for c in board],np.uint8)
 rare=[i for i in range(5) if board.count(COLOR_ORDER[i])==3]
 mask=np.array([sum(1<<i for i,v in enumerate(encoded) if v==c) for c in range(8)],np.uint32)
 boards=np.repeat(encoded[None,:],30,axis=0);masks=np.repeat(mask[None,:],30,axis=0);cursor=np.arange(30,dtype=np.int16);previous=np.full(30,-1,np.int16)
 paths=np.full((30,cap+1),255,np.uint8);paths[:,0]=cursor;best=[0];bestscore=-1e9
 weights=np.random.default_rng(seed).uniform(.7,1.4,8)
 for depth in range(1,cap+1):
  if time.perf_counter()>deadline or cancel_event is not None and cancel_event.is_set():break
  parent=np.repeat(np.arange(len(cursor)),4);nxt=_BATCH_ADJ[cursor].reshape(-1)
  valid=(nxt>=0)&(nxt!=np.repeat(previous,4));parent=parent[valid];nxt=nxt[valid];old=cursor[parent]
  nb=boards[parent].copy();held=nb[np.arange(len(nxt)),old].copy();displaced=nb[np.arange(len(nxt)),nxt].copy()
  nb[np.arange(len(nxt)),old]=displaced;nb[np.arange(len(nxt)),nxt]=held
  nm=masks[parent].copy();swap=(np.uint32(1)<<old.astype(np.uint32))|(np.uint32(1)<<nxt.astype(np.uint32))
  nm[np.arange(len(nxt)),held]^=swap;nm[np.arange(len(nxt)),displaced]^=swap
  # Deduplicate equal boards/cursor regardless of prior edge.
  _,ix=np.unique(np.column_stack([nb,nxt.astype(np.uint8)]),axis=0,return_index=True)
  nb=nb[ix];nm=nm[ix];old=old[ix];nxt=nxt[ix];parent=parent[ix]
  marked,groups=_batch_match_features(nm);count=groups.sum(axis=1);attrmask=((marked[:,:5]!=0)*(1<<np.arange(5))).sum(axis=1);attrs=_batch_popcount(attrmask.astype(np.uint32))
  next_count,next_attrmask=_batch_known_gravity_features(nb,marked)
  final_count=count+next_count;final_attrs=_batch_popcount((attrmask|next_attrmask).astype(np.uint32))
  needed=max(6,required_total)-combo_bonus
  good=np.flatnonzero((final_attrs==5)&(final_count>=needed))
  good=good[np.argsort(-(final_attrs[good]*10+final_count[good]))]
  for i in good[:120]:
   if time.perf_counter()>deadline or cancel_event is not None and cancel_event.is_set():break
   after=[COLOR_ORDER[int(x)] for x in nb[i]]
   goals=combo_condition_goals(recovery_route_goals(master_multicolor_goals(),require_heal),required_total,combo_bonus)
   if board_goal_metrics(after,goals)['all_met']:
    path=list(map(int,paths[parent[i],:depth]))+[int(nxt[i])]
    # Replay is authoritative; batched bookkeeping alone cannot arm input.
    if board_goal_metrics(board_after_drag(board,path),goals)['all_met']:return path,depth
  # Pair/gap completion and excess matched cells, including hearts.
  loose=nm&~marked
  near=_batch_popcount(loose&(loose>>np.uint32(1))&_BATCH_HPAIR)+_batch_popcount(loose&(loose>>np.uint32(2))&_BATCH_HSTART)+_batch_popcount(loose&(loose>>np.uint32(6)))+_batch_popcount(loose&(loose>>np.uint32(12)))
  potential=(near*weights).sum(axis=1)
  distances=np.zeros(len(nxt),float)
  for c in rare:distances+=_batch_three_distance(nm[:,c])*weights[c]
  excess=(_batch_popcount(marked)-groups*3).sum(axis=1)
  if mode==0:score=final_attrs*25+final_count*17+potential*.8-distances*3-excess*2
  elif mode==1:score=final_attrs*15+final_count*12+potential*.7-distances*4-excess*3
  else:score=final_attrs*20+final_count*20+potential*.6-distances*2-excess*4
  if require_heal:score+=(groups[:,5]>0)*20-_batch_three_distance(nm[:,5])*2
  order=np.argsort(-score,kind='stable')
  held_codes=nb[np.arange(len(nb)),nxt]
  # Preserve routes with different carried colours AND different matched
  # attribute sets. Pure global ranking quickly loses all four-colour setups
  # whose fifth colour will match only after gravity.
  chosen=[];seen=set();quota=max(1,width//50);buckets={}
  for i in order:
   key=int(held_codes[i])*32+int(attrmask[i]);buckets[key]=buckets.get(key,0)+1
   if buckets[key]<=quota:chosen.append(int(i));seen.add(int(i))
   if len(chosen)>=width:break
  for i in order:
   if len(chosen)>=width:break
   if int(i) not in seen:chosen.append(int(i));seen.add(int(i))
  top=order[0]
  if score[top]>bestscore:
   bestscore=float(score[top]);best=list(map(int,paths[parent[top],:depth]))+[int(nxt[top])]
  chosen=np.array(chosen,np.int32);boards=nb[chosen];masks=nm[chosen];previous=old[chosen];cursor=nxt[chosen]
  paths=paths[parent[chosen]].copy();paths[:,depth]=cursor
 return best,None

def _solve_master_direct_batch_round(board,budget,cap,width,seed,mode,required_total,combo_bonus,cancel_event=None,require_heal=False):
 """Direct-match beam retained for dense eight-combo layouts.

 A rare carried orb must not crowd out common-colour routes needed to arrange
 several separate groups. Full replay and a one-wave lower bound gate success.
 """
 deadline=time.perf_counter()+budget;encoded=np.array([COLOR_ORDER.index(c) for c in board],np.uint8)
 rare=[i for i in range(5) if board.count(COLOR_ORDER[i])==3]
 mask=np.array([sum(1<<i for i,v in enumerate(encoded) if v==c) for c in range(8)],np.uint32)
 boards=np.repeat(encoded[None,:],30,axis=0);masks=np.repeat(mask[None,:],30,axis=0);cursor=np.arange(30,dtype=np.int16);previous=np.full(30,-1,np.int16)
 paths=np.full((30,cap+1),255,np.uint8);paths[:,0]=cursor;best=[0];bestscore=-1e9
 weights=np.random.default_rng(seed).uniform(.8,1.2,8)
 for depth in range(1,cap+1):
  if time.perf_counter()>deadline or cancel_event is not None and cancel_event.is_set():break
  parent=np.repeat(np.arange(len(cursor)),4);nxt=_BATCH_ADJ[cursor].reshape(-1)
  valid=(nxt>=0)&(nxt!=np.repeat(previous,4));parent=parent[valid];nxt=nxt[valid];old=cursor[parent]
  nb=boards[parent].copy();held=nb[np.arange(len(nxt)),old].copy();displaced=nb[np.arange(len(nxt)),nxt].copy()
  nb[np.arange(len(nxt)),old]=displaced;nb[np.arange(len(nxt)),nxt]=held
  nm=masks[parent].copy();swap=(np.uint32(1)<<old.astype(np.uint32))|(np.uint32(1)<<nxt.astype(np.uint32))
  nm[np.arange(len(nxt)),held]^=swap;nm[np.arange(len(nxt)),displaced]^=swap
  # Deduplicate equal boards/cursor regardless of prior edge.
  _,ix=np.unique(np.column_stack([nb,nxt.astype(np.uint8)]),axis=0,return_index=True)
  nb=nb[ix];nm=nm[ix];old=old[ix];nxt=nxt[ix];parent=parent[ix]
  marked,groups=_batch_match_features(nm);count=groups.sum(axis=1);attrmask=((marked[:,:5]!=0)*(1<<np.arange(5))).sum(axis=1);attrs=_batch_popcount(attrmask.astype(np.uint32))
  needed=max(6,required_total)-combo_bonus
  good=np.flatnonzero((attrs>=2)&(count>=max(3,needed-5)))
  good=good[np.argsort(-(attrs[good]*10+count[good]))]
  for i in good[:120]:
   if time.perf_counter()>deadline or cancel_event is not None and cancel_event.is_set():break
   after=[COLOR_ORDER[int(x)] for x in nb[i]]
   goals=combo_condition_goals(recovery_route_goals(master_multicolor_goals(),require_heal),required_total,combo_bonus)
   if board_goal_metrics(after,goals)['all_met']:
    path=list(map(int,paths[parent[i],:depth]))+[int(nxt[i])]
    # Replay is authoritative; batched bookkeeping alone cannot arm input.
    if board_goal_metrics(board_after_drag(board,path),goals)['all_met']:return path,depth
  # Pair/gap completion and excess matched cells, including hearts.
  loose=nm&~marked
  near=_batch_popcount(loose&(loose>>np.uint32(1))&_BATCH_HPAIR)+_batch_popcount(loose&(loose>>np.uint32(2))&_BATCH_HSTART)+_batch_popcount(loose&(loose>>np.uint32(6)))+_batch_popcount(loose&(loose>>np.uint32(12)))
  potential=(near*weights).sum(axis=1)
  distances=np.zeros(len(nxt),float)
  for c in rare:distances+=_batch_three_distance(nm[:,c])*weights[c]
  excess=(_batch_popcount(marked)-groups*3).sum(axis=1)
  if mode==0:score=attrs*25+count*16+potential-distances*3-excess*2
  elif mode==1:score=attrs*15+count*12+potential*.7-distances*4-excess*3
  else:score=attrs*20+count*20+potential*.6-distances*2-excess*4
  if require_heal:score+=(groups[:,5]>0)*20-_batch_three_distance(nm[:,5])*2
  order=np.argsort(-score,kind='stable')
  held_codes=nb[np.arange(len(nb)),nxt]
  chosen=[];seen=set()
  kinds=np.unique(held_codes)
  for kind in kinds:
   ranked=order[held_codes[order]==kind]
   lead=ranked[:width//len(kinds)//2]
   selected=list(map(int,lead));used=set(selected)
   bucket=attrmask[ranked]*300+count[ranked]*30+nxt[ranked]
   _,unique=np.unique(bucket,return_index=True)
   for i in ranked[np.sort(unique)]:
    if int(i) not in used:selected.append(int(i));used.add(int(i))
    if len(selected)>=width//len(kinds):break
   if len(selected)<width//len(kinds):
    for i in ranked:
     if int(i) not in used:selected.append(int(i));used.add(int(i))
     if len(selected)>=width//len(kinds):break
   chosen+=selected;seen.update(selected)
  for i in order:
   if len(chosen)>=width:break
   if int(i) not in seen:chosen.append(int(i));seen.add(int(i))
  top=order[0]
  if score[top]>bestscore:
   bestscore=float(score[top]);best=list(map(int,paths[parent[top],:depth]))+[int(nxt[top])]
  chosen=np.array(chosen,np.int32);boards=nb[chosen];masks=nm[chosen];previous=old[chosen];cursor=nxt[chosen]
  paths=paths[parent[chosen]].copy();paths[:,depth]=cursor
 return best,None

def solve_master_batch(board,time_budget,max_moves,required_total,combo_bonus=0,cancel_event=None,seed_offset=0,require_heal=False):
    """Bounded diverse restarts for high master combo requirements, never input."""
    max_moves=max(0,min(65,int(max_moves))) if max_moves is not None else 65
    started=time.perf_counter();best=[0];goals=combo_condition_goals(recovery_route_goals(master_multicolor_goals(),require_heal),required_total,combo_bonus)
    best_metrics=board_goal_metrics(board,goals)
    upper=sum(board.count(color)//3 for color in COLOR_ORDER)+combo_bonus
    if best_metrics['all_met'] or max_moves==0 or upper<max(6,required_total) or require_heal and board.count('H')<3:return best,best_metrics['combos'],best_metrics['matched'],list(board)
    attempt=0
    while time.perf_counter()-started<time_budget and not (cancel_event is not None and cancel_event.is_set()):
        remaining=max(0,time_budget-(time.perf_counter()-started))
        # Dense eight-combo layouts retain the direct beam first; alternate
        # with gravity-aware ranking when that beam cannot satisfy the goal.
        run=_solve_master_direct_batch_round if max(6,required_total)-combo_bonus>=8 and attempt%2==0 else _solve_master_batch_round
        path,_=run(board,remaining,max_moves,700,seed_offset+attempt,
            0 if attempt<4 else (attempt-4)%3,required_total,combo_bonus,cancel_event,**(dict(require_heal=True) if require_heal else {}))
        after=board_after_drag(board,path);metrics=board_goal_metrics(after,goals)
        if metrics['all_met']:return path,metrics['combos'],metrics['matched'],after
        if (metrics['attr_count'],metrics['final_combos'])>(best_metrics['attr_count'],best_metrics['final_combos']):
            best=path;best_metrics=metrics
        attempt+=1
    after=board_after_drag(board,best)
    return best,best_metrics['combos'],best_metrics['matched'],after

def solve_master_attack(board,time_budget,max_moves,spikes=None,avoid_skull=True,required_total=6,combo_bonus=0,cancel_event=None,seed_offset=0,require_heal=False,prefer_heal=False):
    if require_heal and prefer_heal:
        started=time.perf_counter()
        result=solve_master_attack(board,time_budget*.55,max_moves,spikes,avoid_skull,required_total,combo_bonus,cancel_event,seed_offset,require_heal=True)
        requested=combo_condition_goals(recovery_route_goals(master_multicolor_goals()),required_total,combo_bonus)
        if board_goal_metrics(result[3],requested)['all_met'] or cancel_event is not None and cancel_event.is_set():return result
        remaining=max(0.0,time_budget-(time.perf_counter()-started))
        return solve_master_attack(board,remaining,max_moves,spikes,avoid_skull,required_total,combo_bonus,cancel_event,seed_offset)
    if require_heal:
        if not any(spikes or ()) and 'S' not in board:
            return solve_master_batch(board,time_budget,max_moves,required_total,combo_bonus,cancel_event,seed_offset,require_heal=True)
        goals=combo_condition_goals(recovery_route_goals(master_multicolor_goals()),required_total,combo_bonus)
        return solve_path(board,time_budget=time_budget,max_moves=max_moves,goals=goals,spikes=spikes,avoid_skull=avoid_skull,seed_offset=seed_offset,cancel_event=cancel_event)
    if max(6,int(required_total))-int(combo_bonus)>=7 and not any(spikes or ()) and 'S' not in board:
        return solve_master_batch(board,time_budget,max_moves,required_total,combo_bonus,cancel_event,seed_offset)
    if max(6,int(required_total))-int(combo_bonus)==6 and not any(spikes or ()) and 'S' not in board:
        started=time.perf_counter()
        fast=solve_master_batch(board,min(1.0,max(0,float(time_budget))*.4),max_moves,required_total,combo_bonus,cancel_event,seed_offset)
        goals=combo_condition_goals(master_multicolor_goals(),required_total,combo_bonus)
        if verified_prefind_route(board,fast[0],goals,max_moves):return fast
        time_budget=max(0,float(time_budget)-(time.perf_counter()-started))
        if time_budget<=0 or cancel_event is not None and cancel_event.is_set():return fast
    started=time.perf_counter();budget=max(0,float(time_budget))
    goals=combo_condition_goals(master_multicolor_goals(),required_total,combo_bonus)
    if seed_offset%2:
        return solve_five_attributes_v101(board,time_budget=budget,max_moves=max_moves,
            spikes=spikes,avoid_skull=avoid_skull,required_total=required_total,combo_bonus=combo_bonus,
            cancel_event=cancel_event,allow_diagonal=False,cluster_hint=True,
            seed_offset=seed_offset,beam_width=180,transport_hint=True)
    first=solve_five_attributes_v101(board,time_budget=min(.25,budget*.15),max_moves=max_moves,
        spikes=spikes,avoid_skull=avoid_skull,required_total=required_total,combo_bonus=combo_bonus,
        cancel_event=cancel_event,cluster_hint=True,seed_offset=seed_offset)
    if verified_prefind_route(board,first[0],goals,max_moves):return first
    remaining=max(0,budget*.5-(time.perf_counter()-started))
    if remaining<=0 or cancel_event is not None and cancel_event.is_set():return first
    second=solve_five_attributes_v101(board,time_budget=remaining,max_moves=max_moves,
        spikes=spikes,avoid_skull=avoid_skull,required_total=required_total,combo_bonus=combo_bonus,
        cancel_event=cancel_event,allow_diagonal=False,cluster_hint=True,seed_offset=seed_offset)
    if verified_prefind_route(board,second[0],goals,max_moves):return second
    results=[first,second]
    remaining=max(0,budget-(time.perf_counter()-started))
    if remaining>0 and not (cancel_event is not None and cancel_event.is_set()):
        third=solve_five_attributes_v101(board,time_budget=remaining,max_moves=max_moves,
            spikes=spikes,avoid_skull=avoid_skull,required_total=required_total,combo_bonus=combo_bonus,
            cancel_event=cancel_event,allow_diagonal=False,cluster_hint=True,seed_offset=seed_offset,
            beam_width=180,transport_hint=True)
        if verified_prefind_route(board,third[0],goals,max_moves):return third
        results.append(third)
    def rank(result):
        metrics=board_goal_metrics(result[3],goals)
        return metrics['attr_count'],metrics['final_combos'],-route_move_units(result[0])
    return max(results,key=rank)

def apply_path_to_spikes(spikes: list[bool], path: list[int]) -> list[bool]:
    """Move thorn flags with their underlying orb along the same swap path."""
    out = list(spikes or [False] * (ROWS * COLS))
    if len(out) != ROWS * COLS:
        out = (out + [False] * (ROWS * COLS))[:ROWS * COLS]
    for cur, nxt in zip(path, path[1:]):
        out[cur], out[nxt] = out[nxt], out[cur]
    return out

def spike_route_metrics(board: list[str], spikes: list[bool], path: list[int]) -> tuple[int, int]:
    """Return (spiked orbs removed by matches, estimated thorn cells crossed)."""
    current_board = list(board)
    current_spikes = list(spikes or [False] * (ROWS * COLS))
    if len(current_spikes) != ROWS * COLS:
        current_spikes = (current_spikes + [False] * (ROWS * COLS))[:ROWS * COLS]
    crossings = 0
    for cur, nxt in zip(path, path[1:]):
        if current_spikes[nxt]:
            crossings += 1
        current_board[cur], current_board[nxt] = current_board[nxt], current_board[cur]
        current_spikes[cur], current_spikes[nxt] = current_spikes[nxt], current_spikes[cur]
    _, _, groups = combo_details(current_board)
    matched_positions = set().union(*groups) if groups else set()
    cleared = sum(1 for i in matched_positions if current_spikes[i])
    return int(cleared), int(crossings)

def neighbors(idx: int) -> list[int]:
    r, c = divmod(idx, COLS)
    out = []
    if r > 0: out.append(idx - COLS)
    if r < ROWS - 1: out.append(idx + COLS)
    if c > 0: out.append(idx - 1)
    if c < COLS - 1: out.append(idx + 1)
    return out

def movement_neighbors(index, diagonal=False):
    out=neighbors(index)
    if diagonal:
        row,col=divmod(index,COLS)
        out += [r*COLS+c for r in (row-1,row+1) for c in (col-1,col+1)
                if 0<=r<ROWS and 0<=c<COLS]
    return out

def manual_route_supported(path):
    """MuMu manual playback accepts orthogonal edges only."""
    return (isinstance(path,(tuple,list)) and bool(path)
            and all(type(cell) is int and 0<=cell<ROWS*COLS for cell in path)
            and all(b in neighbors(a) for a,b in zip(path,path[1:])))

def route_move_units(path):
    return sum(math.hypot(a//COLS-b//COLS,a%COLS-b%COLS) for a,b in zip(path,path[1:]))

def board_after_drag(board: list[str], path: list[int]) -> list[str]:
    """Simulate a drag: the selected orb is carried, and each crossed orb shifts back."""
    out = list(board)
    if not path:
        return out
    held = board[path[0]]
    for i in range(1, len(path)):
        cur, nxt = path[i - 1], path[i]
        displaced = out[nxt]
        out[cur] = displaced
        out[nxt] = held
    return out

def combo_details(board: list[str]) -> tuple[int, int, list[set[int]]]:
    """Return combo count, matched-cell count and groups on a 5x6 board.

    All recognized classes are eligible for 3+ matches, including Recovery (H),
    Jammer (J), and Skull/Poison (S). S still counts as a combo if matched, but
    its matched-cell count is exposed to the solver as a hazard cost.
    """
    marked: set[int] = set()
    for r in range(ROWS):
        c = 0
        while c < COLS:
            start = c
            color = board[r * COLS + c]
            while c < COLS and board[r * COLS + c] == color:
                c += 1
            if c - start >= 3:
                for cc in range(start, c): marked.add(r * COLS + cc)
    for c in range(COLS):
        r = 0
        while r < ROWS:
            start = r
            color = board[r * COLS + c]
            while r < ROWS and board[r * COLS + c] == color:
                r += 1
            if r - start >= 3:
                for rr in range(start, r): marked.add(rr * COLS + c)
    # Merge orthogonally adjacent matched cells of the same color into a single combo.
    groups: list[set[int]] = []
    unseen = set(marked)
    while unseen:
        seed = unseen.pop()
        group = {seed}
        stack = [seed]
        while stack:
            cur = stack.pop()
            for nxt in neighbors(cur):
                if nxt in unseen and board[nxt] == board[cur]:
                    unseen.remove(nxt)
                    group.add(nxt)
                    stack.append(nxt)
        groups.append(group)
    groups.sort(key=lambda g: min(g))
    return len(groups), len(marked), groups

def near_match_bonus(board: list[str]) -> int:
    """Small tie-breaker: reward lines of two identical orbs with a matching third nearby."""
    bonus = 0
    # Reward pairs that can be completed by the third cell in a straight 3-cell window.
    for r in range(ROWS):
        for c in range(COLS - 2):
            trip = [board[r * COLS + c + k] for k in range(3)]
            if trip[0] == trip[1] == trip[2]: bonus += 2
            elif trip[0] == trip[1] or trip[1] == trip[2] or trip[0] == trip[2]: bonus += 1
    for c in range(COLS):
        for r in range(ROWS - 2):
            trip = [board[(r + k) * COLS + c] for k in range(3)]
            if trip[0] == trip[1] == trip[2]: bonus += 2
            elif trip[0] == trip[1] or trip[1] == trip[2] or trip[0] == trip[2]: bonus += 1
    return bonus

def score_path(board: list[str], path: list[int]) -> tuple[int, int, int, list[str]]:
    after = board_after_drag(board, path)
    combos, matched, _ = combo_details(after)
    score = combos * 1000 + matched * 18 + near_match_bonus(after)
    return score, combos, matched, after

def random_path(moves: int) -> list[int]:
    """Generate a self-avoiding path so the on-screen instructions stay easy to follow."""
    for _attempt in range(80):
        p = [random.randrange(ROWS * COLS)]
        for _ in range(moves):
            opts = [n for n in neighbors(p[-1]) if n not in p]
            if not opts:
                break
            p.append(random.choice(opts))
        if len(p) == moves + 1:
            return p
    # A legal fallback; most boards obtain a simple path long before this is needed.
    p = [random.randrange(ROWS * COLS)]
    for _ in range(moves):
        p.append(random.choice(neighbors(p[-1])))
    return p

def mutate_path(path: list[int]) -> list[int]:
    new = list(path)
    if len(new) < 2:
        return random_path(12)
    moves = len(new) - 1
    for _attempt in range(5):
        candidate = list(new)
        k = random.randrange(0, len(candidate) - 1)
        if k == 0:
            candidate[0] = random.randrange(ROWS * COLS)
        else:
            opts = [n for n in neighbors(candidate[k - 1]) if n not in candidate[:k]]
            if not opts:
                continue
            candidate[k] = random.choice(opts)
        valid = True
        for i in range(k + 1, len(candidate)):
            opts = [n for n in neighbors(candidate[i - 1]) if n not in candidate[:i]]
            if not opts:
                valid = False
                break
            candidate[i] = random.choice(opts)
        if valid:
            return candidate
    return random_path(moves)

@lru_cache(maxsize=3)
def shape_match_templates(mode):
    """Exact same-colour components; border orbs must not join the shape."""
    shapes = set()
    if mode == 4:
        triples = [frozenset(r*6+c+k for k in range(3)) for r in range(5) for c in range(4)]
        triples += [frozenset((r+k)*6+c for k in range(3)) for r in range(3) for c in range(6)]
        for triple in triples:
            for cell in triple:
                for extra in neighbors(cell):
                    if extra not in triple:
                        shapes.add(triple | {extra})
    elif mode == 5:
        for r in range(1, 4):
            for c in range(1, 5):
                i = r*6+c
                shapes.add(frozenset((i, i-1, i+1, i-6, i+6)))
    elif mode == 6:
        for r in range(5):
            for c in range(6):
                for dr in (-1, 1):
                    for dc in (-1, 1):
                        if 0 <= r+2*dr < 5 and 0 <= c+2*dc < 6:
                            shapes.add(frozenset((r*6+c, (r+dr)*6+c, (r+2*dr)*6+c,
                                                  r*6+c+dc, r*6+c+2*dc)))
    result = []
    for cells in sorted(shapes, key=lambda cells: sorted(cells)):
        border = {n for i in cells for n in neighbors(i)} - cells
        result.append((sum(1 << i for i in cells), sum(1 << i for i in border)))
    return tuple(result)

def tpa_route_goals(attributes, combo_mask=0):
    mask=sum(1<<i for i,c in enumerate(('R','B','G','Y','P')) if c in set(attributes))
    return (False,False,False,False,True,False,False,6,0,False,False,False,False,mask,int(combo_mask))


def tpa_goal_attributes(goals):
    if len(goals)<=13:return set(PRIMARY_ATTRIBUTES)
    return {c for i,c in enumerate(('R','B','G','Y','P')) if int(goals[13])&(1<<i)}


def shape_goal_metrics(board, mode, attributes=None, groups=None):
    templates = shape_match_templates(mode)
    count, progress = 0, 0.0
    for color in (PRIMARY_ATTRIBUTES if attributes is None else attributes):
        bits = sum(1 << i for i, value in enumerate(board) if value == color)
        for mask, border in templates:
            filled = (bits & mask).bit_count()
            extra = (bits & border).bit_count()
            complete = filled == mask.bit_count() and extra == 0
            count += int(complete)
            # A correct shape beats a nearly formed shape with extra touching orbs.
            progress = max(progress, 1.0 if complete else
                           max(0.0, (filled - .25*extra) / mask.bit_count()) * .99)
    if mode==4:
        eligible=PRIMARY_ATTRIBUTES if attributes is None else set(attributes)
        count=sum(len(group)==4 and board[min(group)] in eligible for group in (combo_details(board)[2] if groups is None else groups))
    return count, progress

def master_multicolor(goals):
    return len(goals)>9 and bool(goals[9])

def linked_clear_mode(goals):
    return len(goals)>12 and bool(goals[12])


def linked_clear_goals(required=6,bonus=0):
    return (False,True,False,False,False,False,False,max(6,int(required)),max(0,int(bonus)),False,False,False,True)


def board_clear_mode(goals):
    return len(goals)>11 and bool(goals[11])


def board_clear_goals():
    """Explicit turn-progress mode: no attack, healing or combo floor."""
    return (False,)*7+(0,0,False,False,True)


def master_survival(goals):
    return len(goals)>10 and bool(goals[10])

def master_survival_goals(required=6,bonus=0):
    return (False,)*7+(6,max(0,int(bonus)),False,True)

def master_multicolor_goals():
    return (False,False,True,False,False,False,False,6,0,True)


def fallback_operation_seconds(seconds):
    """A bounded input budget, explicitly not a fabricated timer reading."""
    return 5.0 if seconds is None else max(0.0,float(seconds))


def solve_master_fallback(board,time_budget,max_moves,spikes=None,avoid_skull=False,combo_bonus=0,cancel_event=None,seed_offset=0,prefer_heal=False):
    """Ignore the four-second reserve: rainbow/six first, then six only."""
    started=time.perf_counter();budget=max(0.0,float(time_budget))
    attack=combo_condition_goals((False,False,True,False,False,False,False),6,combo_bonus)
    if five_attribute_possible_by_count(board):
        result=solve_master_attack(board,budget*.60,max_moves,spikes,avoid_skull,6,combo_bonus,cancel_event,seed_offset,
            require_heal=bool(prefer_heal),prefer_heal=bool(prefer_heal))
        after=board_after_drag(board,result[0]);metrics=board_goal_metrics(after,attack)
        if metrics['all_met']:
            execution=recovery_route_goals(attack,bool(prefer_heal and metrics['recovery_matched']>=3))
            return (result[0],metrics['combos'],metrics['matched'],after),execution,'five_six'
        if cancel_event is not None and cancel_event.is_set():return result,attack,'five_six'
    six=master_survival_goals(6,combo_bonus)
    remaining=max(0.0,budget-(time.perf_counter()-started))
    result=solve_path(board,time_budget=remaining,max_moves=max_moves,goals=six,spikes=spikes,
        avoid_skull=avoid_skull,cancel_event=cancel_event,seed_offset=seed_offset)
    after=board_after_drag(board,result[0]);metrics=board_goal_metrics(after,six)
    return (result[0],metrics['combos'],metrics['matched'],after),six,'six_only'

def recovery_route_goals(goals, required=True):
    """Set the heart-match preference without changing colour/combo goals."""
    values=list(goals)
    if len(values)<7:values.extend([False]*(7-len(values)))
    values[3]=bool(required)
    return tuple(values)

def route_operation_seconds(seconds,goals):
    if linked_clear_mode(goals):
        if seconds is None:raise RuntimeError('지우기&콤보 · 조작시간 미확인 · 2초 잔여를 확인할 수 없습니다.')
        usable=float(seconds)-2.0
        if usable<=1.0:raise RuntimeError('지우기&콤보 · 시간부족 · 2초 잔여와 입력 여유가 필요합니다.')
        return usable
    if not master_multicolor(goals):return fallback_operation_seconds(seconds)
    if seconds is None:raise RuntimeError('달인 다색 · 조작시간 미확인 · 4초 잔여 조건을 확인할 수 없습니다.')
    usable=float(seconds)-4.0
    if usable<=1.0:raise RuntimeError('달인 다색 · 시간부족 · 조작시간 4초와 입력 여유를 확보할 수 없습니다.')
    return usable

def combo_condition_goals(goals, required_total=0, combo_bonus=0):
    """Enemy and team requirements combine; never replace the master's six."""
    if board_clear_mode(goals):return board_clear_goals()
    if linked_clear_mode(goals):return linked_clear_goals(required_total,combo_bonus)
    if len(goals)>13 and goals[4]:
        return tuple(goals[:7])+(max(6,int(required_total)),max(0,int(combo_bonus)),False,False,False,False,int(goals[13]),int(goals[14]) if len(goals)>14 else 0)
    master=master_multicolor(goals)
    survival=master_survival(goals)
    return (tuple(goals[:7]) + (False,)*max(0,7-len(goals))
            + (max(6,int(goals[7])) if survival else max(6 if master else 0,int(required_total)),max(0,int(combo_bonus)))
            + ((False,True) if survival else (True,) if master else ()))

@lru_cache(maxsize=30000)
def _matched_bit_combo_count(bits):
    marked = _attribute_bit_features(bits)[2]
    count = 0
    left=0x01041041
    right=left<<(COLS-1)
    while marked:
        frontier=marked&-marked
        marked^=frontier
        while frontier:
            spread=((frontier & ~right)<<1)|((frontier & ~left)>>1)|(frontier<<COLS)|(frontier>>COLS)
            frontier=spread&marked
            marked^=frontier
        count+=1
    return count

def certified_next_wave(board,groups=None):
    """One known gravity wave only; unknown new orbs are never invented.

    Every known triple survives arbitrary top refill. Different colours cannot
    merge, so count just one additional combo per known matched colour. Later
    waves are excluded: arbitrary refill matches could alter their gravity.
    """
    if groups is None:groups=combo_details(board)[2]
    cleared=set().union(*groups) if groups else set()
    known=[None]*30
    for col in range(COLS):
        values=[board[row*COLS+col] for row in range(ROWS) if row*COLS+col not in cleared]
        for row,value in zip(range(ROWS-len(values),ROWS),values):known[row*COLS+col]=value
    # Distinct placeholders prevent unknown slots from matching each other.
    probe=[value if value is not None else f'?{i}' for i,value in enumerate(known)]
    next_groups=combo_details(probe)[2]
    colors={probe[min(group)] for group in next_groups}
    return dict(minimum=len(colors),colors=colors,known_board=known)

def board_goal_metrics(board: list[str], goals: tuple[bool, ...]) -> dict:
    """Measure direct matches plus a certified single known gravity wave for master goals.

    Direct goals: 10 combos, 8 connected, five colours, legacy
    recovery, exact four-orb TPA, five-orb cross, and five-orb L respectively.
    Shape goals accept attack attributes only, with no adjoining same-colour orb.
    """
    combos, matched, groups = combo_details(board)
    required_total = max(6 if len(goals)>4 and goals[4] or master_multicolor(goals) or master_survival(goals) or linked_clear_mode(goals) else 0,int(goals[7]) if len(goals) > 7 else 0)
    combo_bonus = int(goals[8]) if len(goals) > 8 else 0
    orb_bonus=sum(bool(getattr(board[i],"combo_plus",False)) for group in groups for i in group)
    final_combos = combos + combo_bonus + orb_bonus
    cascade=dict(minimum=0,colors=set(),known_board=None)
    if master_multicolor(goals) and required_total>=6 and groups:
        cascade=certified_next_wave(board,groups)
        final_combos+=cascade['minimum']
    combo_met = final_combos >= required_total
    largest_group = max((len(group) for group in groups), default=0)
    matched_colors = {board[idx] for group in groups for idx in group}
    direct_attr_count=len(matched_colors & PRIMARY_ATTRIBUTES)
    matched_colors |= cascade['colors']
    attr_count = len(matched_colors & PRIMARY_ATTRIBUTES)
    recovery_matched = sum(len(group) for group in groups if board[min(group)] == "H")
    recovery_combos = sum(1 for group in groups if board[min(group)] == "H")
    if "H" in cascade["colors"]:recovery_matched+=3;recovery_combos+=1
    skull_matched = sum(len(group) for group in groups if board[min(group)] == "S")
    skull_combos = sum(1 for group in groups if board[min(group)] == "S")
    if "S" in cascade["colors"]:skull_matched+=3;skull_combos+=1
    enabled = [bool(x) for x in goals[:7]] + [False] * max(0, 7 - len(goals))
    shapes = {i: shape_goal_metrics(board, i, tpa_goal_attributes(goals) if i==4 else None, groups) for i in (4, 5, 6) if enabled[i]}
    achieved = [
        final_combos >= 10 if enabled[0] else True,
        largest_group >= (4 if linked_clear_mode(goals) else 8) if enabled[1] else True,
        attr_count >= 5 if enabled[2] else True,
        recovery_matched >= 3 if enabled[3] else True,
        *[shapes[i][0] >= 1 if enabled[i] else True for i in (4, 5, 6)],
    ]
    progress = []
    if enabled[0]: progress.append(min(final_combos / 10.0, 1.0))
    if enabled[1]: progress.append(min(largest_group / (4.0 if linked_clear_mode(goals) else 8.0), 1.0))
    if enabled[2]: progress.append(min(attr_count / 5.0, 1.0))
    if enabled[3]: progress.append(min(recovery_matched / 3.0, 1.0))
    progress.extend(shapes[i][1] for i in (4, 5, 6) if enabled[i])
    if required_total:
        progress.append(min(final_combos / required_total, 1.0))
    selected_count = sum(enabled) + bool(required_total)
    achieved_count = sum(1 for i, flag in enumerate(enabled) if flag and achieved[i]) + int(bool(required_total) and combo_met)
    progress_total = sum(progress)
    return {
        "combos": combos,
        "final_combos": final_combos,
        "certified_next_combos": cascade["minimum"],
        "certified_next_colors": sorted(cascade["colors"]),
        "known_after_gravity": cascade["known_board"],
        "required_total": required_total,
        "combo_bonus": combo_bonus,
        "combo_orb_bonus": orb_bonus,
        "combo_met": combo_met,
        "matched": matched,
        "groups": groups,
        "largest_group": largest_group,
        "shape_count": sum(value[0] for value in shapes.values()),
        "shape_counts": {i: value[0] for i, value in shapes.items()},
        "attr_count": attr_count,
        "direct_attr_count": direct_attr_count,
        "recovery_matched": recovery_matched,
        "recovery_combos": recovery_combos,
        "skull_matched": skull_matched,
        "skull_combos": skull_combos,
        "matched_colors": matched_colors,
        "enabled": enabled,
        "achieved": achieved,
        "all_met": (all(achieved[i] for i,flag in enumerate(enabled) if flag) if enabled[4] and not required_total else achieved_count == selected_count),
        "achieved_count": achieved_count,
        "selected_count": selected_count,
        "progress": progress_total,
    }

def route_search_budget(seconds, default_budget):
    """Short drag windows get bounded fast failure, including diagnosis."""
    return min(float(default_budget),3.0)

def goal_label(index: int,goals=()) -> str:
    if index==1 and linked_clear_mode(goals):return "동일 종류 4개 이상 연결"
    return ("10콤보", "동일 색상 8개 연결", "5속성 공격", "회복 우선",
            "TPA · 4개 매치", "십자 매치", "L자 매치")[index]

_HORIZONTAL_PAIR_STARTS = sum(1 << (r * COLS + c) for r in range(ROWS) for c in range(COLS - 1))

_HORIZONTAL_TRIPLE_STARTS = sum(1 << (r * COLS + c) for r in range(ROWS) for c in range(COLS - 2))

def five_attribute_near_completion(board: tuple[str, ...], matched_colors: set[str]) -> int:
    """Reward *missing* colors that have two orbs in a 3-cell match window.

    A four-color candidate with an almost completed fifth color must not be
    eliminated merely because another four-color candidate has more combos.
    """
    missing = PRIMARY_ATTRIBUTES - matched_colors
    if not missing:
        return 0
    masks = {color: 0 for color in missing}
    for idx, color in enumerate(board):
        if color in masks:
            masks[color] |= 1 << idx
    score = 0
    for color in missing:
        bits = masks[color]
        # Same 3-cell windows as before, evaluated with bit operations. Masks
        # prevent horizontal pairs/triples from wrapping across row boundaries.
        if ((bits & (bits >> 1) & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS)
                or (bits & (bits >> COLS) & (bits >> (2 * COLS)))):
            best = 3
        elif ((bits & (bits >> 1) & _HORIZONTAL_PAIR_STARTS)
                or (bits & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS)
                or (bits & (bits >> COLS)) or (bits & (bits >> (2 * COLS)))):
            best = 2
        else:
            best = int(bool(bits))
        score += best * best
    return score

def verified_prefind_route(board, path, goals, cap):
    """Validate a cached/IPC path before trusting it, including every edge."""
    if not path or any(type(i) is not int or not 0<=i<30 for i in path):
        return None
    if route_move_units(path)>cap:
        return None
    if any(b not in movement_neighbors(a,master_multicolor(goals) or master_survival(goals)) for a,b in zip(path,path[1:])):
        return None
    after = board_after_drag(list(board),list(path))
    details = board_goal_metrics(after,goals)
    if not details['all_met']:
        return None
    return list(path), details['combos'], details['matched'], after

def independent_master_search_process(board,spikes,cap,avoid,seconds,bonus,output):
    """Publish the five-colour answer first, then assess six combos independently."""
    try:
        if sys.platform=='win32':
            kernel=ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype=ctypes.c_void_p
            kernel.SetPriorityClass.argtypes=(ctypes.c_void_p,ctypes.c_uint)
            kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
        else:os.nice(5)
        five_goals=(False,False,True,False,False,False,False)
        six_goals=master_survival_goals(6,bonus)
        five=None;six=None
        if not five_attribute_possible_by_count(board):
            output.put(('five',None,1));output.put(('six_skipped',None,1));output.put(('checked',None,1))
            return
        if cap>0 and five_attribute_possible_by_count(board):
            candidate,_,_,_=solve_five_attributes_v101(list(board),time_budget=1.2,max_moves=cap,
                spikes=spikes,avoid_skull=avoid,required_total=0,combo_bonus=0)
            if verified_prefind_route(board,candidate,five_goals,cap):five=list(candidate)
        output.put(('five',five,1))
        if five is None:
            output.put(('six_skipped',None,1));output.put(('checked',None,1))
            return
        six_cap=max(1,min(65,int((max(1,float(seconds))-1)/.14)))
        if sum(board.count(c)//3 for c in COLOR_ORDER)+bonus>=6:
            candidate,_,_,_=solve_path(list(board),time_budget=1.0,max_moves=six_cap,goals=six_goals,
                spikes=spikes,avoid_skull=avoid)
            if verified_prefind_route(board,candidate,six_goals,six_cap):six=list(candidate)
        output.put(('six',six,2))
        # Independent yes/yes does not imply one route meets both requirements.
        joint=next((path for path in (five,six) if path and verified_prefind_route(board,path,master_multicolor_goals(),cap)),None)
        output.put(('success' if joint else 'checked',joint,2))
    except Exception as error:output.put(('error',str(error),0))

def solve_tpa_batch(board,goals,time_budget,max_moves,seed=0,width=192,avoid_skull=True):
    """Batched joint TPA/combo search; scalar replay is the execution authority."""
    deadline=time.perf_counter()+max(0.,time_budget);cap=min(65,max(0,int(max_moves)))
    encoded=np.array([COLOR_ORDER.index(c) for c in board],np.uint8);powers=np.uint32(1)<<np.arange(30,dtype=np.uint32)
    bits=np.array([sum(1<<i for i,v in enumerate(encoded) if v==c) for c in range(8)],np.uint32)
    boards=np.repeat(encoded[None,:],30,axis=0);masks=np.repeat(bits[None,:],30,axis=0)
    badges=np.full(30,combo_signature(board),np.uint32);cursor=np.arange(30,dtype=np.int16);previous=np.full(30,-1,np.int16)
    paths=np.full((30,cap+1),255,np.uint8);paths[:,0]=cursor
    templates=[]
    for mask,border in shape_match_templates(4):
        cells=[i for i in range(30) if mask>>i&1]
        if len({i//6 for i in cells})==1 or len({i%6 for i in cells})==1:templates.append((mask,border))
    targets=np.array([x[0] for x in templates],np.uint32);edges=np.array([x[1] for x in templates],np.uint32)
    colours=[COLOR_ORDER.index(c) for c in tpa_goal_attributes(goals) if board.count(c)>=4]
    if not colours:return None
    weights=np.random.default_rng(seed).uniform(.6,1.4,8);required=max(6,int(goals[7]));bonus=int(goals[8])
    for depth in range(1,cap+1):
        if time.perf_counter()>=deadline:break
        parent=np.repeat(np.arange(len(cursor)),4);nxt=_BATCH_ADJ[cursor].reshape(-1)
        valid=(nxt>=0)&(nxt!=np.repeat(previous,4));parent=parent[valid];nxt=nxt[valid];old=cursor[parent]
        nb=boards[parent].copy();ix=np.arange(len(nxt));held=nb[ix,old].copy();displaced=nb[ix,nxt].copy();nb[ix,old]=displaced;nb[ix,nxt]=held
        nm=masks[parent].copy();swap=(np.uint32(1)<<old.astype(np.uint32))|(np.uint32(1)<<nxt.astype(np.uint32));nm[ix,held]^=swap;nm[ix,displaced]^=swap
        badge=badges[parent].copy();different=((badge>>old.astype(np.uint32))^(badge>>nxt.astype(np.uint32)))&np.uint32(1);badge^=swap*different
        _,unique=np.unique(np.column_stack((nm,nxt.astype(np.uint32),badge)),axis=0,return_index=True)
        nb=nb[unique];nm=nm[unique];badge=badge[unique];nxt=nxt[unique];old=old[unique];parent=parent[unique]
        marked,groups=_batch_match_features(nm);cleared=np.bitwise_or.reduce(marked,axis=1)
        final=groups.sum(axis=1)+_batch_popcount(cleared&badge)+bonus
        count=np.zeros(len(nxt),np.int16);near=np.zeros(len(nxt),float)
        for c in colours:
            ink=nm[:,c,None];hits=ink&targets[None,:];spill=ink&edges[None,:]
            count+=((hits==targets[None,:])&(spill==0)).sum(axis=1,dtype=np.int16)
            near=np.maximum(near,(_batch_popcount(hits)-_batch_popcount(spill)*.5).max(axis=1))
        good=np.flatnonzero((count>0)&(final>=required)&((groups[:,7]==0) if avoid_skull else True))
        for i in good[:24]:
            route=list(map(int,paths[parent[i],:depth]))+[int(nxt[i])]
            result=verified_prefind_route(board,route,goals,cap)
            if result is not None:return result
        loose=nm&~marked;potential=(_batch_popcount(loose&(loose>>np.uint32(1))&_BATCH_HPAIR)+_batch_popcount(loose&(loose>>np.uint32(6))))@weights
        score=np.minimum(final,required)*12+np.minimum(count,1)*25+near*4+potential*.5-(groups[:,7]>0)*80*bool(avoid_skull)
        heldcodes=nb[np.arange(len(nb)),nxt];order=np.argsort(-score,kind='stable');chosen=[];seen=set();quota=max(1,width//12);buckets={}
        for i in order:
            key=(int(heldcodes[i]),bool(count[i]));buckets[key]=buckets.get(key,0)+1
            if buckets[key]<=quota:chosen.append(int(i));seen.add(int(i))
            if len(chosen)>=width:break
        for i in order:
            if len(chosen)>=width:break
            if int(i) not in seen:chosen.append(int(i));seen.add(int(i))
        chosen=np.array(chosen,np.int32);boards=nb[chosen];masks=nm[chosen];badges=badge[chosen];cursor=nxt[chosen];previous=old[chosen]
        paths=paths[parent[chosen]].copy();paths[:,depth]=cursor
    return None

def quick_tpa_route(board,goals,cap,budget=.12):
    """Return a replay-verified ready route before optional beam improvement."""
    if not goals[4]:return None
    best=None;rank=None
    existing=board_goal_metrics(board,goals)['shape_counts'].get(4,0)
    # Preserve existing fours before considering a constructive new seed.
    if existing:
        for a in range(30):
            for b in neighbors(a):
                result=verified_prefind_route(board,[a,b],goals,cap)
                if result is None:continue
                d=board_goal_metrics(result[3],goals)
                r=(d['shape_counts'].get(4,0),int(d['final_combos']>=6),d['final_combos'])
                if best is None or r>rank:best=result;rank=r
        if best is not None and rank[0]>=existing:return best
    seed=find_tpa_seed(board,tpa_goal_attributes(goals),budget,cap)
    if seed is None or len(seed)<2:return None
    result=verified_prefind_route(board,seed,goals,cap)
    if result is not None and board_goal_metrics(result[3],goals)['shape_counts'].get(4,0)>=existing:return result
    return None

def shortened_route_search_process(board, spikes, goals, cap, avoid_skull, output, total_budget=PRESEARCH_WAIT_LIMIT, required_total=0, combo_bonus=0):
    """One low-priority math process, bounded chunks and rest, no GUI or input.

    Fresh seeds explore different beams. Only a verified success crosses IPC;
    unsuccessful routes and screenshots are neither retained nor written.
    """
    try:
        if sys.platform=='win32':
            kernel=ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype=ctypes.c_void_p
            kernel.SetPriorityClass.argtypes=(ctypes.c_void_p,ctypes.c_uint)
            kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
        else:
            os.nice(5)
        goals=combo_condition_goals(goals,required_total,combo_bonus)
        required_total=goals[7];combo_bonus=goals[8]
        attempt=0
        deadline=time.monotonic()+max(0.0,float(total_budget))
        preview=quick_tpa_route(board,goals,cap,min(.12,max(0.,deadline-time.monotonic()))) if goals[4] else None
        if preview is None and goals[4] and not any(spikes):
            preview=solve_tpa_batch(board,goals,min(.65,max(0.,deadline-time.monotonic())),cap,avoid_skull=avoid_skull)
        if preview is not None:output.put(('preview',preview[0],0))
        multi_tried=False
        while time.monotonic()<deadline:
            if goals[4] and preview is not None and not multi_tried:
                multi_tried=True
                current=board_goal_metrics(preview[3],goals)['shape_counts'].get(4,0) if preview is not None else 0
                possible=max((board.count(c)//4 for c in tpa_goal_attributes(goals)),default=0)
                for wanted in range(min(4,possible),max(1,current),-1):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    seed=find_tpa_seed(board,tpa_goal_attributes(goals),min(.6,remaining*.45),cap,min_groups=wanted)
                    result=verified_prefind_route(board,seed,goals,cap) if seed is not None else None
                    if result is not None and board_goal_metrics(result[3],goals)['shape_counts'].get(4,0)>current:
                        preview=result;current=board_goal_metrics(result[3],goals)['shape_counts'].get(4,0)
                        output.put(('preview',result[0],0))
                        break
            kwargs=dict(time_budget=max(0.0,deadline-time.monotonic()) if goals[4] and preview is None else min(1.2,max(0.0,deadline-time.monotonic())),max_moves=cap,spikes=spikes,
                        avoid_skull=avoid_skull,seed_offset=attempt)
            if goals[2] and not goals[3]:
                path,_,_,_=solve_five_attributes_v101(board,required_total=required_total,combo_bonus=combo_bonus,**kwargs)
            else:
                path,_,_,_=solve_path(board,goals=goals,stop_on_first=bool(goals[4] and preview is None),**kwargs)
            result=verified_prefind_route(board,path,goals,cap)
            attempt+=1
            if result is not None:
                if goals[4] and preview is None:
                    preview=result;output.put(('preview',result[0],attempt));continue
                output.put(('success',result[0],attempt))
                return
            if not goals[4]:time.sleep(min(.8,max(0.0,deadline-time.monotonic())))
        output.put(('success',preview[0],attempt) if preview is not None else ('timeout',None,attempt))
    except Exception as exc:
        output.put(('error',str(exc),0))

def find_tpa_seed(board, attributes, time_budget, max_moves=None, cancel_event=None, start_cells=None, min_groups=1):
    """Search only target colour positions and held cell to secure one real four.

    Full colours, badges and hazards are replayed by the caller before acceptance.
    Weighted best-first search is bounded; it does not certify a shortest path.
    """
    import heapq
    deadline=time.perf_counter()+max(0.,time_budget)
    cap=40 if max_moves is None else max(0,int(max_moves))
    starts=list(range(30)) if start_cells is None else sorted({i for i in start_cells if type(i) is int and 0<=i<30})
    targets=[]
    for mask,border in shape_match_templates(4):
        cells=[i for i in range(30) if mask>>i&1]
        if len({i//6 for i in cells})==1 or len({i%6 for i in cells})==1:targets.append((mask,border,cells))
    if min_groups>1:
        from itertools import combinations
        packs=[]
        for rows in combinations(targets,int(min_groups)):
            union=0;border=0;cells=[]
            for mask,edge,group in rows:union|=mask;border|=edge;cells+=group
            if len(set(cells))!=4*min_groups or union&border:continue
            packs.append((union,border,cells))
        if not packs:return None
        targets=packs
    distance=np.array([[abs(i//6-j//6)+abs(i%6-j%6) for j in range(30)] for i in range(30)],dtype=np.int8)
    target_cells=np.array([row[2] for row in targets]);lookup=distance[target_cells]
    colours=sorted(attributes,key=lambda c:(board.count(c),c))
    # Each colour gets its share, so a difficult first attribute cannot starve others.
    colours=[c for c in colours if board.count(c)>=4*min_groups]
    for ci,colour in enumerate(colours):
        now=time.perf_counter();local_deadline=now+max(0.,deadline-now)/max(1,len(colours)-ci)
        bits=sum(1<<i for i,c in enumerate(board) if c==colour);cache={}
        active_targets=targets;active_lookup=lookup
        if min_groups>1 and len(targets)>24:
            positions=[i for i,c in enumerate(board) if c==colour]
            costs=lookup[:,:,positions].min(axis=2).sum(axis=1)+np.array([2*(bits&edge).bit_count() for _,edge,_ in targets])
            selected=np.argsort(costs,kind='stable')[:24]
            active_targets=[targets[int(i)] for i in selected];active_lookup=lookup[selected]
        def heuristic(mask):
            if mask not in cache:
                positions=[i for i in range(30) if mask>>i&1]
                # The nearest-ink distance retains progress before any line exists.
                cost=active_lookup[:,:,positions].min(axis=2).sum(axis=1)
                cost=cost+np.array([2*(mask&border).bit_count() for _,border,_ in active_targets])
                cache[mask]=int(cost.min())
            return cache[mask]
        heap=[];best={};parent={}
        for cell in starts:
            key=(bits,cell);best[key]=0;parent[key]=None;heapq.heappush(heap,(2*heuristic(bits),0,bits,cell))
        while heap and time.perf_counter()<local_deadline:
            if cancel_event is not None and cancel_event.is_set():return None
            _,steps,mask,current=heapq.heappop(heap);key=(mask,current)
            if steps!=best.get(key):continue
            if steps and any(mask&target==target and not mask&border for target,border,_ in active_targets):
                path=[]
                while key is not None:path.append(key[1]);key=parent[key]
                return path[::-1]
            if steps>=cap:continue
            for nxt in neighbors(current):
                moved=mask
                if bool(mask>>current&1)!=bool(mask>>nxt&1):moved^=(1<<current)|(1<<nxt)
                new=(moved,nxt);nsteps=steps+1
                if nsteps>=best.get(new,cap+1):continue
                best[new]=nsteps;parent[new]=(mask,current)
                heapq.heappush(heap,(nsteps+2*heuristic(moved),nsteps,moved,nxt))
    return None


def solve_path(
    board: list[str],
    time_budget: float = SOLVER_TIME_BUDGET,
    max_moves: Optional[int] = SOLVER_MAX_MOVES,
    beam_width: int = SOLVER_BEAM_WIDTH,
    goals: tuple[bool, bool, bool, bool] = (True, False, False, False),
    avoid_skull: bool = True,
    spikes: Optional[list[bool]] = None,
    seed_offset: int = 0,
    cancel_event=None,
    start_cells=None,
    stop_on_first=False,
) -> tuple[list[int], int, int, list[str]]:
    """Search routes with hazard safety first; five-color mode ranks distinct attributes before combos.

    The silver crossed blades are metadata carried with their orb. For each path,
    the solver estimates how many thorned cells are entered during movement and how
    many thorned orbs are cleared by the final matches. It favours an overall positive
    thorn-safety score (2 * cleared - crossed), then fewer crossings, then more cleared
    thorns, then poison safety and combo count. Damage is not simulated exactly because
    the game's HP/damage formula is unknown.
    """
    if len(board) != ROWS * COLS:
        raise ValueError(f"퍼즐판에는 {ROWS * COLS}칸이 필요합니다.")
    if master_survival(goals) and not goals[3] and start_cells is None:
        return solve_five_attributes_v101(board,time_budget=time_budget,max_moves=max_moves,
            spikes=spikes,avoid_skull=avoid_skull,seed_offset=seed_offset,
            required_total=6,combo_bonus=goals[8] if len(goals)>8 else 0,require_five=False,cancel_event=cancel_event)
    search_started=time.perf_counter()
    start_board = tuple(board)
    start_spikes = tuple((list(spikes) + [False] * (ROWS * COLS))[:ROWS * COLS]) if spikes else tuple([False] * (ROWS * COLS))
    enabled_goals = tuple(bool(v) for v in goals[:7]) + (False,) * max(0, 7-len(goals)) + tuple(goals[7:15])
    avoid_skull = bool(avoid_skull)
    eval_cache: dict[tuple[tuple[str, ...], tuple[bool, ...]], tuple] = {}

    def evaluate(bd: tuple[str, ...], sp: tuple[bool, ...], crossings: int) -> tuple:
        cache_key = (bd, sp, combo_signature(bd))
        details = eval_cache.get(cache_key)
        if details is None:
            raw = board_goal_metrics(list(bd), enabled_goals)
            marked = set().union(*raw["groups"]) if raw["groups"] else set()
            cleared_spikes = sum(1 for i in marked if sp[i])
            potential = near_match_bonus(list(bd))
            details = (raw, int(cleared_spikes), int(potential),
                       five_attribute_near_completion(bd, raw["matched_colors"]) if enabled_goals[2] else 0)
            eval_cache[cache_key] = details
        raw, cleared_spikes, potential, attribute_potential = details
        skull_matched = int(raw["skull_matched"])
        recovery_score = raw["recovery_matched"] if enabled_goals[3] else 0
        recovery_combo_score = raw["recovery_combos"] if enabled_goals[3] else 0
        thorn_score = 2 * cleared_spikes - crossings
        has_spikes = any(start_spikes)
        # The selected five-attribute team skill is activated by DISTINCT matched
        # attack colors, not by the total number of combos. Rank 5 > 4 > 3 > 2 > 1
        # even when a lower-attribute candidate would produce more combos.
        # Poison/spike precautions remain safety constraints ahead of damage goals.
        combo_tier = 2 if raw["final_combos"] >= 10 else (1 if raw["final_combos"] >= 8 else 0)
        if enabled_goals[2]:
            # Attribute count is the hard primary objective (before hazards
            # and combos). Next retain near-complete missing colors in the beam.
            objective_rank = (
                raw["attr_count"],
                attribute_potential,
                raw["combos"],
                raw["matched"],
                potential,
            )
        else:
            objective_rank = (
                combo_tier,
                raw["combos"],
                recovery_score,
                recovery_combo_score,
                raw["achieved_count"],
                round(raw["progress"], 4),
                raw["largest_group"] if enabled_goals[1] else 0,
                raw["matched"],
                potential,
            )
        safety_rank = (
            -crossings if has_spikes else 0,
            cleared_spikes if has_spikes else 0,
            thorn_score if has_spikes else 0,
            -skull_matched if avoid_skull else 0,
            int(skull_matched == 0) if avoid_skull else 0,
        )
        if board_clear_mode(enabled_goals):
            rank = (raw["matched"], *safety_rank, raw["combos"], potential)
        elif enabled_goals[2]:
            # In 5-attribute mode, safety is a tie-breaker, not a reason to
            # sacrifice the team's activation condition.
            rank = (raw["attr_count"], attribute_potential, *safety_rank,
                    raw["combos"], raw["matched"], potential)
        elif enabled_goals[4]:
            rank = (raw["shape_counts"].get(4,0), round(raw["progress"],4) if not raw["shape_counts"].get(4,0) else 0, int(raw["final_combos"]>=6), raw["final_combos"],
                    round(raw["progress"],4), *safety_rank, raw["matched"], potential)
        elif enabled_goals[1] or any(enabled_goals[4:7]):
            rank = (raw["achieved_count"], round(raw["progress"], 4),
                    raw["shape_count"], raw["largest_group"] if enabled_goals[1] else 0,
                    *safety_rank, raw["combos"], raw["matched"], potential)
        else:
            rank = (*safety_rank, *objective_rank)
        if raw['required_total']:
            # Joint progress retains near-complete colours AND extra combos;
            # damage-nullified routes must never outrank a valid route.
            rank = (int(raw['all_met']), round(raw['progress'], 4), *rank)
        return rank, raw["combos"], raw["matched"], potential, cleared_spikes, skull_matched, raw["all_met"]

    states = []
    e0 = evaluate(start_board, start_spikes, 0)
    start_indices = list(range(ROWS * COLS)) if start_cells is None else sorted({i for i in start_cells if type(i) is int and 0<=i<ROWS*COLS})
    if not start_indices:raise ValueError('시작 가능한 드롭이 없습니다.')
    if seed_offset:
        random.Random(seed_offset).shuffle(start_indices)
    for start_idx in start_indices:
        states.append((e0[0], start_board, start_spikes, start_idx, -1, (start_idx,),
                       e0[1], e0[2], e0[3], 0, 1, 0))

    best_path = [start_indices[0]]
    best_rank = (*e0[0], -1, 0)
    deadline = search_started + max(0.0, time_budget)
    if cancel_event is not None and cancel_event.is_set():return best_path,e0[1],e0[2],list(start_board)
    if stop_on_first and e0[6] and (max_moves is None or max_moves>=1):
        pairs=sorted(([i,j] for i in start_indices for j in neighbors(i) if start_board[i]==start_board[j]),
                     key=lambda pair:int(start_spikes[pair[0]])+int(start_spikes[pair[1]]))
        for pair in pairs:
            if cancel_event is not None and cancel_event.is_set():break
            after=board_after_drag(list(start_board),pair)
            metrics=board_goal_metrics(after,enabled_goals)
            if metrics['all_met']:return pair,metrics['combos'],metrics['matched'],after
    if enabled_goals[4] and not e0[6] and time_budget>0:
        seed=find_tpa_seed(list(start_board),tpa_goal_attributes(enabled_goals),min(.5,time_budget*.40),max_moves,cancel_event,start_cells)
        if seed is not None:
            seed_board=tuple(board_after_drag(list(start_board),seed));sp=list(start_spikes);crossings=0
            for a,b in zip(seed,seed[1:]):
                crossings+=bool(sp[b]);sp[a],sp[b]=sp[b],sp[a]
            ev=evaluate(seed_board,tuple(sp),crossings)
            if stop_on_first and ev[6]:return list(seed),ev[1],ev[2],list(seed_board)
            seed_rank=(*ev[0],-len(seed),0)
            if seed_rank>best_rank:best_path=list(seed);best_rank=seed_rank
            # Keep a four-match seed while searching the required enemy combos.
            # Publishing/execution still require all conditions to pass.
            states.append((ev[0],seed_board,tuple(sp),seed[-1],seed[-2],tuple(seed),ev[1],ev[2],ev[3],0,len(set(seed)),crossings))
    depth = 1
    while time.perf_counter() < deadline and not (cancel_event is not None and cancel_event.is_set()) and (max_moves is None or depth <= max_moves):
        candidates: dict[tuple, tuple] = {}
        for state in states:
            if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                break
            rank, bd, sp, current, previous, path, _co, _ma, _pot, turns, unique_count, crossings = state
            if max_moves is not None and len(path)-1>=max_moves:continue
            for nxt in neighbors(current):
                if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                    break
                # Immediate reversal is redundant and can make the drag bounce in place.
                if nxt == previous:
                    continue
                next_crossings = crossings + (1 if sp[nxt] else 0)
                moved = list(bd)
                moved[current], moved[nxt] = moved[nxt], moved[current]
                moved_spikes = list(sp)
                moved_spikes[current], moved_spikes[nxt] = moved_spikes[nxt], moved_spikes[current]
                nboard, nsp = tuple(moved), tuple(moved_spikes)
                ev = evaluate(nboard, nsp, next_crossings)
                npath = path + (nxt,)
                if (enabled_goals[2] or master_survival(enabled_goals) or linked_clear_mode(enabled_goals) or stop_on_first) and ev[6]:
                    # Stop only after the selected colour/combo conditions
                    # have both been independently verified.
                    return list(npath), ev[1], ev[2], list(nboard)
                nturns = turns
                if previous >= 0 and (current - previous) != (nxt - current):
                    nturns += 1
                nuniq = len(set(npath))
                candidate = (ev[0], nboard, nsp, nxt, current, npath, ev[1], ev[2],
                             ev[3], nturns, nuniq, next_crossings)
                key = (nboard, nsp, nxt, current, combo_signature(nboard))
                old = candidates.get(key)
                if old is None or (candidate[0], -candidate[9], candidate[10], -len(candidate[5])) > (
                    old[0], -old[9], old[10], -len(old[5])
                ):
                    candidates[key] = candidate
                candidate_rank = (*ev[0], -len(npath), -nturns)
                if candidate_rank > best_rank:
                    best_rank = candidate_rank
                    best_path = list(npath)
        if not candidates:
            break
        states = sorted(
            candidates.values(),
            key=lambda st: (st[0], -st[9], st[10], -len(st[5])),
            reverse=True,
        )[:max(100, beam_width)]
        depth += 1
        # v90: 10-combo aspiration; never terminate on 8 merely because of
        # a fragile tuple-index test. Stop only on a verified 10-combo result
        # when there are no poison/spike hazards, and prefer shorter paths.
        if enabled_goals[2] and board_goal_metrics(board_after_drag(list(start_board), best_path), enabled_goals)["all_met"]:
            break
        if (not enabled_goals[2] and not enabled_goals[4] and not any(start_spikes) and all(x != 'S' for x in start_board)
                and len(best_path) - 1 <= 36):
            early_details = board_goal_metrics(board_after_drag(list(start_board), best_path), enabled_goals)
            # In five-attribute mode, 10 combos alone are NOT a valid early exit:
            # keep searching until all five attack colors are also matched.
            if early_details["final_combos"] >= 10 and early_details["all_met"]:
                break

    if enabled_goals[4] and len(best_path)==1 and (max_moves is None or max_moves>=1):
        pairs=[(i,j) for i in start_indices for j in neighbors(i)]
        if pairs:
            best_path=list(max(pairs,key=lambda pair:evaluate(tuple(board_after_drag(list(start_board),pair)),start_spikes,0)[0]))
    final_board = board_after_drag(list(start_board), best_path)
    final_combos, final_matched, _ = combo_details(final_board)
    return best_path, final_combos, final_matched, final_board

def solve_board_clear(board,time_budget,max_moves,spikes=None,cancel_event=None,start_cells=None):
    """Best explored cleared-orb count, with a real one-edge minimum gesture.

    Check every legal single swap as well, so a timed-out search or already
    matched board cannot become a stationary DOWN/UP. No optimum is claimed.
    """
    if max_moves is None or max_moves<1:raise ValueError('판 정리 이동시간이 부족합니다.')
    if cancel_event is not None and cancel_event.is_set():return [0],0,0,list(board)
    allowed=set(range(30) if start_cells is None else start_cells)
    result=solve_path(board,time_budget=time_budget,max_moves=max_moves,goals=board_clear_goals(),
        spikes=spikes,avoid_skull=False,cancel_event=cancel_event,start_cells=start_cells)
    candidates=[list(result[0])] if len(result[0])>1 else []
    candidates.extend([i,j] for i in sorted(allowed) for j in neighbors(i))
    if not candidates:raise ValueError('판 정리를 시작할 확인된 드롭이 없습니다.')
    def rank(path):
        after=board_after_drag(board,path);metrics=board_goal_metrics(after,board_clear_goals())
        cleared,crossed=spike_route_metrics(board,spikes or [False]*30,path)
        return (metrics['matched'],-crossed,cleared,-metrics['skull_matched'],metrics['combos'],-len(path))
    path=max(candidates,key=rank);after=board_after_drag(board,path)
    combos,matched,_=combo_details(after)
    return path,combos,matched,after


def solve_turn_progress(board,time_budget,max_moves,spikes=None,cancel_event=None,start_cells=None,required=6,bonus=0):
    """Spend a short bounded search on six combos, then clear/advance the turn."""
    started=time.perf_counter();budget=min(1.2,max(0.0,float(time_budget)))
    target=(False,)*7+(max(6,int(required)),max(0,int(bonus)),False,True)
    if cancel_event is not None and cancel_event.is_set():return ([0],0,0,list(board)),target,'cancelled'
    result=solve_path(board,time_budget=budget*.85,max_moves=max_moves,goals=target,
        spikes=spikes,avoid_skull=False,cancel_event=cancel_event,start_cells=start_cells)
    path=result[0]
    if len(path)==1:
        allowed=range(30) if start_cells is None else start_cells
        pairs=[[i,j] for i in allowed for j in neighbors(i) if board[i]==board[j]]
        if pairs:path=pairs[0]
    after=board_after_drag(board,path)
    if len(path)>1 and board_goal_metrics(after,target)['all_met']:
        combos,matched,_=combo_details(after)
        return (path,combos,matched,after),target,'six_combo_turn'
    remaining=max(0.0,budget-(time.perf_counter()-started))
    clear=solve_board_clear(board,remaining,max_moves,spikes,cancel_event,start_cells)
    return clear,board_clear_goals(),'board_clear'


@lru_cache(maxsize=4096)
def _three_orb_match_distance(bits):
    """Minimum Manhattan transport to a straight triple, not drag path length.

    There are only 4060 three-cell subsets on a 6x5 board. Cache them across
    searches. For a horizontal target, rows share a median and sorted columns
    optimally map to three consecutive columns; vertical targets are symmetric.
    """
    if bits.bit_count() != 3:
        raise ValueError("Exactly three orb positions are required.")
    cells = []
    while bits:
        low = bits & -bits
        cells.append(low.bit_length() - 1)
        bits ^= low
    r0, r1, r2 = (cell // COLS for cell in cells)
    c0, c1, c2 = sorted(cell % COLS for cell in cells)
    a, b, c = c0, c1 - 1, c2 - 2
    start = max(0, min(COLS - 3, a + b + c - min(a, b, c) - max(a, b, c)))
    horizontal = r2 - r0 + abs(c0 - start) + abs(c1 - start - 1) + abs(c2 - start - 2)
    a, b, c = r0, r1 - 1, r2 - 2
    start = max(0, min(ROWS - 3, a + b + c - min(a, b, c) - max(a, b, c)))
    vertical = c2 - c0 + abs(r0 - start) + abs(r1 - start - 1) + abs(r2 - start - 2)
    return min(horizontal, vertical)

@lru_cache(maxsize=32768)
def _attribute_cluster_distance(bits):
    positions=[]
    while bits:
        low=bits&-bits;positions.append(low);bits-=low
    best=100
    for a,b,c in combinations(positions,3):
        best=min(best,_three_orb_match_distance(a|b|c))
        if best<=1:break
    return best

def _attribute_bit_features(bits):
    """Return (has_match, completion_potential, matched_cells) without flood fills."""
    horizontal = bits & (bits >> 1) & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS
    vertical = bits & (bits >> COLS) & (bits >> (2 * COLS))
    marked = (horizontal | (horizontal << 1) | (horizontal << 2)
              | vertical | (vertical << COLS) | (vertical << (2 * COLS)))
    if marked:
        return 1, 0, marked
    if bits.bit_count() == 3:
        # A nearby pair is insufficient when the only third orb is far away.
        # Reward bringing all three toward a legal triple, including gaps that
        # do not change the old pair-count score at all.
        return 0, 12 - 2 * _three_orb_match_distance(bits), 0
    pairs = ((bits & (bits >> 1) & _HORIZONTAL_PAIR_STARTS).bit_count()
             + (bits & (bits >> 2) & _HORIZONTAL_TRIPLE_STARTS).bit_count()
             + (bits & (bits >> COLS)).bit_count()
             + (bits & (bits >> (2 * COLS))).bit_count())
    return 0, pairs, 0

def _master_attribute_bit_features(bits):
    found,pairs,marked=_attribute_bit_features(bits)
    if not found and bits.bit_count()>=3:
        pairs+=max(0,9-_attribute_cluster_distance(bits))
    return found,pairs,marked

@lru_cache(maxsize=100000)
def _master_color_features(bits):
    return (*_master_attribute_bit_features(bits),_matched_bit_combo_count(bits))

_FIVE_ATTRIBUTE_NEIGHBORS = tuple(tuple(neighbors(i)) for i in range(ROWS * COLS))

_DIAGONAL_MOVEMENT_NEIGHBORS = tuple(tuple(movement_neighbors(i,True)) for i in range(ROWS * COLS))

def solve_five_attributes_v101(board, time_budget=FIVE_ATTR_TIME_BUDGET, max_moves=65, spikes=None, avoid_skull=True, seed_offset=0, required_total=0, combo_bonus=0, require_five=True, cancel_event=None, allow_diagonal=False, cluster_hint=False, beam_width=None, transport_hint=False):
    """Find the first five-attribute route with incremental, diverse beam search.

    Keep bitboards per color, updating only the two swapped colors. Cache color
    features, not complete combo calculations. Reserve half the beam for different
    matched-color masks so one four-color arrangement cannot crowd out every
    alternative. Restart with deterministic color weights if a beam is exhausted.
    Full combo/group evaluation is used to verify the returned route.
    require_five=False reuses the engine for the master's six-combo shield,
    including heart/jammer matches and repeated matches of the same colour.
    """
    deadline = time.perf_counter() + max(0.0, float(time_budget))
    board0 = list(board)
    if len(board0) != ROWS * COLS or any(color not in COLOR_ORDER for color in board0):
        raise ValueError("퍼즐판에는 유효한 드롭 30칸이 필요합니다.")
    cap = max(0, int(max_moves)) if max_moves is not None else FIVE_ATTR_DEFAULT_MOVE_CAP
    goal = (combo_condition_goals((False, False, True, False), required_total, combo_bonus)
            if require_five else master_survival_goals(bonus=combo_bonus))
    needed = max(0, goal[7]-combo_bonus)
    initial = board_goal_metrics(board0, goal)
    if (initial['all_met'] or (require_five and not five_attribute_possible_by_count(board0))
            or cap == 0 or (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set()))):
        return [0], initial['combos'], initial['matched'], board0

    encoded = bytes(COLOR_ORDER.index(color) for color in board0)
    masks0 = tuple(sum(1 << i for i, color in enumerate(encoded) if color == k)
                   for k in range(len(COLOR_ORDER)))
    thorn0 = sum(1 << i for i, flag in enumerate((list(spikes or []) + [False] * 30)[:30]) if flag)
    has_thorns = bool(thorn0)
    has_poison = bool(masks0[COLOR_ORDER.index('S')]) and avoid_skull
    poison_index = COLOR_ORDER.index('S')
    feature = lru_cache(maxsize=100000)(_master_attribute_bit_features if cluster_hint else _attribute_bit_features)
    rng = random.Random(''.join(board0) + (f':{seed_offset}' if seed_offset else ''))
    width = max(30, FIVE_ATTR_BEAM_WIDTH if beam_width is None else int(beam_width))
    adjacent=_DIAGONAL_MOVEMENT_NEIGHBORS if allow_diagonal else _FIVE_ATTRIBUTE_NEIGHBORS
    best_path = [0]
    best_rank = None

    def evaluate(masks, weights, thorn_bits, crossings):
        if cluster_hint and require_five and needed>5 and not has_thorns and not has_poison:
            matched=0;potential=0.0;count=0;transport=0.0
            for color,bits in enumerate(masks):
                found,pairs,_,groups=_master_color_features(bits)
                count+=groups
                if color<5:
                    matched|=found<<color
                    if transport_hint:
                        distance=0 if found else _attribute_cluster_distance(bits)
                        transport+=(12-distance)*weights[color]
                    if not found:potential+=min(pairs,12)*weights[color]
            n=matched.bit_count();k=min(count,needed)
            return matched,(transport+2*k if transport_hint else n+k,n,k,0,0,0,potential)
        matched = 0
        potential = 0.0
        cleared = 0
        for color in range(5 if require_five else len(COLOR_ORDER)):
            found, pairs, marked = feature(masks[color])
            matched |= found << color
            if not found or not require_five:
                potential += min(pairs, 12) * weights[color % 5]
            if has_thorns:
                cleared += (marked & thorn_bits).bit_count()
        if has_thorns and require_five:
            for color in range(5, len(COLOR_ORDER)):
                cleared += (feature(masks[color])[2] & thorn_bits).bit_count()
        poison = feature(masks[poison_index])[2].bit_count() if has_poison else 0
        count = sum(_matched_bit_combo_count(bits) for bits in masks) if needed > 5 or not require_five else 0
        rank = (matched.bit_count(), -crossings if has_thorns else 0,
                cleared if has_thorns else 0, -poison, potential)
        if not require_five:
            rank = (min(count,needed), *rank[1:-1], potential)
        elif needed > 5:
            rank = (matched.bit_count()+min(count,needed), matched.bit_count(), min(count,needed), *rank[1:])
        return matched, rank

    while time.perf_counter() < deadline and not (cancel_event is not None and cancel_event.is_set()):
        weights = tuple(rng.uniform(.7, 1.4) for _ in range(5))
        matched, initial_rank = evaluate(masks0, weights, thorn0, 0)
        states = [(initial_rank, encoded, masks0, i, -1, (i,), matched, thorn0, 0, 0.0)
                  for i in range(ROWS * COLS)]
        # Lower crossing counts dominate repeated board/cursor/thorn states.
        seen = {}
        for _depth in range(cap):
            candidates = {}
            for _, current_board, masks, cursor, previous, path, _, thorn_bits, crossings, spent in states:
                if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                    break
                held = current_board[cursor]
                for nxt in adjacent[cursor]:
                    if nxt == previous:
                        continue
                    if (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                        break
                    next_spent=spent+(math.hypot(cursor//COLS-nxt//COLS,cursor%COLS-nxt%COLS) if allow_diagonal else 1)
                    if next_spent>cap+1e-9:continue
                    displaced = current_board[nxt]
                    moved = bytearray(current_board)
                    moved[cursor], moved[nxt] = displaced, held
                    moved = bytes(moved)
                    swap = (1 << cursor) | (1 << nxt)
                    next_thorns = thorn_bits
                    next_crossings = crossings
                    if has_thorns:
                        next_crossings += (thorn_bits >> nxt) & 1
                        if ((thorn_bits >> cursor) ^ (thorn_bits >> nxt)) & 1:
                            next_thorns ^= swap
                    key = (moved, nxt, next_thorns) if cluster_hint else (moved, nxt, cursor, next_thorns)
                    cost=(next_crossings,next_spent)
                    if key in seen and seen[key]<=cost:
                        continue
                    seen[key]=cost
                    next_masks = list(masks)
                    if held != displaced:
                        next_masks[held] ^= swap
                        next_masks[displaced] ^= swap
                    next_masks = tuple(next_masks)
                    matched, rank = evaluate(next_masks, weights, next_thorns, next_crossings)
                    route = path + (nxt,)
                    if (matched == 31 if require_five else rank[0]>=needed):
                        # Independent replay/group calculation is authoritative.
                        after = board_after_drag(board0, route)
                        details = board_goal_metrics(after, goal)
                        if details['all_met']:
                            return list(route), details['combos'], details['matched'], after
                    if best_rank is None or (rank, -next_spent) > best_rank:
                        best_rank = (rank, -next_spent)
                        best_path = list(route)
                    candidates[key] = (rank, moved, next_masks, nxt, cursor, route,
                                       matched, next_thorns, next_crossings, next_spent)
            if not candidates or (time.perf_counter() >= deadline or (cancel_event is not None and cancel_event.is_set())):
                break
            ordered = sorted(candidates.values(), key=lambda state: state[0], reverse=True)
            states = ordered[:width // 2]
            selected = {id(state) for state in states}
            buckets = {}
            for state in ordered:
                if id(state) not in selected:
                    buckets.setdefault((state[6],state[0][2]) if needed>5 else state[6], []).append(state)
            # Iterate each bucket in rank order without repeatedly shifting lists.
            positions = {mask: 0 for mask in buckets}
            while len(states) < width and positions:
                for mask in list(positions):
                    index = positions[mask]
                    states.append(buckets[mask][index])
                    if index + 1 == len(buckets[mask]):
                        del positions[mask]
                    else:
                        positions[mask] = index + 1
                    if len(states) >= width:
                        break
        if cap == 1:
            break  # All legal one-move routes have already been examined.
    after = board_after_drag(board0, best_path)
    combos, matched, _ = combo_details(after)
    return best_path, combos, matched, after
