"""Local visual inference/training, review records, retention and GitHub sharing.

This module owns the active model and its per-thread inference cache, the review
and sharing locks, labels and image eligibility. It does not import the overlay,
Tk, OCR engines, route search or device input. Import performs no credential
lookup, network access or user-data initialization.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import numpy as np
from PIL import Image
from pad0_common import (APP_VERSION, ROWS, COLS, COLOR_ORDER, COLOR_INFO,
                         COLOR_HUE_ORDER, ROUTE_GOAL_OPTIONS,
                         _atomic_json_write, _load_json_dict)


def circular_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)

def rgb_to_hsv_np(rgb: np.ndarray):
    arr = rgb.astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.max(arr, axis=-1)
    mn = np.min(arr, axis=-1)
    delta = mx - mn
    hue = np.zeros_like(mx)
    nz = delta > 1e-5
    mr = nz & (mx == r)
    mg = nz & (mx == g)
    mb = nz & (mx == b)
    hue[mr] = (60.0 * ((g[mr] - b[mr]) / delta[mr]) % 360.0)
    hue[mg] = 60.0 * ((b[mg] - r[mg]) / delta[mg] + 2.0)
    hue[mb] = 60.0 * ((r[mb] - g[mb]) / delta[mb] + 4.0)
    sat = np.zeros_like(mx)
    valid = mx > 1e-5
    sat[valid] = delta[valid] / mx[valid]
    return hue, sat, mx

def dominant_hue(cell_rgb: np.ndarray) -> float:
    """Estimate orb hue from the saturated central area, rejecting pale grid/glare pixels."""
    h, s, v = rgb_to_hsv_np(cell_rgb)
    yy, xx = np.ogrid[:cell_rgb.shape[0], :cell_rgb.shape[1]]
    cx, cy = (cell_rgb.shape[1] - 1) / 2.0, (cell_rgb.shape[0] - 1) / 2.0
    radius = min(cell_rgb.shape[0], cell_rgb.shape[1]) * 0.44
    d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) ** 0.5
    mask = (d2 >= radius * 0.28) & (d2 <= radius) & (s >= 0.32) & (v >= 0.24) & (v <= 0.98)
    values = h[mask]
    if values.size < 10:
        mask = (s >= 0.22) & (v >= 0.2) & (v <= 0.99)
        values = h[mask]
    if values.size == 0:
        return 0.0
    hist, edges = np.histogram(values, bins=np.arange(0, 371, 10))
    idx = int(np.argmax(hist))
    return float((edges[idx] + edges[idx + 1]) / 2.0) % 360.0

def hue_to_color(hue: float) -> str:
    """Map a chromatic hue to one of the six normal drop types (never jammer)."""
    return min(COLOR_HUE_ORDER, key=lambda code: circular_distance(hue, COLOR_INFO[code][2]))

def classify_cell(cell_rgb: np.ndarray) -> tuple[str, float]:
    """Classify a board cell using hue, saturation, brightness and purple/pink RGB balance.

    Dark (purple) and recovery (pink) drops can share bright magenta highlights.
    Their red/blue balance over many saturated pixels is a stronger cue than a
    single dominant hue. Low-chroma/brown cells are classified as nuisance drops.
    """
    h, s, v = rgb_to_hsv_np(cell_rgb)
    yy, xx = np.ogrid[:cell_rgb.shape[0], :cell_rgb.shape[1]]
    cx, cy = (cell_rgb.shape[1] - 1) / 2.0, (cell_rgb.shape[0] - 1) / 2.0
    radius = min(cell_rgb.shape[0], cell_rgb.shape[1]) * 0.46
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    disc = (dist <= radius) & (v >= 0.16) & (v <= 0.995)
    if int(disc.sum()) < 16:
        disc = (v >= 0.16) & (v <= 0.995)
    sat_values = s[disc]
    val_values = v[disc]
    if sat_values.size == 0:
        return "J", 0.0

    median_sat = float(np.median(sat_values))
    chromatic_fraction = float(np.mean(sat_values >= 0.30))
    hue = dominant_hue(cell_rgb)

    # Detect the ordinary dark-blue spiky jammer BEFORE hue classification.
    # In the user's screenshot this icon has a dark navy body, blue/cyan edges,
    # very few bright pixels and many low-value blue pixels. A normal Water orb
    # is also blue, but its central area is bright and has almost no deep-dark
    # pixels, so it does not satisfy this combined signature.
    disc_h = h[disc]
    disc_s = s[disc]
    disc_v = v[disc]
    dark_blue_fraction = float(np.mean(
        (disc_h >= 185.0) & (disc_h <= 255.0) & (disc_s >= 0.28) & (disc_v <= 0.62)
    ))
    deep_dark_fraction = float(np.mean(disc_v < 0.28))
    bright_fraction = float(np.mean(disc_v > 0.70))
    median_value = float(np.median(val_values))
    if ((dark_blue_fraction >= 0.28
            and deep_dark_fraction >= 0.22
            and bright_fraction <= 0.22
            and median_value <= 0.62) or
        (dark_blue_fraction >= .55 and deep_dark_fraction >= .20
            and bright_fraction <= .35 and median_value <= .58)):
        # A glow behind the jammer can add bright edge pixels. Its large dark
        # navy face still differs from Water, which lacks the deep-dark core.
        return "J", hue

    # Detect the skull/poison tile by its icon shape, not hue alone. Its purple
    # frame can look like a Dark orb to a hue classifier, but the skull face has
    # a large pale/white area inside a mostly purple ring. Normal Dark orbs in
    # the supplied screenshot have very little pale area and a much larger
    # fraction of saturated purple pixels. Use the central disc to avoid grid
    # lines and bright effects outside the orb.
    pale_fraction = float(np.mean((s[disc] < 0.30) & (v[disc] > 0.52)))
    purple_fraction = float(np.mean(
        (h[disc] >= 250.0) & (h[disc] <= 322.0) & (s[disc] >= 0.25)
    ))
    central_face = (np.abs(xx - cx) <= radius * 0.56) & (np.abs(yy - cy) <= radius * 0.60)
    face_mask = central_face & disc
    face_white = float(np.mean((s[face_mask] < 0.24) & (v[face_mask] >= 0.66))) if face_mask.any() else 0.0
    face_black = float(np.mean(v[face_mask] <= 0.19)) if face_mask.any() else 0.0
    # Locked skulls have a yellow lock in the upper-left, which can shift the
    # dominant hue to light (Y). The pale skull with two dark eyes is stronger
    # evidence than that incidental hue; normal light orbs have no dark eye pair.
    if ((245.0 <= hue <= 320.0 and pale_fraction >= 0.27
         and 0.16 <= purple_fraction <= 0.72) or
        (face_white >= 0.35 and face_black >= 0.07 and purple_fraction >= 0.09)):
        return "S", hue

    # A true neutral/grey block has very little saturated material. A muted
    # brown jammer tends to sit in the orange-yellow hue range with lower
    # saturation than the regular fire/light orbs.
    if (chromatic_fraction < 0.16 and median_sat < 0.23) or (
        18.0 <= hue <= 39.0 and median_sat < 0.58 and float(np.median(val_values)) < 0.93
    ):
        return "J", hue

    # Disambiguate purple Dark vs pink Recovery. In purple pixels blue tends to
    # exceed red; in pink/heal pixels red tends to exceed blue. Use many pixels
    # rather than the single brightest icon/highlight. Exclude near-red hues so
    # Fire is not accidentally interpreted as Recovery.
    if 270.0 <= hue <= 352.0:
        rgb = cell_rgb.astype(np.float32) / 255.0
        red, blue = rgb[..., 0], rgb[..., 2]
        purple_pink_band = disc & (s >= 0.24) & (v >= 0.20) & (h >= 255.0) & (h <= 359.0)
        if int(purple_pink_band.sum()) >= 6:
            weights = s[purple_pink_band] * (0.55 + 0.45 * v[purple_pink_band])
            # Positive means blue-dominant (Dark); negative means red-dominant (Recovery).
            blue_minus_red = (blue[purple_pink_band] - red[purple_pink_band])
            balance = float(np.average(blue_minus_red, weights=np.maximum(weights, 1e-4)))
            if balance >= 0.035:
                return "P", hue
            if balance <= -0.035:
                return "H", hue

    return hue_to_color(hue), hue

VISUAL_MODEL_B85 = 'P)h>@EdT%j2mk;8AprXjkNyAu|NsC0|NjgC6aZ~ybYWy+bYU)TaCrd$5C9GX00000003tJ00000006C0O;5ux3>{Z~Mbt|{>ZW!d1;(A*5EA0Rsq#{Hi<UN(lU7Wlzk)xEvx=fhNJt3h^86gz&xt=CUp7y#7n62!3UjoQ!nFwBn<R`xSQv5yRc0th&p&{+$h_S{jqG2gt7UW@iBA04E>D4#dn_SXizE_3f#4cK%kF9(Gof;Z!PXdOSxY@71U+q&fJ4?aB+-Wp)nZmc)qvK=jO)n*=GhKP%(ez7xBfb(t4dY7FKR6o%(kFa4o;cso1ZgJSo8GWI|b5cLuUkaO{PV!$x*{^5rC8pfjoNsGcQ<joM7j5#k0HH#mM>utR2fkB167GJeDC*Yfq=1uaPpT!S+Jpp-VEL`G6J!S`KJ6ptMI_$5<PAu)h!VqyF)H>Bw&Wbv`a1k51C{>Q$-Sxt7lgo=g69v1vAqDAb$Z9%0eli^_=FSmk&(gxedcgoZ6@8!@e{sl>T2UvuKT8vF>|Cs0cR1T6pn00;m803iT_ETF^x|NsC0|Ns9C02BaYZ)|UJUw1JsZg6=401yCx!vFvP00011wEzGB0001-m-#yv&ez86`%;LKH5DPP3hy~nM3GW`T4>QGr9CMX2`LI?PYO|qEM?8|o-+|5iIP-O+S5X46VkWmUwG!XIoDj*`C-m|?)w$vw0z;>m7*fvBEE*3+`ZiP8d~WYlFic%P4x^n@7}v_?}lCLcJJNf{(tqk8+LfP|Eqg>Y}n)e?`~v1b+W16)aesU_5Aeye@AY+ZwR5P>mbkl0A%UuQN?x{Aap)|eKnS(oE4#R&wdf-L+ZR#a*#~;m<{h@4`ct+K{j9*2Sbh&gA&_eqE80mzG>8S?@pmd@*4Wb@ggpFy#$rr$~bDX1uU|a$6x-r$R-Hrg;lL^H(rDri>RWbTPn;NwhtYiM`O<LSiyVdOR#Y~&l%LW8acjLm}gW^ewY@4N<tF$b=uR{rDoJ?h6%OqmE$9~Hp8=RN6=+c4NT>G(A;jRP_*|E^hZfx)P58G#!jCMV=A!jk|(}%Q{!8Ih(lW4PjGrI#~)1o3OjbaWyTi0aAm3voiK2WshEwU;Jp+yq!j3nhsu0Kp&Mj2wUco_E?RBIUYvH?1O2z_!P<x!#QDWy-nd)_OCD|m)xV9nvta|i$wtuLGZvg&|HFM}nqa8lGw6zUmzgB&q<8+ag2$sKL3Z>h*y524?v6Q3tj?L9I2?prS;)#P)o`wv1b-;5PIq5-ptl{W7?vD_nCBK)fBlB=YVQJ!O4O!SU00dL=Q0!$d3coj2yf{*qU|~#uv;I)&Us&EAIr7q)|VlqL`<3YcTJ?uhr;m2!CJVewM`gza2xUd<4S{M578gLHF)^s2k>lGG&_`6i*KY-@K?1G-SYbe*}MNN+T2eA%cNqGx%@DklGLD%Kem;H7@fk*J|P?(t4617T7V`cL(q0Lk$o}9Coz+Xaf5R^(OdhCg)U1IdU=Gi@xGC;Uv+@(9M*^H>;o|CaW0h3e@5!2zQK9sK9Jxy5kk+{5j%@ESe{mkE%idS$gLFy7K+ik6EwKNeF<vYs?1k9IKtxe^Q2^RAUjZPOY08V(7&hDa8_YA$ojA0QzYtzhc9#7P?LaB#wghH!i2vaRt5HF)`5imRN;o^ad^(li)<=h0p^}ZnUi5A1lcBG<%?%9_nkVwAHNk~M5L9lJ&#QcJO_&wTha;FUb90AAD~I|3W~_23p<6BtE#F}k(B8$X>}g%_FV()PcJU0_JSE!BSHO}F0a@Agk#JUz{Rr}R$R?P_vSisyhtCj4z-s#+e@SM^9XoTTq!*0sRr*C-a^qO1t1|<&CIt@viQbsQhRVUOphxiVQmdKbKgkn6A=$DdN$+vfjlCJ3&R?l6hUm#Pr-fD1ei880%Zz*vb|A{LHj~J>-&BMs*<YN9Gx?;X|pk1r|Utwp2cIU#WYsZEX!9-PQ`fD0LT~m<IFA7acyxBPBQlf{|VD@jf6g?jSYnZm%GY7Ts#05su?_QZNsTQ^yuWHH(7*|E2=*dXUDpQg2J*q=o)<$9zQ!roMbe~vmMfKqR6bY_p&7~XIp{xUq>&mS$x>j5$sIsU8_V59olkl20h!2B;a8P&YwLB3eMZo>EABnh0i^(es&BRmkz^)VVS7gmBb!C$^ore1agf>5&cgA?8XTNzT&}kY!OYt$y%2{^Ui#*)9`_|P{caJ0#eqKhR*w)Aj$28z&~{bZkjWPiOqZr^9*Bf!owgqIAat>Xa?c^r?T{6>1;u*#3nLz_!zoL?-h&<)5Y%{u6&9v7s}SRS{f}D#g1E1g8QeGxJq>}vR)vIoByEowmQqL2QNZ$x-Q(Ls`SvRD{QxpB5z~o;PR{CI1@I3T~P&mv`i2_D7b*X4y~r|_s;+duVLesTeyXZagiVWxN%Z7tgpC=8SC`<+YckzsMA%LyV?V08@q#&QYf*u=*11whJnSuKYVs$9jeEd6OEoGl=mCKZY~KE=tmq8PCvH+^j0mT0ZkQ{{^J|OY75BIJ}KBPEMu=%+cCd|-^lmjaah=J5^hM^L2bn_>YCvX`7&Wp)sTwE{tl;824leT*fDfnDGCSb^Kni0PIN&rP#ZeJekNZNj;$I=Yo08Cv#uVn?0W{DTRtS1{Ynbl{Sxs~(@%_97l?i#=`8(mBJ9<jE{Mr<!WIIsKUNp>t){{DIUS@aR+D6uhJfSnBwTZTExV`c4uii!z+-I=cD_-h9~RWWGpTAUDf8zav<XuT%d^^79fUz0s{F}mX+B#m4YI`+;I008Hhf_hBuvZ(TjOfn6&uHntbPYg^EVQk>|uf@xu4nVgF5&*JqVYb5T#z{^x*lW0n)cF1@G7;;R2$Ka{CO?dy*tybWsSqzbwb0$gf0gm>Oh0-5?;R4&lM3F|cfl9yy!z0e`5yXF*4TV6GA}3-Kss($GVWncuNm2L9mMugjfPeXz#$0#VhkfuVV+_|<PB&Ylv$RI{6~+4~`eWX^%)m*Hq48w<vJW{_hk@v!pjN@lS<A9FXo#j=V85I6HAs47YDmj~amjpcEI|3c$HDeW+sWORjTxkclW_yqVm*OEEr)L_URQFw9qHhXyD1-?1=v8?>83p$30^1aWFLdeMGGQ)GeuzE)=NP2xGpC4P|#=NOGddDfKus)4hX7|vs@wU*Sr4LjTLraQ%N5O=LUm;*jKF-gT!-}!NSZ!cJRmPkn>dSp_XIUYXT=4)LcNlDLYvJ|{DcC3T2=6Y|pjG}IDD}e&M;VN!?Y%;HTX7AC-rXVxr|2<Br=wuA=M%WVc>bU9QrtRI2}gTfC#DvkaJa1&CYszNN++ba@bzSb_E4PFc^NIv1Y@IzkXWpaLdWEdjFh(G=X3JB1v8*X#|InwhoQ|SDROdCF0@BDgP4B{+hB49rFN*m_m>;-;(|vI=ClKw0v|EAdz*0mQI7fHhEVu-0c-W1jW^4a$@14@F-Y??TCP{7Iff4CeDo&TY?#CwK5T;1$wt_yn1Two+Q_5ZE+{c0f_YV>LcyteXj8h2Vr#Bp-_1y?IR`U9OVbZ_T%SzhhaCXLQN=KsAAqj)zRV40z?NSNnXegxnJ-qcb*i(_L>L6GZ%}g5&JtgXZxEJ-7}24H`4~F&89d)I9QW^i%7k@~P?xM_mkO(~N9#9pOWg@!-mA+rrWE64=TLC)5s<4_F5;-Wj!bh&1=b|aCjEkT6cbG0J}+xPBKRiSPyLFIn(KsR<v&1F-v=YYM#7bKelWCi1^KZ`1J^j|qt16Vv|Cju+_=hx8s2#T0h2b8168S(UH28(M9(Pv>oFC=#jUu<bU*M>mEq0F=G^&rk<fqVZmjn>4<6#{(0@%N)IT_ZQ&;Rj8K2!kyRJMqydoRrwQNy3_d3gEFUZvz2^wW`jm&R9K<Wx=+45C8F;=huU8Q@_y*8hXQ?~`-)n@#zH5mjA0oXM5BEE^*&PIRsC9%Vvg7r;Ve$HN$P7pRjM9m6Vaz+`YkNw0WPseey{WHPF`w3heu!0p?GcjSW5&iph6}!6m0|pCzf_c6ZTb?+9U5+y2epbIAHT5(EmA`?{U-w`|+!MCVRSKj!jJO1ug;jxyRA`t2FAt<+>ji1tr#%-}zm9^Ady;fSa00kJKLnc3K7h-m5nL+q{y#5?hyKS^cr3lx^3%@I_*(I+U_-SX9_)S1W~ZGIIta!R`?RSvc>a3QTyTepoolu#QglYA@@5oMXu-Z0$z=V9bx`*_lt>@HhbOnDp?PH$Tsk?9|CoIif@bCm*2bT~!s$5}wc|1BFHON-7ZKe3@jv07mH;51WU1mCKVp`;87swFaM<NB=#`lS^S8+JjlGx9wLKmcZy#jCHXH(>Z~-h0Rb?(ZB2?}AqOzW7eaIag$&N2SiC!O;fmXU5J`U?b^L5*C`fdwmwo4J_{VBupb*A)C;WQo*a2E5s2cb&55jM|W#UtC4Ih4%g%_b^zQ1BdRWC96}c_Wy3SP_23z9p5mPb_t1kh$-*L+iVf@TOQVZn|!PweRiu!3K%4@2CFhbM$qTc+?M9+qH4!?hI6Mo5*&=RKhiv1H8q0FOxhw7Y#le(vhueV7b^SwpO>Asm9)b@ZFzDK}QrWIw(WUyEkI3gA+EYn({Zp6wqG!l)#4w$&zDHa4)!sDOfdGi4WbwZ0GK>Pa^vSm*%L@DkTdp(y|_>?Aw6e*HvKdNi$mC=L;Y1o)t_w@`(Jn@WLu6x(d5*xq#Qb!)QuQlttTe^lm*4YW><gr!)i?7bQch*<>#Dx)>tA9N`KtZjb~QV|ZHl7u4Tw=6SV&Xgu3V=;0j({iBs=MWYjiwqxKhs83C4G`!wuiQ^55V4!9M`hD|2g(kq{0z2V5ffN@z<;<Ua{9blUxfdlxTflSqaO(bV&Q9Be!;Y~A^uPAgSY=y|)oCse^WFg@!)9P)&taS)Od}6|%g~4S09_8aVs(}jF0cv#({oE8wOA5l6E@-};}_sxoCx}3D%j|)GilP!d-y%sg}qLlhR?3)!15|r@Xs@V0C!s+X?GqvB1~X-RV{8#sln_86ELHvRH$LU6D^djfma8>-e<+=aX1P#6wC2PpW|V%NC{N<1<+G+!+3(0FVX8B30r2F!(P2~j1*l5$;WTP=zD9y=#m2e8aNL=?db&wKgGtrdWQaG3Uo=>1+wtZGiI8z2hNo*DG9B<1bY3Z)P0a)O~VL!a;6ffhso1-ZW{$QHMPReyBp9Z^gc9mOMz;<D;ODA!-uv-WG$S*C9?*>yfpy-7CMk6Av4&SQ~QKNU2h=&#%$O*sg+pyic$mVO~Sn;*HCf6CETEw48N>ugg?A)67Szpc=q=*>^Icr9(9e-(QW*1-`Q~K4n_Lktocx|c{MBv3uR|}mHB+{K-5)y3~l%3bJOn2WTd788*@<$ouX|Z>(fa5sCOC1w`M`H(l2m-@DZ9NETMQ+E!qq}My~wIAp_4_U~$nkXxRUWy&u%2I#aXBwxUgVH}nR`>PWLVxe~PcbqyoWghHu#1`bNK;fFiNP~^u++%xtt)HlzhJ8v=cKYNdr+;+f$dFn!~=(RXJ-vyf9uSTn(5*Rvm1`g|$z>243s3kodmY42;ID>w8;=BmEPpV<v8GA@pJ_t&qj)3E|L0BWvDNJ8FTX-X46Xfcz!Rax%;HkHh7^XB4qp`+pciwdrZn+GqUd31xb`KnTBx!_yDYpOWWnQHM8kMX-m2RX#!@E9|nH7wo*bO80YNG9uT5NMjU?)_(1nYvNKt7=cM>}?7UC=4$OgIBEtN((QPI>WQ$2lxI7)AWwcnId(sL<Z=yW!7yb&Q&lj8je>ramuk66QM@%<o-hnVo+zd;elyo~%pPRs@kLQ}j87VceWO70yrB<B}Ig&`rafaf+Ta@B31TdzBo)cHL9ZFZ;s&Jb8}WzkGr4C<k!(8bR)zY(v>LO=>^7U6_5P7XD%m4h!%{TZgmkulzhVab_3xMHRqlBWc>_IvpR~?SiCf6S>siN_aVDKaOt*gBi<h@U&;VVAb$A$Y0om4V(V^=igh&dHJdQR6{hAauA{IK?^`;V+N_)eit`o4zNO{2Q1p$3-#Vh@S>9S_#piT4&RdsZ+uqpIM*)n*HMKOiU&jD`Kh!+xeqK1m+<u)5=lqR1l(0x4_{P1f@<+Rd@r_;YkTgXW_2Ru(X?O8P(%Wf&f0*X)IOfjyc?r^6!^e}0JJIn#74zQ^M!Hx&?&VaPE0xgkB=&HO|?kum`6Y&tqLdRi=p~{Rpzd(OgqL2;oy32nAN)<7x-JzNc%<Ts%(pSYee||lY97|_F(YT&K7DMx$=+OGjY{aXBd@{1czpIK=xNjZZ=GuzNp`ciG3HD%~o;x)MPeVW@yk{6Me27%gCLeOi+9M7@rEiqr}EN^u?JlIMFeU%y7!Y@aKovtJNxOU;0dLaa)IY=Z}H!Ck&8gn7~|z6tKO&iZ5ApAG&V6z`5U6(<_(4U|9VG9xCUD$?j4($!9+Zlzrf$dx*e!j*unhThj9f|1m<cBWA=+hkeWDq3zdkIB)ZB)UO)CUXuwJHkd(%=cqHCv)>>~a0aZ?73kt+MW|7x1`o%Lr^N+hLDRqyy+RJ7>LeEk-#LobA3gzdMF%h{MVn5)mIFr`uYt)j!cR*S<LaN@Fmsv#e`7R+TTV&R9HW2QG0evMmPzp7XE&Z|-bBN{iR1Txa&mN04$<5+4fM?mg$<P!Y|-I~V3?FkBAYb0#CcJE`|omCGVB_duP}$7bQq7V2}a?|cBXtbsZ4WSAoQm#g1xHUMCZbB)^}$Ow&*+a`kqkCxH5o^E|e|`aV3EPb?D!jgNElfu-2n%Y2xaYkUH%@()lSDPrWmug{$(R|I|9v?zY7h>mCano_t~w-{g5z<`J&Dt^)lMGFf8oQ#_XX65dz*g=E!F;M!!$&;2oGdF^Xq<ZdzY)YJ*%ue70{n^~n7_mF_E63}-l8n$J~5f5I%6jb!6eWMI9orNeW5)7-Y!XQuKGE`ml#65?0gOe{qDW`IXEf8S`W)^^5!g~}uZ^(_s;sxm!+~9cAD`DLl1)QC*75Av;!@KEP{G3>$(3IAJ_xfuvTBiuMlm%hZlj;AonFQ~$rt-he`WSF{6Iw)^!PU8?Fugz*O`|RG#jFWnB$vZB7rlX4vr<U7DZw2_Q2uh67`NKm1B3oLEFgG49DQTP2dc(UzqM_!!pH$f=U>30=nz!Ta>hipUF^%S5T3rv7<G>PVa~DHxbKiBUOErdbh$L$(Kv^jJ}|~(Q7_>9ky_kf5RF?S<jM0nXP~c40c}<f2-b*I!jnIDg)8N+qHS9++^W85rM6d!-%pFhJ2HN3*L)GWEGAXhJ9#~uCN09t{l#cOX9k<P!vcrBjc1V$^l<%R2e41AW*44bElv5VOb*nqgVvQ2bXj#iZn2z;THWeYP9qSMS_h$5O@$xOIm?_Q1<=|l#73xOOJdw{dwl|G#6{!&ru8KLgdC47HvzpUEy$dg565{tNokPd!swakv}rOdt!W0&dI~}@ada)x<lF!D{(Cc7&=hM3_QNLPx>7Y>9+Qd%H`IBt_8gvd;0A6oti?kfFY#~W17aDcOD9)IQ!|}8nAd2?dlFwl{BKogT^xnOURJROj!Q9fVI1jQ6onD5!r^v|sbxt?0(SLZf%Wrj*se3X*t3!qsOvYLZoXXsURzyZUS0*;ELsBZkF^Q?cT7ahzQe4h?lwy>d_`tWF(pGj1u$GipG<HJ!LYYEEHmjZI}$6&^?Ry8YJ(h&_-{4adh;AiP+5k1C!fWL_)92PV=2(<*MP(Wjp(j^l6^196VCX26<v2O#q9Qa+<th7T=ub`%ddPUt3TBW!V|M_;RQFro9oU(!~e>yMD=s=XXh<;<v<4s7r85hHBE55WF0PCwH&{W-UQA1dLTGHf$3=#3f>MX3&-@<ATO<iOvz7Rn=b%^#5$CYlYrH0X5&1?VeHhlqd53&F>YREhq{XzP<!?dqJTZHQalW|?mLT#v3+cBV<i~NOr)WBmiTxcXA@jAh(x^)Sd6vC9mfCsB<V4()Vjc;qf6Kp8%27&aX(ZY6s6ZpJITvkCU{3b1?@-w6`*AnQ_xi53X_V-@X3BmLJfsuyr=Q^lGRw%=`3jS{tZnrA^31<JIh|*&TfnTW7>jaWuuZmv+9lz9CXs++0)|4>6V`$`6o=EIn0GSUJAi9t>d^Y)D=5JY>+IkL_x(V{AIF^<Q)HtmI)LCUaZ843F5T5DFc`9a3aT(=5nv3YIaNhCA)Pm1$90auq)y2aIwmo?0x%17-@PAEmw%p-$R#}ulXz1B9<Xg{i%pQEv3*;!w<ScCZkM`FYXu9LFb1B_~l|Ws3>11d}Jy69Fbu4yE7aIJk9V<)FW8dI~M+0ilOfDbW%KJJI+bV!5>?Ep=i|}NDsRTW~OBzU8q7u>kC-DS0wp%Cy)dyK7#O3BHUc~P<Vf)GFQv{RJJB82PVasVe2_%=5lKUSUbL9la~0C&xQ9u`qfj^9G?zx_Wl_0=?~;AZ3K~;DyFvE5k=<MV)s8VklQ1}1H;BbLC{xnQu`Xb_|uN9dS&<_q6Ckss8dl@BkG%U4qY66Li1}vylz~<m)BE-b!wFeKjpbr{|UI6poPva=D?{@Zv`56T_8hckQ_gA1K(eWf`)7nZh0V>wA-ujsJ5?QFzFs%R(!?+r^|DdtR(n3+lY#KcEcsbVJLUOkX{hi#lwnrxasd5t6!cUu*gUa22a06+ksv1)A2E6gy=A5wPTp~;uq+>FNP_?09=yY0=<>R>{OZ)wOToWH~dlOy;`H`gR}|ky#8!z$S0wqagWu_SxaHpfv;?7og?m8Du#&{-{7a)qj}uIE9jZ&$RbB4!ITBMxW9UcL`!@Yy31cB3lF`((yRY^tlEfz0!4UVHJwLH(}qy32dLT_%wB|YsMJh{)lFV_S?&-xg}cFzGZHw*w~(+%M4d%4tl_vEUw%}PdhGZ`VvoJVJ?DO5(2p{-<P9*p+)NN+FcD{%{eiW@IMzK`3mzz(L&MscAZZdu8fP|RrL+vyIe!bhWG7I$(uu6GZx(d#&xMe|lW^d`WcJn74O;rPg7>NGtm)_sXnZWfvn;l<vLZ!3&r$)-)>}gKn<~sV{z}UCz9JhIdVsG~GPaLfO4`l!aK_7Ez<u|SZqx<aeah$;+Qv%H&cX3o)`ED&d_22wAB;-Wf(?py!9aWjtjX5qcR!wn=ofFncI6T1nrcSE1N(#n&P`-RuLeC=vlaU~Md|BQXS%gbi=S7q!m!)bFln1Ml>V0`ywzz)<>GUN4@Q)Lcyc)`Jf{y|lw5GtfEK3KHUgb;sO;DwDJtszhOBk2g)-+U@Ht>JIejl3MbGC#w7e&N@lA#lEsD)o^H5elh~3`g0Uz$4h7h$*;hp|iP$DuUOG|`yHW-0KnKCubG{XmyYw>aZaJppWLRhA=28_p0jPQ;S9Ql<&-gp(V1<5^l(d#)F%$K9q<*`i8Ar#d*t+<4H1F@VqgXcC+LUXfc=r>=9XMGDM9dnKOI48u5q2)xyX9PNhdqZ65Z{f(NlX=>lQ~2)IUd&jX2Ag&i;fV<+;M*huo?pVCHyyEUS_{}WN5PnHyRiDVJ}#N~Z*Nwpu;1ScnD661Y>Jc~Upue>Mjp|iW#iAG`_LDRyS!DXeOeUe4()*O<LA&~{k4BQW&lx|_H_JVSzh|Ykx$xUM7`7U*_g0Qs8v+JtX?gAIWeDfRz5+GZ7w+R!wC9Ar-hi+y~JBS)v$4K1HP}$0ON<LaB9^aW@=@J;p^Pk1OHOCx??&I%e~4LN4x~v@qKLU@85WE<PZc_J%PEVp-fgk0yjGBVC}AxiHBSvri%*r`oj+B=-|t)=NMv<`7u(T@5X~%JVDlb9V)Fcpv_@BF!uc`JR+=P;cC9b<B2Zyn?DBAjN|@gb2&zm-h$*5A1I0GfXE~jDrPOurQBpWH~o&eL*o3hnkl<7Zx*hg6ZrG_i{P83JKbiVNQPhkfVO`P`OC{gOe-^x$qsBlg+T!twI{%(iq(*D(Gi>Ge?;xzED){TPolc^v8J`#@mbplZXa9<IpRB*$viRM*!}==){mgCyZrIw=2X}fYLDuZZo&SCSD1y~N0!vmLrg9o0b0_{^6$1|vHddAo6-)yYDe?nbGanz$#s~&D+n|z%;3z`+5FPnB)r+al$qfTys>)?w`y~Nzw%w!^=7GXk5CfhUrBM>gXXX<Gz2GPB+{eDW}{3!@Z=wI+-8LdU)nMXY=b6Y&m}otP$b1KpV6WrViX(=wV31Y=6{U)3A+}>Vf8Uv{H8t+k15WA4~C<l&(jT?mW~8!c?4ExDT4xkA&jYcg`qps$@m6B>m7fg=%+!Vc}#@P4#|a}G4a@{et^l|n@m4#tzpYvi&AZkFKk=?NoKD404i3z5+=7)Gv`ZtAUIVCE9y<)q@}iS^6nN?8SIDpon!dI5xTf%qb3&_H-$_uOTax-F5}(d0Wjab6JlJA`1Qk9pke7r*X`7y)B8qZwRAAPu9?8jzA>eyG7oTmtsLi<L&%r<BAgUZ$T}x4<%*we`2M<`Agp_jbe1iRxUmJ42Uqb$Bj!Th&fB<f*<xB~!q{Q?v*_pe22)z@K>CeznD>4yn>T(Kq_j)(#^X`=zO5Y9zQ>?ttrnk|@5Ocs>`=}(90tT^($E7dVE^cUOf!5d6U|x$n$`XgX0{ABR+#V=E*4bk&?j)qjmDL~i>$^l5t=#cAgujQgKoQ(f`0;x`TJloI{tJRHp=h8Z`KV~s;LLa^pOwoxNtNatgI#vF5Lymm4EP5+ifBvt%<MGiUh4qTT$9%B9{}<!?Q4#+8A_UzhjF)M{FU5HP4~HES5NVZ)M_7KC?3CV7PMj-|QP1@-J%I<k~ACE-h9>Y5NRJ2zkT)3qAlZb{Y{ueIK|lzKegSt>!aYl=;kKeJB|<1VI~zgH=%%OSxi#*7-JYxhe(}?kqsbykYd^=C?S*=MT1Z*I@Q4Gk*R<C^-=}itZ{I%fD<L5Hy;Z)9r&Bs9DW&{4sev*K}SB=lmv;o$X`!skkTX<6%|myyGNf3Q7dajqVB?zdZz((^8~3)E$mF|Hhwt{<5;)t+4Gi;Oe9ukh|+1^u@cv7q{`~8u1=Rj*G?zIm77<!E`KnP>fa@9OXs)$YwJUjJKV_SIj%Z(ke#&<HdG%$nO(=ef<`ql~zFi#@mA5yGwD@)iQD#N73E=&oTYPVw|<m4YFMu*z@f=EJRWo)0iWQtEteRw?47wbBZ9#u8~!>Go1C~gfR1HE~)rj2a)@afc?E5p`?orSI@ZwgE?`qQPzx{UOHEB%dr-(Ry;zh<{Zq*cI0}`fQcQdg0Hz!T%vv|{8^L>ZJ|#gd44#Kep<qccGL^kOG^o>Ud-W|4Gu8W_!3H{K7{szU(q6dEY1wQ0Zxbi@y{d+x_;&woG$eO)8Bj+t{eA|$jmuT%5@F#?dqLaFya!v8kR&%rRJeRnHWuZ7>L#D^Vs5)B(OO$1{1#2;xoSroFbQj6@w{oeBo=1mfwaQAB5nzS)E4SSV^Yk-$Ch1rR1Z675x<7i8-I=@|R<0fySBN`0m1dIQjN4u05d1XH}jfrn~p!*pK#ftZ6$IoDlGD&1cZF`Vvl;eFQH}c7nCyT#{>@gTrGN(&(hU@U7(nS(sM_`;Npw;J_02{ohecQ(DElvtk6!Ck>!*#1Kk4h~wYOmqE7U1=tryW7V3M*w%XizyDUI@3lT7nV16MMjzm)Sv~xaOUK_08Z^D}J$&bXQ2sE3VU5=4mMqDK-M<1GT}_}StB^E&oeA?J-LSPM4YCb7Aw@=kH@XCnz(g(HW=}!3;4-lvo=z$%PNRvQ691wS$^5gespd)}KE6^HJ;kkQ<hXdal~an2+myNGMp0h>R2ibC11~9iErg-nq)kMJ2PVtYO2NIdVJ}N?Y1d}Vznn@gzLKIV+s5MEHBGp_Y$@t5N{6LEtI2inMw5Z@u$W5mb?Zc_s8=PVKVJs@n+(8O*9-ksgu+<qPo($sE_7P73`&YLNl3(8zToH>b}Ghz##r7&n@?v^QxjS3=wclIMF9RmraYYL@rPj=aOze%F3>Ax8b;1EJVjji;Jpg_FkGG{?+asheuQDsmo9SXdmQw5c)`2tHsElk7H0}4;^Or+a8xM6&-vVjqccpc_Pck&V_O1VUROa!A{;E1nW3-uC-N*+orbGRa=p_MJTl}g>^Y!DM{C@KQAsIG&+ICBddddW0;G6xk25sCy~Qf;_@l+0LiX#YHnB8u$3>UZ*?#vZ{7^oY4lh9h2D)6vL<a-IZ?jkC<!Jov9R57z4>22_ph8?Md^5R>*)cNAL}$OH+lE#)Dy)$y?au?J6$5zb=X+GJ2!oDKnQUt4aWd)53Q9A+3GT0n6()XofG1~OC1~~&;u1=6PP81F&%A>-GtV<fZ5J-6IfCtvBzVE2T68Ze0xkDaVZa$+bZ--xv8IOoDp!C{!G-8FzZF$S%aeyImXNxY5$yOX86G+I1S>O$f*a{Mczqy`IqS{^#kJqa*hF*8dOD7tAD@RSo=CyLBctHND`)a&?Mh+Bm$PiY?s@15J`F3^&*USoo**NR{D);OGqLPYE8IRO1-C}V;?#?iQT0d|=Gac7pDK|geR+rP+?T;h^P?pA<49I(bOq-nB(NWu991@SK~|?NnRHs2m%RAGuKV4=u#hyoTXGq!JDcIsVhLQ6>M5KIv-$IDQ$TG+Ed-ZH!>4tjm{h9H#_SGa-&#$v<@OyoRJedhrX$lX6Qv@DPcloLV6bwC2AwSvP>H;SgS8Gs%OsXm`E0=NLCs~Kea2(K(@sd<r3d~)wsc%5;^Lz5xYkmN|J2=$*BW&NCO2<D{Zwb1JX4xxIZ2}Oi5YlZ5y{EQ7wG?M23@M?gi~@(FzJ|wF!QYm>g)`Ke2H1`@wU3~t3^MWJ>>ADK7|;%w+d@M-(>R>q`;7^;Qg|fn2AdQdu4r{Y`Omi$keT%{a!~XUZMaqDvy&dQnmQiR*!VdGvs~GkHQa6b0*r~hy{Dvpx8MN?k-W|Bj1={PFxVYn6V05nv!7j!X+3Rb{eJb{6W3^6@t~~{qV5z6Wg{d6g|7jg)O^o!tcK(pjiAwII7{dpnb0~x55-i+ZKrDXWs*N&rYIuA`q|Mss%+w8PdBg6chLSg0e<Cs;hFBt=;qv6h#-{nXMD3Z*(7K8i{~CBW$F099RV_!oap0IAr(-D@7G)bifp{)GQU*_%vd%e>Xgk8IO<1Xr8E3ZuM<u8#G;*&3nlU;r8A4LFd5+$P9T7^K`yrv0VqAd6PswEm{E=7Ru4J861<p>+v~$e{jN%8RYlF5is9r4R$-b!cCcYt6bkK*wa1%Jj~<S)U1~z0{08wwpx>h{-Y$XxxDP}EW(_os*{{kUvTlgdm!dC1fKsqe3V!s4w>D=Xia^(<Xj}w9t_0{$rad{(gSzO#OZC?1uom#n5RZMq>PPbp3{P%H|{h`Hc{XrSy`|xbRM*72;gOv6n!bRU(mFy2dQW~JU!Zni`xw81KnuW(Ub}=2DV{EWGJu=@BcC8IUJm9OczXl2pbPeaJ8~eSX1*8N<I!k@>XX!v1KIhuR0-2YM2MsRpH$4*Jd_v)maj<=?Wg1kc+kujU_LIIL<bawN916>lc>N_}K}hv{k@u>>EjirW}M?XG7TWxm-s43{JFX`243b29~Xc?s8EY`uZyzx|xP5hX{xqw}rD6>ipxP4@^NmSFlF14--FVP}`_q;D6qQ4J(u45!M4(A5l$0Q)KBqw+1XSj>Z(d7qI@8Jk-CufFCCpG2A3eGc8kbys<3Rle>zi2S@OR@`q5K)B~PNMsN}G9+vt1<J~Ks;34uG_KT!pz?W{YO83SvG6MI_PsGJnbt%j;f~~4~IAEaxemi{W?Ggi?acL!veQN<9hvaBpNeT8XPC_Z$Wl+BR8nmCSz*8;7m^~*M?psXf_;DWwE*c})r27N&Cu9&W#X#ogZ$~e0sb#HW%JEC4F1L>BW!WD^LG3xirg{hLHaLj~)~JysQ>D4s`fBp#?07mssvk38F)sevjHe1p&}_CWu;mvp_{}uDtYHuFt%)GHYZ)C{*NZ#GX5qqIcXGk_Fs+S?!PN&O_&$XSNc=?Uy1F>rVR@I`70SVB-v})6F{I&3zG0P59X^=Kz&-IOZlC7@U7dw+F)$Tur=Eu1a~;sPC<WJ#m_=1@{bmOpm&2JDG48u)8_a*2fXja=aTjA>=0Ao|8?_GM_XD-)DVYYpw@#ylCw<`h0XbgN^@w<T_rd79MqIz`GB$RdU>AI@W5$h4@I4cN>$NgbM^}$)xc8vb3lXZ6U4+F2bNFt#5FEX`QBV`y4wjwMxPjVg)Vq6-?^+oIcN*JaYHAIrJ+!4~??&Lf5@T-eSAcyQlgdVa`pTpSa-p%$1e@Gi;hJq9n7^9>FLZ7ApD|ytDK|{`-(CuDD(+dCu_64^GMtXQs7N!-cN3Mr|1sHa1$r;>6WVvxf%7AAezIo_w~QZ6M|gzcS2q!?+b&Ai-MLA6#*N@7@7`eh?AP)geak436a(+FG4Sa5ELgWX9K3T%iF(N&3@mJc1#bpO{xm6mtguF~>HA`u&=v!JJ6FO2g+AOYcU|b1n+$4M?PzKf#T=j3qN#{9cdC6rq-rI&?TsXKSQ1BqOoSMFVHtg}`50!twcra4B)GzM5iZD)rXE`_;TRJEZuODnUwUNVpoSDZ|6c|<TcSzp#dZ0X&6-^GeGT3+c*RQ3`9ov<5r}n`;jeC-fSCzBARZ@1tvq*fR}BHLJh=y7E*OXV6t%hfoIK`h5(O4xCe!kk5R6Tl$?~j(q-wi8@9R*YnZ3jLjI01YK0t=H+YPcs>2VOGoCkGN(!jq}j31qz$XvSa<D8Xc@Y%{8{TvoCzpeRr@b_u_82p=P$5i0<eSgbLCd`7*XH0l$tR)VLPodVA@}chHH+J|E!`Gix>4J?>aL|4uSJb?Q->uKV;KwDn#W{rd=<9O@<pa1lS(H`2OGZCuPuS<S29K_OjJM2QVcjS@CKEQD=q(rL>iJ=~a(6d2+dL)v9Tcf#zBv6pBtdH(n(?@*5icFBN0v$&@{$YgSQ&N^JvPd4kA2G2eVH0nTi*@N-)j(Wu3-arH=;1&8d%1y!QF$p{P<8Aj0g^*l?(ra_E?5N@18@~UI*;Beiy(>l3%?30wq3JL+bk|bdVd1kxwMR*QJGhEH1JPHr@$ikBD)9180~bRf+3m{qeE$3a)od6$0o0-l{r_-3LN3bN>XmS@;N5mTCw(){UeN9~$A%Eq7ezJ%u0KVvecD_fdD>GnOUawE1TB->{@+A|EyGFa+=O!RKF&qPN3zymmPVO}_`i)R-2`d3yp2=DowktB(mi)w)4)Q3<m#)*xj?VtlZ24{qrM>a?U^P<KWQrt}Z6drta%yQ>HvCs07~r2UXP)kL^zS~A=C{-4hdONKPr9uOI;%yS&ZaR0y$_$bMR)-4I<2hF?jK;~!IaM7M#y`Y7W1}Cf<<(7lRGkJdYuN*DiSwT`~9Kx&hKS{{fH{_}1Cs30*1f$OHg@RF~xIifg{yOd9vyPX*ALC2d4XRw~dj<x58IEi7Gf{bL8kRhC!U;NBbfV8xOwE6V$De&ikwgE%Sn;oDcV-N^Ke7O}E|;O*CE569^;%4{^a4MbN4RtUA=u;pf!rDKj|n>CamC-wSl**V!!ttAUv(2tn|TUXjlB-OXih6Hc3|Lp1xi!?>F)eiI9jeww+W5N+Y9%g=dUd4{`t<PSgZ1s_jcU7`vZh4NKj*IW8N&ONEd!Lfr~vu5Hiw{KT7<AQNa_rO4dm>=iC%t;<L0&`-m#d?g#`UhudV#c^4Y1o=1FUC~$+8o8-gKqgeaZ0E0~J;O3Ggu;|bo@@^u~!-Z#H>kNBRJxQPHQ3_J2Eo4?_HsbrEsJXEjGKYp!S=W)U{_QIq^&tm+`m)L7w(r;y)&xT@OoT&czQeQQ$5@iy1Rhy2jfxtsq1j>qXt$!*;_-(&AW}Dh{}5!t#2;T!I8KVsQMkgA)keU`>9(}e{5R&^zJk$mqqs>_H`wfY1?#_U$AjA=;QcrWZsVc^ZjN7I^OB7i=%+@F3$3vC)Iy$hr=4AL8pkX3wV-7A4kFAe#%}w2@cQWnTKPvB4j;4Q&e|H>OZPJR8Xtk<A!pg_-GeOoz5otLr837c+i=abUikC%IM&>FjxjMI5Ro&3Nh<rGaO@f=`qBY`gT`><8i6Uf8dx{h3i5`01gow)VE%U<L7~oG7^aX)H0rY9^_gav@4XC_zj@&<>3Kqxprdem-%*&7G#$h4oB(WBrMf%ck>g$I=wNV<#H$nHHd>E2s5;?6k?(Bi`beJjs#UnU*MJ*NYr=2ek=dNgLgx{h^x|s~Zjcd<kMmU_Y?vLaTJ;muvkUO8wG^nfePmlYhQa51^03_VG>-e53gdSqv-k35e2w*Q^27TpnvH6};6Fmr_kJo5Q)~p;(9=+2xPbQURN@&rZpapvkXPCfQ1$+l;6b_}NO+%z^|2`!sjW-i^*Yh=m9h9`C=O+1XHk{O(zHwJB}s8RfE&Xq*&Wxvu()5I=dapI8s<JE_x(A{IuHtBf$}Un)>SByqyZmBzk&ptUeX<Ef`^==VMf?(Hmy#K{+V=_ebUutvkpCCatGp3{^3I$46_i(`(0$w()&QjQZNG-P`z>am^8W<^<K!+@duya(>uShYlS+vZ%TlM1y#^1^amB`iClZ}0b<J4sict_ZCiC6e->Fme^WGmJn;=$9gkspoe2bLO(61lTk&Vz87x_|0}2!n+|%cvnROIwKNi57lnZf0(SEY@$xC5T@d0T46b+Z=%hJK+V<0~*jff45#J=YO+<C4OH2$Vxymu5l$ICEf`#3l^P>K%AW%+XMN}_~IVW+49DIa5wk}q##(k&}C`SDiRsnbC6s-;-tt&Q+~`&{zrr3Eo~BuDYP8<d`>;2yG^{Y)zseC~(@)ms@>e&Y(@NyK#!%U6TJ_5Q-`%~#P{v=y}Fr{c)~*RqdWmSfMF;avRu7(DZHG)*^fWf=~a@#~2<xS;X_bD!b>v(8J>(>Y7Qn_q*No7NPM&BFSn2H@FNiubfug75d?bpP){I9T@w_W6Ef&T%<7PNf-jlyY&w!%URV%M)zY(ZFZwBLHFsaMzb~qL-$P;~FoK#@qXa;a$JLGh{d)?+bMLb5mx1eI>keyFv~wvO?)s%P}Z%F^mn@z~aE~u-QKhXE=wV{rZWhe<4lqZ^@$cwQT5n+5(*yI>}$<1aR^4X4ATqX_<{L9C+$U@@wYd>0C9K-Wm-`|5f73`uQv|HWbGG=Y@xN?L;lT5WXm5D%_P7;gP-{(Rb-&D5%$^$(x_Brn1xMrF|JTOW(z3!$+cdN-{33)WFFfhRCYXvV5!SZ`hNOkItei|7l|*Uz=YHJ1sMyWzls!JI{<~sw?xPmKkK&j2H;LQ31X=7hp)q1C@PN^SF91JpELR&nid;2lWAH-z?5cHlAUkZENY0>?w5FV>AB8>N!>|y@4$rl*XtV@=PZc{&m`3*f#zUs61THo?a?|+57T|N~kE8={KdXCn@ocuM%ij7z}PJX5bT<3m`pb7<H=ah3XR@VdZ39Dw%W%6_UOS3dI``pXy=5L=U!I&XwJ>h$1cVcTsy<HuG5^O^=NGfG;16K$olkaU0voG~DPRswe4l<A4@2r`VguocPUjwWeUR)E#)e%#e%Su7;~lWBBH`R|Hm_q5LwD$A(qyP*<kIe-}^Yu`&$XM+Sh!l0qn*?a#$}Pr=)Pr#Pzk0(vBL2}Kf4K=PMIz}<Q<DZ!k7`Q0L9y1w{zP8RM`@B*JqA6$R+K8_47hqrcR%udjRh2EohSmqpz`8AVDJ(z>(%5nJk(qA}!`2ow%a)h~u4zLXn#MVsB$I3s=FiD=G>zXt8V7WH+aFD0<W+8BXk3L=fBa<8$a0lJb+H}&+KiIV+60pPryWhFtv3fObocSLN{;XvUe`NU88)AI&zT1$o*^w(gU4cKPEP3WSd3dv(!lg!4y!~Z8IQCGATB#@%;#T@H;WIp<)3932k2u;Yaf>%q7;xtpZkiH^!YVVmebsTiVkXZ=yA9&mqTTe>&F@5|%7A~J{|mzJ9pZJC71;J6)#~$Z0eVdkp;4OE=yd%nz6m}??njR1^UB(xP^iO{6L;V{`2d(DXFz2qFQk0}H(uBDkd>G`L)EbXY~}9Jxa;98E}r}o0xq|Mk>@KA?HmaT_6bm4qscon$I<n=wJ33+5$`M(p({c{K>NRX2r7u6I$9;d5qprZs!DPnKVn)3p*QxP!%GXbxJ<b$ce4zD!;e*Pz}k~nKYPGtZ;+*jbf@w2zJbDF3nO6t({N}j1dtgg%{}CvliM4LA%BeqziNtj>|7e)9S>fra!W8_Of-6hcVP39IcWE@3Fm(pOWSQPB7fA6rcE8V?o~FVJYNZ0{`BFJ@?cWv>B!Mhg5N3$hKawT;lladbnhy{Z;UbH{UXhzuUn4mSUh6&8)l+4vBMtegP_r_OZR0?rN2CN_})E5WXakC+|CZcQA&Y7eA<Eiqh;wh4^>*RIi8KIHHP?&**I8|i@jUy>EBPWc(6u?+v#v{YfoW;pRdA&>EA%Q>jsk;Ih%DEBJ3C*hXoGT;AmJOPC6>Zx6hct{XP`2$^gJIUOjl?SU&6#7*HcW19tYi5g*DLL2peBpznXBTD{E><(agfxaLv1DX|()KdeE&9r2K)DaMUI+S16{mAvd93!2?p%AfQc;`dyw>BM?NZnh~KcOH+ykrt1LUbhx~J$561Dt4PJ<$L&kb8${O$^{a)wLtk>G*v4U<5JN^T<?}4WSG6g(+jKw#z7}pn5G6#`bHqo<r*}XPJ`WxDiKGwV7s7|JwC%B^~N8Rdq}y$t7&vy-T-8rP=dRAZ-PwLGTyf7CWaM?QGvJ`pYZz_-kUKUZY^Pubt(Y{a`dTh)kaY1ya@B&SPA|1c<>Kl=W*QOt=Kp5by?NPL>{3nLzz*nVB5m4m>;kaZeITdkz9@EyN}1U+k_BsWeja~2xLBG!I)%m6O@K~QsL#%{9SSxPPB_87Sp_Vko#yD7kCNZ@Bnn1H;F}_=)_4i&*6OH72!wA3`}g?$GQ(GVNhupjDInQcch&~nQ!hqen^U+O6`P2xjXQhdO!S%`;WH|O8x8eua{ydQa6IS7X^I0$q4QzB@P>k&O`j+2DqcUk~^9|6Ih*4<l36A!6~u}A<c%HE?P*%6#^i-;ek-bt`V;IIg=fJk5OQ(MTe{0z$coI*|MhLG-01MQJ7{;ug1#JI4CR|xV0N4mvutR=A*)H_1iGc)`+iuJQs-3BHpZh4wlLnu+1IA`Qj)CtX#f~)OfYv)YH4!s_Q3M>eEGFyx9&k95wmqXX_#N)NIJSyO?jvRK<`AcXG8S3)90p;L!*}?yWMO-<@X7r^+>A;Qb0nYW~Uony=*3KQOF~{Kj^Clf{p<KKx-(68sF2<ui3=!sBQOno(<yHq!&C$J<Ak?)3n-pOj^?F;=|MOWSJ7!4eizs>Ji(T(y#s4HMM$_d~k(Gb~>68nu5`p~D>x5h99wtD*tdx~omgqb9<k;w*4;ZGtroqTHizA}<Llg_#=+&`Rk(R=;eAFMd<N#cVbWvNb1DH$}jhi~GoDe`)%yPQb;(C~4_wL_bvpE_i6hzgpM9xZDM(TVuo<Ue6R*+EswIj|pF_eC!{S9pmkZ7O-koHi*eda>32<bgq#dy)o|&Zk}7rnky~nvPD`v!%mjocZw7mhnVqiuUfJFaT7+LoIvjf#Nx#h(){laF+T2@8m)+3L=`V;@cg`RuxT>ps`h#K*WChU*T)h&XKmUWCdKKMLM&_1#qnp=`G~`B%EtaaiqG|LpnkwyFn-lf_`~<$qdkFJE^y*MT@|^w(ou5WFC1ZGI{upLNLRmfAgpqp(A83mdT#zGxFsdaljr<`z78AyzP}I;eAb16wtLWeLkVJA-RYfZS18kMWx*x!FsUvcJ!Vg~dTn=(D21xy`G#va!~P}|=*xgdP?%srZ66!8>J@o*Apj#3axh}U5iTAWD9oR86MN#e;I;)_aQ>AZ6}>TrYfDU~Bb!FB9Fa&~R!{<g51e_m_`j4qNrD#@5n}X@_RhXA<YWJ3pSZo{=wdL5nkU=>aq~U=`%MXor-QkqbQxA&Q3gC6W98DLM=STg!Hdg3;oiEB&^_}g_pJVgeqX+j_L|SQ_q{yiOAf&F1cm=mWG3!deO(wf&tqhWlBo=tL%e5gQdA0sQlz3;MM;x1ktsz=5($Yyp=5l|+DauuDN2L#lSoohM8<r+KVV<i-utZm-1l=Ga_kfzHdu$@eY<Oz^!6`gZcw0)|IVi7_Z&&#opan@W>xUja1rr89KkfD9jAA=d}Px|FC0n<pi95Tv)8-Ufv#N~+$m+j(}D*B)Lx+t&wHqL-Gr58(zsRXBfBB11=VI91*5gi@c3^DGrUfjemq#pcF6TYtV|(ZUf0K|r>a!3dL3NppNXeu%TnRh=0s6?4KNozqL}js>vtj#nK~b3YIaSNWa&&|+edvm-*6$l=U0WE8n<D}xAD@374)N$Bwce*h2)H!fF6>H-h59XYquReF|&{Pg{Pr%s}BS^y#tNIWBA=_#uV~KIBTXyQCXG87_7LASw8$77e1WNmTOFB>e5=lQ`(2tU7AI|VJv&vQ;`&|Kh3_Fo`rYiTR=f$I$hi6$9xyCAXWS)VQ<z-x}ru18ja6`f5{R`E$f-BE5ad|-xDqzD2J^~6b1~<WtT0=!KF*maG<gTD-Ey1lg<6C*5Y<}wz3x1=N5pK>LAt}OT>#$-oQ|41d8_t!fc-mTrkqZ9Qx&fbNE)_g*JKUnB*gWE<Zwz)q6R~j0wD{>|#sR7@`!YL)D9Wu{Uln=c{rKh_*1~NYoW{kyE69mgch);#Zj;wSpvVs}Pln|H$~H=QG^sba=nQ0F_Ikn1-j*Ii=a1D77So&4^GVz7v0N!$1W_USE%{a$Iyu5NA2RMM!FyIJxdP4K6nxWEb7CBklqD@Z)_KH{{S>Toqb_KE69E)_yn$?&+7nGQ%IPtk$E;u2*pVlJjs}H3sMRPG^k!r-Q?07a;l}Ol?6nca5wFnq}SuclGm7-z*6?*GdoppCa&o)(2Zho};2=15@5*2m?`J>}<6%=5gQ*yn0N7{C#E#?%Q9pi_7`xcx?)^Wt$4ApRWoJihfkQkr=EvceMmPUx~o;b+L^1-eMF!a-6%|;sRbh>IwfHJ`0zG-atlLEY`?H;y#kbUiYhq_%SUe(Z`%AO_qYpr$Qud)gaR{zJmRDT9N+!s(|xutY#O^^u~iPc=49*PkgSWNTb<N;GF!+HtZ2*^pxMQZd&QA{pUEipyN;YE!Lq0bp_VzBwiFd4R>$pqDY}C%vOkm2X6-0lXLm#V1ZhN^0)gCxn+v?z1M(OFAn5R-UH9iYGhMH{Qv*ua8?XPfbc6jx^=fcse5yc?N3Yun-UjBwB3%#jK#8nI+}E6pd!uK@Cha|zHvWK3eu3%ES*1j9{7F><EH&fQITndK`R~->a`OK8|1+Kl_ZJ0*v>t`^B;UuzK8YhP54>qCyHD?fwvL{A+L~!wx6=Y#505V-#%r+cr`F7JJjf0v0ZHGDhYb|>{oWaZy|KV%_g^J7!dLI(=a?thejT7#=0v}Y+cz^oc%_bZIV-=S<<iB531tCLsAX1n~kx~SBCPOh=aX4y5wP$9_7vErK^=5!b_LuxKVNr`r~y*Y4bJq)p7~?uhfKO{t5@E)}j)nFWJM~IOucy0O483pxFKn#0Uig&oMI+Q2K!*7<2<3_5R=v&k&{3rWI^wVGnGXuSrUuF2{c};*mSUjPRA})1f`#_`B1A-U#}_yqxL_T4tH7K&})?xVQzj90<XJ_0!nW#q*%4cn9rn=LK(`!}K}dN@m7`N!;BjL9w(2L`2`=kzXCS>x>_b60e6yMgml<(rNqpQFdiTA>I{Kz??m481*QX?VIaC?hJ(Ca=ivT@qI0{ep`+Ldrff1;s+Qeu#ZM;O^1DNCCQ=Rp^#v&P9JOSz`>&i=&Sq=xYdrdW??dhIn018g^lbzX+6T#8)tmXdePxbE9S;g=y`dcyXM*-Y}e|BY2FFYTV4TYG*e;Mc|Fi@odu)oV_|i`AGW^El$dq~6VFrn)Ic=_*GL$%PC5BtGC{G;ViAoQ&>>B0GLb&GP+{`kj+*)uLZpf$jWqp-M+QrvK%xgqy<_m)Z9=|2wIHY7wBv)9>72@M?{TEA6eaE$)6btQsLP$%WZCy`=w!e{|K<EZedltxNMFOSCl^P;XM($2Al)jyi}thbjB#Zn6O^}{T-q&8A43Rz@TDFcCZe#m@B=2NNztTDchQ9Z3`kw`1?l_Zpey|g_oVKk`TeH2_)ZGEIjl*i8O3Ak)gExV{fD_7GM~7v^M~kdpK$FvcRIJ;lCbk`vVT50)A4EAAn~FIPEKD)y0jJ&-Jm(dIja%&N(+MCD>1quISBvDpH56PZDI287tC&o;hf5Cf=9lmX-1h9-E;(C)$S{}w{w(J<kpBUi|>P=wKBb@Q-<5s){(QTy+BTFH;Q-Jk{4|+S=%p8v`L~C+7_$POUfDS<1;O|=iw3b;=2NQFJCkNSqx(Kspo8An-!T`V@(Q}!}RmstDsoAmL3@0MittAF%ui>@eP&%+s#h}e;Cm~9dYvh(o5`7JV`6%wu4H23G&C6qHX9OD3Y)u^vFx(`6ElkWp~35|2?EI<{Ou6nnh1Nc@0;kq`*#C8eZnVg{DDAsz2pa+xO%{XQ?<^XHm=zuY$}}XLu67fgCz$4hE*5LHb-G7*r&|c9BiA=6Dyj-Ae<jkz??8SdBXNrNU*~9H#9khX(g4&`EP85;Vt>Qoon5?6d;diW1^x0Mx--i4MJa0`FXviN5qa%Gve<p1jwiC)OUNItp&&LDgzH_oyH(sp>>Y>t2jnAwvW!^e`z{ljbDNfTKkd(0O4LqT?3O^;K6e``1)f{pSn9ao0d%1o7&AT~ZoqL7z`P$A3dT?6yk;9h}P1Kj|2T#~b47{r1*-tGC1J#2D_$6Qfx0>>OIMt6=^1XOJi@N=<W}>3PXya3v|6hd$Rp*gX-9R6OaXnsk)(eZYJw;30x{Zr~!mSTL{igF_l~$htq-oHwzbthxl|lK6!}WHXdvQlSNg&J-gP_7}Oy{DdAgON8FT#wfz?Orrwk(`x?@V3KT2buu<ogr7~~NGVPuabdUEGh1|NeX9@&IO#>|D_^6lX&hdTSVEPD<){~L9mM9GrMx`txNX17R2Pz=KX%V1QkT?m)wfYBdn}5TPD=nzsga#|d+8IYyEvy`l9m`<#_psK@V-Wua!jX~`X3*iv)r2QY*4~pQDxM&_=IlT#xU!!EH$3f#SYf(hSXDXaFsQu_dcl6JLm&KSwA>mr>1Z9>G{-ux*{n}5901tiihalw=nCQ7+q@Ij(j<@NWkkU4&EKd+aun@_nsbH*wPFA%qiIYXA!B6-w!IOv&it0xioZU3F?i9bA|)eu`1;##!R{~9o`F|d7cOrJ*t4sVwE61)$L1z&VYN`dQM*KAsF7Zg*Iw^NA-InVA!#SxRwrM<|Y|*O7y|`*;lb@I~M}dPtjX#&vDM66y3G-FB|?yh@4nE7j?CTXe?hdrU;cn)a;k+CsSUkd|(a^WZF#KVIw|zO^Ato1YTnai4o4np*ef;n0_?-D}t9wf-BX=CD?QKA|`}9XOdQIg~wwn$lu9Y98W96B^Scj^BVhM@X9d0w$H;dGRl!&sR?@?D3i%83rO4f5)9JSrO#tziE`^@cvHKM22VG(x~*If+Mmyo#F`9-_qI4W8}u7aI<6wo8iP0*8H&z&Rv_k>j8W1u^u@{oIKb=0{Ee`nK9WW3!%NR#%UWqFKk$+{cp`7gdphBAcNM;0`wV_QdjU3T-LTaCHwy32B`*7?lg8QBv|Ifhlq3|PZ(Iew?pTa1dxh!j9zpU%@Ew|nY15R){dh=9ie&r?ht!5ORI}qEm)A*}Ui&<bT;ON5Lp>pKr#R7<l%TFjbvQH94rjg7qI}$6*zKbW{o%#<A^0SlhPq622!&V3Lsg3z%1D+mwOd2TU(s2VI{|b>)FNWcOfo{T*I?3HgM3S#N!`XRY4o;Qa9hiR_lxRK*j^a&{wk7H)#k)?vmK%D2N`?Y6J?#;Er8W|TyCu<re`lFTCYXn&cQRx{1#~<>#%~XUSdGKGFPy^Eni@7y%hDnJqv}`WzdoplT2o^E8X!yoXc4vL&Dh4Ajc`gBe!Z{C$BJ>ryWWqXR48*d|UcfDG##`$j}S*{TMQAOyn%<FuIY@Co)stu#ZD@Ul)SW$bI~>O$gfa*HP&^-6)*W2aEX|*jFl!ByPGA73|$fUy~uIi7=+}nijNyuLAs|`Vd>@;_o$&LDp6dR;(1Hy@B^JC(H$pJ)A`vpIpav)+cemdj(nA`~?GB{(_883`$Isg3b<enxs1m3_qr_8Z+ZCc5WS1C3(RaXKV5&KmcOehL{t_&a(O040%<`PrJL%!as9AW^hac`|oA5=V$DId&8NG{vu&|Om8VQI4neXtJ^T{*lqS-mjvBbjn+Y#0ch|+6Wwfo0U$$CoPB83Dk(Cv>m$_3bYdk$!3;YuI&owSV-w`)*u`h~hIt1QnHTX|?Fa0(7NvRb%)q+Bk8x-fVYM1(5StI?)HnYStoT%gwH;2VoNh;E9Y2pL0XOlYNi>8Um`$JcenIuhc5v~RrJVBTFeuJTM8b8*;TNu`b@>CXS<%N`kRwDA=f$GZ$4Xqtk%fk$KK%E^0=Q?#VfuImv$#;4?#;+&I&3T1H=(QWn&4@e!FkTrzsW-$WFJRPS|3>Ty#&^ylX-4Ah%vvTuslqdh>9EIXOHPbchM6l*eOl?^p7!yeOj2kT^GK~7GmIB4-l7Dp*xDCSYFE{G`^_M{PDFQem4%mm~tCd)ulj32;h^AshnxkZ^N{mk?gW>QF!HzDfaM;v(wJS!K9-e-B)rFtwoNp->am^dJ$>z>f<eZFXMtAWO!(dx&j%stzz|GN3grZ206AvRqP6@A2{;GoVXc3$9_DENg<1vuC6vry*`_qa$iWs`(6Olc5}D^QOx%8Ja`|c!))dL2LBQMa=C@^&@xr)_KBI}R}%^uCv-^Ej_3HhP0m`YHxi%QKF6=e^6_1)GdLVgWyfuzvByuA<%?0}T>7vUXUJE;EQdH)HB<|GD}BJ{n-4kLSily=P6LxIQh1<nF(^OY4M|}U*nQgue#`I>O^X7o>fyyj`i5lhBR7sq!6^Ps*T$CGA*OceZ<sJo!}ovwvXwtOaMVB>mxDiz+kFnk7R(@X%Nn6+=>TfFPr}!ME*NOo2d~vbxj($3u<O!4*q?4gDvY+no#Tb<twRSfLisftBJRy@Da*!%sinxvsMGls)=ct>Eby3i2PHEk0lp72@wbCve4jOuKbZhyZ%uIe%sbHRE{`f}n_<Zfds-0M%CWi94F+>A<Mp3EP^&l^7Nl6fy{uN0nK~o0W*ID=HkIL~T)6o*5**Ke;>wqa5@bv8%#Uz3SGNe4e}Bj-PA-P(HWf0bTafC{lB2{_oYL<?;L{;Ta}_2T(@_Jua7P3r{J4#7tF~iywLJ86hZF8{5xS{Jh-3zDB+jpQ(bjEB%(18H)cBMNZ8JQE>n9zs-S{s2_fv=3wzeVnY6iZTzJRPzIR$G6QczUUhAy~cNEiIk1c!__aA#&1=*-h3iP8J<gn1h#-W0%@;r#SRWer3=T*%^a8T#$63w%23NwP=3;=qbic-U8zzE^eQ+Keg_f!0`7^3e`9;)^ul{pW~vCaUC)UImQ!TLRbY2L7}k1I{0J<S!7RJB>m?LvJN$M7@P2Tp<$sJz~lYTbPOc?NI#G5g&T;ppLj5URN&1i(53H{$V_non8(>Vl1u?55+c<ZkPyFq>&nV?C<`6=zZ)fypYRhivOyk#j|wiE2hBHv6MDj8_?lOeo`9Og_nP*S2R4BL3cXKQ|YOF=UP2Sr)zVuyWfp|RFR>Am#WZCbq(3=bQcs8yyzS?NA^^s5Vea8B7yeT;G)ZBm>;i1gpcykqJK4boX-}U!WPiky3g^1O(#C>m!fMfnn48YA#T#u;E_I^?%(7`n_FHoPo$+u=ch2t*k6Nk7pIe?mPGtj_5r#pZ$kf4c{=;c5^7SZL~I5Wh`9`l6N7yfrnlx2D|uNG?ios638g~X+9mMbH3iHk_;ByRBKF&N3o<9@9_oCWO@HjV0RCNF;JZPZ_WD>+o^dBoi#4Vvi*qsSV<e*;DM!-ex^S333bt~?iEXMQ8Cj5nUe}+1@2+mBZ1JLJvRUS<^+S~RP^7c-_s|4`d9?YcJESLiQ)AT==(V4h_Qjk6A15ASw5bcHeSE`N{$U<18C0bKNwOe!Sd5-XQ6Rx`h^J-BQC;RKx4uY+>h~UChwS)(*MAl@88XF`xG2aguLrvs&oD=K171}Yq=Tv-!EbCQ$}b=^Xrv6X@4n-_wUdXw3;#j8JK`>rCm5aF#B6zC1ILVnsZ-@&SXb{xyz@5`sRMtQfxS;5_>?go9ZUchHkp~}H5YEqw<Zz4JJ6sy8|~%%&{5$uBXxBr6)q8@RU-*(zp4eA6zqq8vgYKlydDYOuo~A*7GuY&O<>sC2VZw@rX$yQ!JzyMI)(P)hI>lnhWj7v`Mdy)o?fSWp50;^9=wM`8KD)97vJNDP$l|8)PnRJeg)O`3+Z6(u8K{1J@~!m4rZ{C%(dzJXy4{Y@+$BIehC43MQ@N%&y%Ke?^NLEj-{A1T86i)yqM^g_pm7L24}zfBubBU;teL68`(dTaP;o6awAeS@rNu){UnP<j+fxqF>g?L(up;7D)=n45*95E!a7pUD$n#GFx&-_pIaf<aS(p$aiMKiD2%M>0KM*A=&|b}qZfN0_*aPIbD3SZ&{CV^P0k1ZeK*j3?`dqxdkfi}JoKrOCA@t63Rdz|0Bfs3yJUti)!Pa#WL$-7qDuIux)Ve8j-aBO8NHH!7~<1KX{<&PW^R_Iqg5x+uwn!*MifGA*dluMMi;&a`p8D#xyxwUZlxpNnph=SCz{&i2v@(`F^g^u;e9q9_H9{2|C!y#pdf(L--d9S{tSAm?InIa`UwqJoWqU3784EH4I}4f;^}{vQ9M|Mctq7g_eKGl7Hme-gan9*_95m}-alrY+&B1}5{nt%DltAxjP%VGgX++4xM|5&VjMTh`BCaYCf$~j{?Qj4+r~Md@|vF%>}$c7)$dppTn7%fV<F+B4i(8g&(7Ao&V*WJVKwhCWM#HP%*+9da=Xd?=+hu|Y30C+;Z&&S1BOWZLdX;|ZOr}XZ>Lg*(ay(<@s3n$xfGf3u%$I?53;$g=Cq<rg#P(n0n<%gn3>#GJejD4o@a+(oWBSDDLB)`VjlF4oHYF#-hl@iaycri<mj7~{AA0Z75zPtfwNX9l6Z^L7<J?t^5lu*SUy8HOZMXpNf#(46yiVe(MqUdcXWROx1cC6Zjq)RZu8J<zikPBPd&T0F`7NM?l(A3b(Dt6DG!gSf##5L*g0lF+aKx?N9|dJ(LX^9f2V<yqc%Gyvm0z>cQPF}m1)ssLzreJ#-{8urmsG`(UD7=Adp?mR7yQZ<8M5yR0szwd<q%gEvKO7hcan)636p_Y3$8I4rmZi43pdD5swob8t)$t88>zT&k{{~%0CP;t@lx{(-rJ+eGeKbrnB#iXOUaa=A(S5Cmz~hh$hY9-1YKt@M_6=x->u<y)=3;B(MR!hSE^=N&@%aX?5b~-;5?N2=49p$~tb5r(s@o_|@wsRJ$usKd%&~WY%)n;ncxq-Z(%T9%<mk{deF(N(C4nA=q5KfNE}6CnsWt&};Mp+_>Th|C(1()cA%0E~yak;|TZ}U80Nkv_jaoelQ#O53b+PBk9Yx<8U1>^qn*&)&ud_H=&9N1*z!0b}b#<+k^VfhU};2d2pz^3-`@C&!i2fFmrBc)5`VL+(4lVm^5|{{w>yJ+^qlMoN7f99KD7Z#d*+cQw&v2Y-8=hc<AOi(HIh?PPfKOQTxmDFyrP|O!;mJ8BNu&_^3I|dbWV_ygyMs>&0ARf6$8V6cnVpZdxH0=HmR(B=)E{KYbbc2^ySs(x&7pHss(o=<%3MrIeMie(5mNpQlE<jAnvC_HSSY=8{8+3bcB{g`TRu%w1>cLY{#J&h@aOItN5Zb2w$quBj6@j~IAgBT4Uuyu(d{H(>JW7({jd#c4)0;5W!a{+?byJ`9yqxafDo>UX=*_wN(nd<cM}G>A!*{SNab<Z;83KD6ul4C*Cuu;$$s`m>mau6eIa)|LrSB6|?ZWBsYawh0ha^}sRJD7dlsAX#xmk<3gLrR%rdgH`hsU=gn@-E*Od%{J4b3u|toeYZBfB;JahTeQh6`#Y?;yfk&q5u%F&gowRC8|a14qyy(iIhz+wvCCNk-6TCY@0NCAmj#e{;nVTk(`V>9I~mf36ln9uI=uBt8)pc}(|GY*)<IZ`up<s6la-^e#GGzPC}L;NXa$|HtLU-Oo$_tp58Sb6v@f4Vig!PN?A4dR!QeTn?@kAkd}q8!o?>H!5wSWF%{Z=JK%RWwMT}ckz*ziiw$LsLlpp3ZPqzucN5$Jfg#GB4UnScT#s!Us_dzb|6WX7@j_*U-VaNP-Jc42DSFc^PPfvh6bXiQzm%XjPfZ61E=0@UjW*S|l*#H)B1hg-FLKkICLc`7J<8K$(e-4Xiz~*DrsHz>z1}!Q7qR+TMRhE8urVe||J;*10VLIV(4^kByv9U4|RBJLge8CQMgH#r*<C{gL)IQ;|Etcd6|3VsJHJ{G^kDo}KD??GiEznc)h0)<4=nOSu=TQg7&+{^~q~C!&bXTQCD{WBcj3r07d_H!3e*?Cw`oQJUDcrs<2yYkLfv}+zeD#f}7}u<ZLQ0`tH3WPs4dLjQuVAdXifnixP9)URac<sKoEIucG8fmO)wV?#h=(9OM}Q=Ihe4_BKYSFn8HSvGvY$4TL5uSen(7?@-Lwr~9iI<1`MKcvtQ%Y}Y(a~dTqb6d0k>t76S(iaz_iv>p~jc*XeG}_-};Df7YttJI`9b7nFHSF8P7%g?QtL_B2HRj%W!-5YGVAW6yx~r!^=8jwDS<4J}ZAhgcL8Gdvg@eZT<=c`Qo&;p$UCtU13pq7<zaa!FHEGV!3nzgqxS+Sak><s83>iROQKJ!9FyO3Wi&&F2SYa+c+*~uA*p`JxtrbiR@~t!CjBdAW!TWTO2Wi2K2Y1Nkl7(XF0Q;;~AXuQUC#MXk8d8#>O3}frN4qJd-O<)@y}>H^+?BnZIXWI=aF=rE+#d=RDZ&H_6@%m7-&g(nMryH4}c>nd^I>ALjX+5D%U#pzd<4bXg|Kgh`ONw+CVVwO1IkEEhKa;Gs2<&FmH4B-}V8PLop`VR+X9vbyyq&Mwkm{PIe0$!#gvD&Pg$qGxf&%}tEoOf|6R5o6DWSwf>}22TG-aPZx8+{^EazQIOp+s#+_c6|&=@1Dun+sPtV*dB5iQ(WBp4zj&iQ2RTL)U8#b9ri_Fs(l5M7wCZUuOG~bQU*@l-o|;iOqeczZbb{0_(A>=El{{_i1KH|$(}zW%uy>TBG&aBiXJ9F(CQWp{sDwjb^{xEyx{cGe0HDzTK2&%WonR>!z^_Cf&m`lbo}vpM&hp$;rsOfH@%sU5=}|;d`BVky``Alk`s$IwE^s@)S0AZ%|mR_btG+3uR%4=6E|eXa^v=eGXLghqBqJ?=e^d9cy|fd4bP`*_(af?jHC865v)Q5qTL{gb>$Z^U#bW58n*!V)g!L;`>9T1s01hWw&S0diD<rh8`^eu;=JRx*uEuV^xNYEl)EQ`-aD^Dg>oztaz~!7QGJG2tY&isYY<#QI5d<7(YbF+p;CH3Oob~~FPDM$p>vqA5ED8Yb`tg|@nTwk9cKX_AJIJag^89p3r}u_L%3H8WNrAwre0b{oE^Th(!LKsZ_6ZOdF?Z*r9VX;kB#JS*h(1LAxLj3I>7db1bA;Um#(*(j(L(&=*ay8rX4OgWc!O9+3gEXCbs0~U>CkgHHOKo)zCc4nTG7Y48|`J59&6eM0FlYu4_WBcLx@HdXC&c0i3sE1#Z+eCo9)=!q1H9RAJF#OkBB$E&vv+w6z(-Up#E!eQk1h))V$^qabaQ42H%DCHmSe3oknSU=!lRXfZhg<|hS6;Bgn?alx7%^Wdl9b|={%dl+K9Xe0B{-+<oRZ3+1W6?nYqHN*OL!GAesXxMH?fBbeJ^?GTrYe@*rxgto8<-G%&t$O6dt~F$g_bqrjrQ&__XvQh>61ul)F%^rOA!~~a-EQwmmtA}dxl7k_c=slu?73KUY_%fqHGgAW?`O<?xfxe-50Rc*x3DHNgBAW$4hDOdlRIyWsn==~So35l$=_u})3!CjbBh?%eZGfn&6J_B$U~1gEr#^VZZr$x&>n54?ogB1L_dU^-@RzCTr_U}a-7;_N8)3X2ONb?8`ybm4)LzJhwBDDV$oS!{5DO9zU`O8^_JmmLYNT_e_IMNVck%@J`!GgE~E8V3UO@U8UC#vh1xAb+%s|USX=2s=AV)Qm1{@9dY2Z_nH0j>mrLoTzU3rWC=(pUPSA(Zie#==4wOi$lZ*P+Br5+h*nU#y{wHfd8kvu{VPia`A2-ECb*C_KbR%7`tC_784TVm#bL?8t0gPDP4{-~B<CU-V?B@s8@H=_~YA@b{b;p(H?SnobVBP>H>x=QrpJ{aDY5^2)NTC<2%t*_|YCI%5j@K3VXy(jQn48)S1`F?@!nWx&Y)qIQ-LadyBr^zB9&N)Fo77;q%#k`L3ZiEFBJ%6L5Ll;W!*Omg##k%TM_b0N=f85osIgHr>MLMo=+vW>QzGa7eF}|7m%~oW2`udtqQ^hRv01`0g#DwA+nOcm`ORG*)msSfyB@%>Ry{cAYg3!GinQ*NB3a56r)xeO#U>vs@+RsQ97&ZVfeS21p65a+=#__HT`6MU6HZTc$3X2pB{;wDHu9`_%p`7P8Qn)&u)nw)#|m=M&8Zyj?=QxbBw1Yjx`+MuU6g!o4`XY24CwwVGT>X=3zL2(l*jfdez%rp$J=Jn>?UEt&6--jS&XuI;kap~7=0Ts9re|FI93wn_)N(O|7axuZs#Ro7Tc(At|`5J&XV^2abah#bz|JNJ7aX+bW&#f4!_M6qPvbhX7UCEXm^4nEhsNVQ8i2Ib@VHI?3_&^6Y61|=SeEu<wy;ddO@(oa;D~}6<z4sifWG^!JcnV;n2|#tce_EUKnnpn)_|2x#e6q9%+XK6H;{i9ESwE2*R$cA24R=4dYHRa8Ill8y?RlO0Kfh9N&Y`iTEl1?qV{AMd{?_t57hHA9GJ%LP3>>c=`*6RJa%6`e<jk(IZSU{>&l+uO(@1o+fpRy3AzU4rls>6sdaFGqzntgg#G|rSF|hp^2HqA~h>oT_8YHl$wx7*Bnl6jI(|irbEyFQ$gQ10?@oY1KeljVub-8bzbO19iD6iqm-kdx}h3hc4?6>QQ??#WiCFt5ymMMX<(biRj}z+3eHU&N2%g$W_#~A^Kjk*5@M5q_l|2*x!y)twltD)eR~;C$E?EBTYF&lryH!}us73{69G-hO{ki4jDE9^0F7}irl5_X**%9rD|84J{d6JC^>1PCKMowtIm_;so=ut-w4ub+D2!D#gh=%~^!pZ0ck%O+>1Aoa8}%6Hz0pIwID>?)9LKm@VT_OZXLxxgfty;W0z>wtI6P1fX^EQ9?Pp5wwypvl6eeGj_36A-?ckItLEk!$vc2wVbYS^PJg}catH^WopK_Y}5$V|BrG~2y34mHHA63{~i7GZmG}ogAojh}3qnrYy|5-&IG`&GHeO20Stqil|r&F5>2P!=?h8-Fpc<Sm`?xOqSU~A_<o?lX<7E@bw)y?Jlo1GzR_lwdQ0%Pnk#)NPLmGE+41itukgqbtUOM`j%=urD4H0YgU%qz{Q%Ay@$lqEp^+>VC%M`eg$rUaRtrihk*`=D6wJ-QCRg$>t-7}tbEys5mCwv1ebo0-qp-A|2aD&JPvR2B+KDT?&Rm<IMvno{H87D({_#(dte6UA>B5YTRg3n)Ryt_;A$el?O`GL1HT+`;Xmf|PmMjs?0c*l=(JnO`%BpUO0<yKX7<FwvmXH~q&>6q~@R3^S^@=Kxkd-%m+O7;fIH08fN}GvdkNBy#2hu7twWEb;z^^8RdexXn*}=Nf?SljU>-48euxG3<RMO}<sAQ%5;tbpNYLqmvTAJzEijPI}W8gB}pRv4HlrMZ&GUzhKg56{L3cq5Oa#VPw2<NUNMp4Lu04W>=AGY)v?P+9bn70?l5wvXi?H6AJ+)T=*>;mbQqJuJFA$m#-C1ZNC6@p(-pHP^Z2962#=TKd7&oPdUc~Fd;~o@>^bo>*0OO&P;~6RJ#pT6~&0K`x#td-oV&IWU@0BIZ%JeIjCTuPg?`i@uk6Wy4h+YN|xD>q*>PV?kWR1Q)4~dld8|ujCqoErfPUl;32BG@1#nHa!}E9E3>Ay5>KC!BXRqdP}%0oxKcVFcGLrvStp8Hce>+rZ6VsBx`q4nix}~d6eB`|O}OQ_E<O716Lf5R1nn^fba<yb{kSgyl)2WdzXpfw!0%u;m9OQEOE6nnil|UKO5XBOHM|A+J63@6JZ+*E90sOdh0LF|(@5K+48~c%35OJin58PS$Q8j2*c>t)6a~dmur-AZ)>%)5hb1V#bPeoEkfCE{KiCF|B3$BI!VwQC2QAaJbf~kBX<aBr%707Im=+GpsTu(tA065hGXmMmgu(6NF6{ZNOU);~VTk%8NIt%dz7vYU!}blBF_I6V2cJXJ3^CeM84k~8`Oz8quEcD^ahNQh3Cq&wlJzDxaPH|EY~DH!-bsVF<*XImddC`a?*2kJ{t@KIzd)s2E4a1rqq$2lid952s^SrFef<dP9SWn%cQ3+VjUwjVjaKL>-vn+KeqgtsI(@gT2G;z|Va^w2Fs94hxFL(;L0?;o#H_dks_P$O$gnJV?Ual&l)BLP%N9mdbtNn-GlL2dmWecpf(uKc;q3B4m=zg9TRiLFNLe>_{*Z!+^TKp`#aukGEsXu`=*+$>up#jz8g$J1@sY3!aW7lRqzT<*#eYA9)mIuY-|P^k+XGb;&c+8SMsQ%|Al^2(40co8J~uK1DhHmF%ScGm;e8e0CMQMD2P_4@;_J}ldjz}cqrrd04Myw|59QBX3!BG{;J2a&PQKgEEcyfV*?I<^;C85UIDwHV^%(HR5U<YTBkilTNbc9Km>Ki3;?@2D@V7Q#ZhXtfn0NVTUnWJqYQ1NR@@2?>EA#P#R51FpS8#Q$FE||D#D25ZMkDdppz-Ghd;IntxRmV=Ip0okKA9#k|9Y3gqMhR8M8jp&zw{JtmQeP1dK7f_1H?upvU1%I;MW;jZmp^U^T1I8p52oqyKC=rca<fvU1RI$@Z2W&IhA2jH?pAQoDr5}>sYxpm9VpKy+G~c2K@U^o!rV*fYafPI76n7{drvhy>|Ry%I9x^u3bZzy7LC`+Uz9L1)|vW@DTQpeG48PKMWImy{ME3)UHDl%Wk`&Z=C@i&>Q16eSUy}*_Eu3EaLI`7Hm|-QwWj^wC=L$V+_72L)qvc7^_XqY?l|b3_oIi?6m>=UjkI5z6Rqb&%$Z5Ac)bhp>(YWGvxLe>a}Fx`%ypmzDj_YSiEAze<g#v?Eq)Y{1V94JK*j;RTw8APtW{%1!vkE=<fAPS;6uePILQDkeU~dI`M+^*269|nr#554wZuR-3wTjtcfu804B!~jkXG-X|XA}BCw9#dSw>|TUT@Qb7oLiSp|5b+Q-OjP=F-yf0#W_mnzrjQCDMQl-;jEL~~xD5#KVrSzCYsPk3pH(=)behccOauopVrqp);=0gbyQOy@cOLW7cZK;0VP`+|QMf08iCSEE3&vl;ZhPNQ))B^WU8EXt4i!%AauYPKZ;^(3vyvchoCKg>srCzs%ntJ1Xaelqt-A`h8hp5T$*B8=fNB)5x381*^ZARzcG)3rX7)9S8<rYb^YEgCT!WC|d#vk_C?p243RrlE7pUMLQaLW@9YY!F(<c)Q$%q>q}ks9l8Sor=erP66U$!fXA<<v&hQ!F+W3;L1g|9208X=mOCh#2WbNet}??Yj**6u6CvMBJc6j`I+dVX2gu&Q=(q{UvO}Z3^hA@7rqwBk<HQ)IKM-Yc3u34>Js@(zr#~#{%J`1)WSjV+9j+=k*8$&13Wifnx^APHv7K_40`+tlZTM`8RJ9#`*{ya-)^B<29+q6n1QT7J!q}|z+Nxfk4_c7aO#ae{ww5=IiH1bcCs=3uv(1@B)$a+4MmL5`-=IRW<*Th8YHJOXrJn7SQ_^go-gpiUq{lR>Yy^UzFiKR1k$XX*E&&|IiXZ`+ecU#cYqB^-$T_{eHwOg4Hj|bX=LSeTBx1|fwgbok5&b~o$?xES$Uiu5kWQ_m8VK8Z{Ukm0oKD~7j-(D03~ss**q;HT3bAX<MwCJH%JF<r<}j;zc%*7#(w<eu0f<HjNsrBh9ewiMFl=`=w0R?E53rC3`M7-?Q%JC{rWF-J1IxE%b&y>Hzu$pP#oWF(x6tOE9mCLX3p|un^{%4h2-L3I7ayF=Jt=AVr$(uflB;AI<UqQ1ESqXu1z`9m8MKSVJK%n;WX|RuEG;<)8X2&^UQ)Kby_yhgLpU!kfh0(#8{D!q~CEQtaK1~|Ly~>dK0#X2f=ll!|We9ReF<~gBrY6#8onhS^IB2{W;fyjtKX`-l__8H9L<7_$QfdG1JIVhZ1y1m`5*cFht!YTu5AMN)KI;p$EJo0N-fReai2dCDt=(=)Vv6VtfEq9J|<HJz=8q3Fyr$ndN^!tI<Le53<)|A?bJXqgBgRSENfM;#*-M8rX3jvfsoYW_S>$dNqC2qDZgUD>KGS3)<goLFeFjHtD@23LMI1))j3hIZIp!W8^^RuXu;<b5_%e!!yXO{w&OV*@8QE-3CL=23G&03Y19HFtgIMSiU?ZRFb<5EA-#s_ud6mx4sMX-BaM*=RP>=<p%o>-UPFaV)!FKk-P#0oOkUbd~IKeT5J08dU-e|N{BEeD&L^AGZrJ8^Kr(nmr%LuCSHimWqmy!Gs|>WL;NLWn&kTdXY-6hvz`t4*3pc|jRdKPdK~+sC4g3KxDP$)&+x_vLYt!Qq3bJMIy0~uuATVACap@ud9DsPey9s1E5~5fga8fXoI|f*J9@j=0bQqhV*H2@b2QVHbatM_7lI*FPRtU-e@9RSUQycq#+bde^(?CGu7SaTlb}D<;Rbem!p4^4toPC*c%D-M$=)YXOc>}s!BA4DQ40em@A07dS?J1<g{Q0H&?@=~Y~F88wLhrQi;vVu<m(}5;r2nnmtJsm^oF(rL#(vnd+?M@V>$b^i2EsLkhyjNe~vstVrvamK5`_WeHd3>&O@go9Po>sO~Y>6Sr=|N1^sz0WWc5tT$e7V%%&+vm#u~pZz(#qBOIS|JHT$B9tZ4XiToTh)Sjk6XSCnOkkT4l!`Tl(E?3aPaSl48DIOb^AsefN$b79F6qV;g-UKgty2=(#)tO8gLp=9%BM?<fVX9y!PG|UaajwqKMS&wcv~`UHS>|vbD|~F&O?w<jhutL9>@UY^-vrD)VMu-Y2jTts=a>*3jfeluCdG>DAf@UplYE*XuX{x4uvH4$JnM(xCIRA?y_SBp{0};&PXO=t^<<>nm_+!Ck=!5Kaoq!55?)<{ecGbrNvaN!+HnJ`|J=jmz$<W9M-W6K=F!4yycoP9pG`?>#LF`eVBvr$##q`DzhX%&OFe@p{G7q9*q>aKZb0b)Vd}Pc7f4ZSI$p&?%3URC*)~3!E}Mo&mENGX?<iFI4WNVdtBP8<f!9kAQnHe#*0Xr&_7wL&$J6ip$!O**PG$-IV+-bLk}UU`(B$Gs$5Q98d&~7`)R+JsvxvmLt723~=oL&jx|41ZTk^9@n3nC;pb>h8^oWHzk;@aNFEbl3@nj+Fus(;}x2xdAG%464cODMuTGD69PUP|{TY7MbFqL|G9F{&2CKYd3=+CVL;YR|HEf|S5TP&b7))!w?h>=al_@Lrv16!wj0qwHY;B@kAm_OQvreA8UC&m$CUGs6KQXJl!@`f$5`N*c(Ts*NXA6<Jo7;%v#a8T5zx%pyrt+^;UJYqu0g6Y71N{67RBlulblR6bk(ked}lAbO?2Ukp^+&hhEeZvEuHVKngI~1vlXB}g+)Ri8%QODMNP=xR!=P{7+CU0)~<Csboe$d^@7+bBN%Uw1x-)9AayJjO^kQsnUqev#8*$>7H<!RuC0QzM!p)m&~$l)VO)N#)-OdAj*=kyQ3+y6w#@AP>1vGNs)eyqf|YfTBqUz?Ww<)u<PBGBt^Cp><d&K#Q1C5LP*$jCos>+?!~fIqn#TVL9Oj0rzEdH6F*IYq<mIcczRu>c#>fLJU%h_B~35}8*kISCIR;k;KH*u%SbLE}0e9Cy&76=x&iPj?cW|NI9P&oyJlf2nW^O{wysLZn3{c;MkGw9!0-1HA&YT|Ngyzjol8<Sg`HoKVqBofPemAPV_jRPH-v0>ccc_=_1heD)#?$#KB)vK{&BI+ck}S(a2*u^?tZM_*rJFYZtw+kY|i<JetTG&=xV-*rRVR(@KtGXwMU@38wM%!uIM0`^p475;3m#-~+haN1`h!j4{Ni@IV^<X;>XKTU_+-?Py4t^%EZZItt)wE<f9zh#UEd5O%jbWjs`&+66P0%H6HN17WTX+g$<wzz0Ec=j`<zS56-{JRQ~F7n63jgQf%;S;>jm0*wDt;4O6VVK;$6DAgBqyB0wd?t|zrl|q2=64;gAM?g(hCvVz;)QEVHsi@X3Up282*!zP;!DS3jJ?c)Z0mNq^}ZkqS)HZx(zOZavKVoEaR*!HeugL9QdFBZlZqsKg#Coj-=14ZNB#&3L@vZ_hxWj&dmI|-y@ZanUjr)H!tx5|z=8RT;P7}IERaX^kgY={e|5HF?ORw8eFHBjRpD;?xg2irQ;_N~g5!0Xps^qVia38+W?>}jkb9HuJnn^#0-NEYs|JKuYLRGR0}KjvWz;LDxD=<#WHoS5{FDhcez*jVdGnc@d<WS-gM+y4*-lWN%7RAq9nANq?>L?z1rT&B18%>Z%T5oe;4J&qg>CBu$*fxp+1DkFeBXN5*I@uA_y*#=ZsNmt=kf1oDn?vM1s(1n6Z^ddyYx3%jq<0nUr0UsLsgQ#lL>}3#t%6XFHT|q`Uh|&FQ2o1dJr2|?v6$xdD!u;7luFDf{3~&eb>C0QOJ9SgZr+bPR~j9PmvBi5PAz!lyYFx?FQCAAs%o2JBuy517NJG%W6}B8_oIse#$j_7`68Xpx|VJ7nkowCsl^CSa<@pBo)|#x`n6}@|)W$#0To`nsCt4iCYzZ2tSmDFbSS7te>RpLCafb!1?JNCP+LFI+pE)MQ(EBWo|6XW6=l-Zfn5D-3It~&c-Zm1PU3NlZ$^hvilefH2r-TpScZ!Zu<(>u+tDGrBtDNmlRQ*vk{ouVpekACHT=TN{{Wn#rW+GrElc<Ai`-qvtXGYvg{=D-DQkZHQvq?uRT{DuW=oc)CAyawjZ3?B}k5yZNv<3epFTthfn>|<oBk<I93*h{8^(Ad?JPk8vV@FKrMJi{f9j%I@IWQ1Wsloz=jqTRBm_(8@zm3Z(e@VDYSvv;Q0);z4HWdhg^2)Of#q*KF@(t6};Ee2Fux}TqV!vxaw*gp56F{(NnNzeQxz|2iI=_`5$>GYY_wng)1@b$Sbz!fHbrDjyI$oE@L<7Hn5|P`<O`Q8ysz+X!b+?X?VHF0_*cK8HF1W&=5QbLvPLTDXP<7mA5$umwp5L?YvaJ`yLavA_gZy=CJj3ecVq@$DuZI6Z*7dL6Cbd-0Hpx54NVVmZ>|j;`jypZDt61m-c~xgaH1@ILT4}9*HIi1+YTzAGfvq8QL#T1%9WOpt+|FJ!LoHI;Sq&VE!1JM0tqnirI9mD;C<tCCFpG_aJ67!OCj?#Eu7FpwgTVc*ZYc^5Y1cE>sJw#%y*$R4|tL=(7$A5%?-45=~QV$%V`p%)LN$+IRvW-NKf8@$MP;`63nSoE(^WKQCa;`DM`Sy%V+XnX)`@<?x@H6YJ2IRIx8um?*zE$DB;`#EzOShQn2a`BAx8tR_JNjaI;B0}=QX8jpXOX0*xE<HlN^W$Ev!?kZqJ_g#u*-=2KLSXz&B7JW&DC&h>@1<tTNewbbM!<>A+?9DVRYhxP&FX8h3Gq_sy5X7rQvsbq7z$({osNpBg?Rq|on3v9jY4;t#%uAGN2a3@|kzQQdu1_;~%CVwp6>L*+rK@GrnBIVwAhAJ)=-%6g_e-;}JWrA=Gr7P?N&_t2szXgy%_W>C!mudP80ZdR;^y}l&&QTvexw|u_FREg{?Ckd38}!-gL$AGwFB>a-i7m3-f-`~JUsD!8Yy@aiN2X!2%g0-Dv5Pi@yMUuEHaCHP_AbM3{2?L1Gg9xyM8Fse25SGe_`^X(~z@}g)1_RplsWLWu2F?ahfMn*DFrL4IL=gM*ydzCl!h2p!t-~4L>-{tm;}p+ci(q_??}wYHc|#ix8%VK6asEtTb!#shRPxo{pnYdURLu4B9B6OB8|yiSR@=PCyteTJoK1xaTKY#4g0L`Y?J|U>}|f4P#wb2vP~uV}jCz$<n1R<ic)O%*xQDHFtl&x_z68O8#9u7+Qzz3ZmqDmO4%RDvi~58^AhCi=J4hN-YFuld<tL6<>NC=rcM8wtqf5#o<tL9-hO`AI8jRT_*R>)(;@O`6y1iEQFh4Be-36LQvMO5Z&8HI5Wv-rZ8BaRvs~+uU6_1!~eQbXnPCd)pN{3e}1wdOPN0PxQvH0myyyhf+R-N56iTFqk+8<)7~LM(=G47$C4wICnyT%RWxCw;&LkgSeuDG=1W(HzGElnp2Hm?FTiVy8ZrLJPd}acj0f}Pu;=1+u-%@A{0skpWKK16yS^Tiv(4#c*J@bFvy7VPYSE=L#n~<ERjIyS0Tk)wLTKe<=x;wo8>gJvw(BReNTv&zDqH&e%>!Ik(gMqLj2XXGY2f?)DU{fZVElU_`r5F}iX-zFau!^|bJxSc@#j(Iy1p~fO0XakA$M8v#saoQC<!p6mK}O)i9R<ap>^6ed{XbqEglPiW^FeRQ8-SoIyhjA%yn375JYLf5X<G~C&B+61%b1Ls9ZObs)gvl>34D@@b)6QG;1AwZZ&~<H~W!4|0`TM{RH=0Mq_U9TAFY95H(9zK<C!yIHnswZ|t<E{%a4x<WXJHD{jP;)B=5MJ%_5SUPr1%e?mI{40?0EDOn!)6$>(&S#wSh`c?<gw>>IYA#efDZg8RdtM`N8v%gSx)SrC#l!-Ex6X@RX3s=9@AgcRZX|O^m%CmgL*~$rn?OuS^rr#jb{TO=pU0~NePK5O>!ff>$QO@<8R<JuJ!4b02BZpl@3ICT!@VIq)YI?K4*}E0b`lmu=^b2O>mKo)La0SCAufUUyl+8(8OH=Qk#Ngmskh}c}XIYP7!|x^Jy!UfB(;tl49APS*j9{pEjycgahNmBj(*CF|sLl9LnUy+F{K}8+nJY&fKfS{0(>Ga-E#>gS)tc_r25icHh&cm2aE|{E<g9PQ_>dS>70Sc4)#fDc*dqwDKg`&T4ueu!INLS%934r0fQezJX(Hz+Jz~^^p#he3yFw(q)-8c!Lzzs{-bd_+urM8E`6x6~SQYXY=k!j!K@}g89ykk*-is*bE9imi8y}$Z&DXdvU<c6%RE2~{7UxtqvR6f?k*?(TaPguJ=^ZKp_5V?Do^d_CZ5XelGTNzBk_IXXS@pZlOA$&cL_%bzq>u=S(jHXO&=T5HTfghPl?I6@Wu~De6xqbT$MgRFeC{{raeR;On=7k?+!uCW4S&ngt51j3td^uIHrL3{zL%(RLY@}>w~`(_vXpDstHpJ0SxSqh%;9n-Sis2kD2QFUkUFgq=l042XNLR1#+uLv=EmHS;@w=lwisAm^hY0)G|+z#f|&)yxJC6ns+_os#o}^QQ793Ezel6orr8`@b`_p)AA()_p<I?x39ikt=QdB6Kz9c3!U)0Rcu@ZZ<S9KznW_4m!~6pN$*v2i7kdQ@w_E~=pu1?Iu0ic7(1JV*>h~!fWi;n=$tT96y<s7AFW2EZt_yPHwhb-Vxdners31OHPw-5y{YE>jdXz{Ir|%{uLRnQ4hRAT>sx}B`Os3MXwfc0wuMoX?BbL;?{0R5U3^>8*FW~;O2naorLXOn`;k|H`f%ZlBAlhO!=Nc`B$IqBjZ*f1)^qL#5sg!buVz*L5-*ogB*5fAgilB6Z5RERf=Gr62@QtG`8(d<@y;;{uTH79=-~DhjQ@R4yx!bru0sSE4Kb1Q_*#nOaEI@U38$K_aKqGIQK;O;hp!b9@mv7a|n(TR~{n`yZ^_SCVkr~|CyxsV5MHwE&xtv&~DrY8l1td}%(b`V?AN!Q(M*GFoylW1(cDpBRe1C)A|7;v}TqeSKYE9*~uUka3Qyzo&*eCc~xQm(3Y{B=2bC`{V7Hui2B4dS$_*S71e(#tKDfgy=OG^#JfjC|ov}Gz=pTW^8#2@+7!6fw}6MG&7dz7`|^|JAJiYcRx(rQQ%7vzdR+R&77Q@F<QgLu=kl{K!MNY95|!}5w2lyP^0@NOd(JF5YkZ&lzH<3iMXj~FXb!|F#?W4tYoQ+RuooLp~<jyiY#Z4)(aj^tq+jB?{#YiCo5tr5TzoW~_vhQN=^J)BFRDu*-gL;u4ISbVbr@_lx3^W%DX|4o|6m33HhmFq*8hnF(C-IwF+3(O$(>UiokC<66VjjJp6!pN08^3ixYEpGjZXT?mpeP<?Co_{C>V!uW~?$CModTKVPWO?CT+cfwR9ES@Qwt}$kb}Dh>E({g#<Oxi?$Lmg2r77l8u*y2wXk>9FM%2Foomujj`ay#kxL1)6o^@<&FqQ1=tA~ljzIa6T1d|y~!TqgGBrf4DOTOWX+D8rWRMIc@pwyJgi>${a{l$2;x)JU7&csPaXJNc_AkRv{kUMx2@Zb?|maXH5BYNq)&Bn^C>O>D&GH*3UN>0JxDo>DhH04HHn?W+$mw)K&RlWxACz&F<7`yBqz>9VLxP3&1Ym`vN$QcQkHZ+WH2WFs0eI+{LMlxEv2EP3kpsGSo_%4$kvj)vQAbdw2bh^j0$68ZaRACh!(EY~*-zDI*%8|J0NwN<g<hZ}<qS!ZuLf(o`nlx>L3TN^BJP3?LLI1j1TvQwiHHXAmUh8pe6yFBjRemgF|8d?%**g9@Wic8oZGr1NLm)Cmg<KF#BYP%$K#O=AT3ud@>(pMtldqC2{A(Ob_nd{FlS*NI_++wLMjcAc;)u<=9*oUBjAk>G=nJJO+%?`qXsb`cKUO}_q2UV!Hqvyk+L-IyI1?0o4Iu7<Av&DbBOT9X(BR*-__@oLUQYhRqy~MlPH86l@5dNPuMHydTn+4XpNt!nlTqVU46poqE~(tyNPKtN!VM`s@?9?!Zk3zC{0b!sAHvx!wYyL=Z5QO$PY3DtKGwY!ZVjg20omKZ<c;_yocZz+S{70`tDgZY*XpxRqu=53jYh_q3UL+=O5j;i2B(aL(d1qMIaGZBtqNMe^r#pRyJ=j?lX|Q+@xg0fSEAy)NTblI0Stv`s4=($<(~f>Cuc5L^!u`x_SvvFFbE!bkCJI>3GldQ+rPcw&T}$T<Bcp;h5Pa8cu`rAeoDB7f9A%4LvuKkj*3Ay|0LcF{7R1Hh{HXfJY11K1XHe!1Bvm6VEIFLGzSknA1Z_m^L%m9eH(NtUx_F8ia<zG5iHRTCc1$h_){_+K1ojF^6r(epo<i$yjM^a<wqc|;ZB-v>2WZl1w4cn^L30G@cRAv+{*P=AkiTaqNhDTVb2s)7}<&P<<p6yatSzEoQM3vIWSe}C5UFu0-KbD08jJT$rXm2_wOfcMpF~6iin0}_wm$trY=;~TtvZ;`OuuIPB)LH<DQ5(@~%G(o1Yqx{@_iJXEi`p)@X8n8-%$VN?CABK8>|01~CsSVGwCd<gGuo8N&=85{L2_G}uv2M3)M2svf%Fu9pXb5q4lUw~s$?RfrwFcN6rLM)3CQTFkUK2r|;8q`A=-=ZRY3@HRo*$OP!tfwgqw#HFw||0FoPb;X<>b#x>fKtlC4q-Aa5tv;njdp7L_I}Jy$_ZcR~U!8)b1?SPf$RBjpKY%csDBO543A2B1p$mULgbQw$@Q&UK{B>q6h@S2un|q|;%<nVowN*J;<#g;H46j3_%6e$DOeLbb4;Wd=-oSB>Vp-<8pK$Xwpx%$i?9O>3lI6FFnO~d@kuQ|E9qmh?V`dZF<Q}t{{cjl)4T7!^1-#a|2<C}AfUB4M;K`3;Fr(@Z=^a`EhMvpt>ANg&NtnRdXk5b?)AiUE-R-b-w+tSU0kk=2M^@A=B+*x8;Zd&+e9;y~febY|;nXXVnh}J$_LpJL^G(>M<_ov{F2KwA-MCM>3~nw|0~dCWtb1(?)pIL|@xBEl>P|egOUDB_Wk_d#sz8xNFQH&W70!|gaM0!((>|dM)1_rlVBsOy8*&T0cwTJH$$Pl6+mOc3J_FZnt65vD6it)A%cIpJP#X}2k@LQ?oWva7hH;&6ZfQIkta9TUF4+b%`Uq+qPRC$lTb$r_1=hQ35~)LV_%|_?WZt=eYs4R8!TbR%jyOR23V)&S(OB%B%12Vy0FDl0tZ&O3>@QO##qL6EUi2uOTCf_dyzSBCS~kqfxr%E7`#@l}9GU*=JZ3sK@=dHoX#AVE;Bz$=)xYJzf8xC;xab6|S=2%*<cm?N+>7k{GsyJ3RLOPi9`fk2BKNs_E~f(HD0kx>sw7Tghj_nWy2^W)ez*x{d=lrpx4O`u7Q5kO?GT%3P{%}F^{C>_5;&5u7}V_zNMX4q3jdhTjeOIhow=R3t7zaK!xZSHjmxOYdu5c~8IEdY8E_^<pFc_412*<{GNUvOyw5K{g(H{ACaw$h(;mU4W`rJVL$2L<73cQa4nFUttW}v{>-=bzvc4OB{4nK-c;CU1o{yj$&)|T#1!o|-h1>3!z;;iV1-9N3=-{-)aJ^BMyK*=b&gX7L;oVg*{#!nt)D6Us`)%O!!&T(Lr@N5xMV*AGU7!P(^tl}hWzezjA&Bp}$iKGs2l>)Jh1=^i#JapIa5Qln7ul+cEx8GBpwAggK1tBb1^1!AYWF{1i^AtL8h3S=gT;y-cz*T)yVc8ZTbKx4Bzc|9a_c1zWTolz-T*XsGD_mF3DZ9lj^mL8JFflcJbb5efmr2EqtT8VsbbV=rr=x74xOD#HCpw#r**+Z?)3pklz58E-_=#la&m>&er9BOaw!H(>OzOZYr%fF2-nLBa7!%HG5yIfgxu65Vy`}-@2D7!+O`W8IF%xuH_BdbIDk_XgK_edIS`S$2pjwrIQH9syA+y?`vyem5!tC+UfXVrjqkwX@lW7KeGh2X-NeJZvdWO;)40#%7@U6BME1X`;|n~QO?$OIq3ggL?$N|lo?KrB9zQXT%l5M&0{J?eT}3dfS+kScm{<VrHNlQmyKr!M0RP^RY1F={8rS$&R5rahL61CCLx*XlsCxR3QI7Z%T)is@!F)RPs~%z-P>oZGsep?&CSiZ}1W0`_mBt!GurrkoU^h>IZW;RLL1X%G;_`Ab6f=QcIq?^^e2Id*hB{JUW6m|5oWqs8xj+}F%3{=)7G8&KI8pp!PBqS2pyf+%mNs?_a_!q-=_!W6j&)f7Iv96exsJ&aEf94)i68R58A}g2p#{B<p<lvbGe%&HjuEdTcNou1Urqww%%o>+eW0hThPOvpmHR|X;b_WhvRc&zv&Sh@*}BEtR`ofYeQg$hy!0||NAo$>AF>-Ji|C?^`9av_GKn57`2{gnLn!-e87J5vOe}a^ByZsWUU3uWr2a(Ga7h`X(IxWq&hld*p!6><uW~SRxd*N4Jc>8!hA_ZSn#OO_BRLZ8VDeLd=4GFUmW(bU5)uvz{}$lPet*^--wcPA45Gu2ShhUmB_#eWXW72mTt~Vlw;q(}+x#Y8=*}A48J>U&rDilyc?eI))M0MK4@kG1MxzS!Is1_&HeseP|N5+0ytA;HEIU4rcgw(uBSNRq^l>E`IyyjZLjeoW?8BZuH=Ol)BCQCN2B-Y<pf7xt|5;6ot5M&FOI&++AIit^eP*aa`(P+e`!Sh2Ki`-8(x=J9Hjd}`e{;eAZy-L?`9Rcm5IX1iY$~!LA6E-Mz@?%~QQ^Pku)lX1cmCK<oZFfVt9Cl`Tpx;4j}d7c`H+v+?XNN6zcdIYQ?TmFWUiq>1M)X<z?T_@wr3B){l^b9w!eZgJCF0)&TzP+qL~<ultQBGMi>&DMn)r7f`#i+vR~5)fB)6wx(#2!?d8|W+12wfX<QWUym%G<j6Ef%<L_gP_a~5)d<m_7Q#tAJNYpwCaA#{c8kEciFZX%m^X)Kf%n1kM7(Fg(`%G%0_X!7*{-S@FAm@6a9s<`SL)$z#Zq4;^)cZ~h|7^lEGI#ng$@=;p=WWr!1iL%zkJoH`T>T$Z<?qKw61!2l$QIp0r*nP_h3FycH*n_UMDXtW4`){Rvrd5-@KJM&z@RGn8~8G*|3YxVc|UUK*dBg-X$mxXY~x2hI)(nrA3{%bG(@di54+yphT$K{;GTER@XY-f5O!LFUgZbzlSK8cZHvzS`_>d2#wLLE^$d7D=OTMqqQ_m5kAf4IYT%<>1>To#f=5R!aYm^qCvx^KZhGblcE+ygRA7YJ&9X3jEDbG|FX!IePbGBjblf9(f*m}2034#V@x93-1V1qhz2}3;3;JME_(gJMOAD@6>fk3j>;mI2Nqj0-iywbW(Zn6+@wTZci@h@j36W{+ciBSfa)n^aGi`2P{yN;P(2Ewwr=qlt23MWt%$@ri3ExvfNm!>8%^Hozr-s^0qq~6}33f(_lKUt(GM!yHv;q5y?m+ZF80b&y#8<yNVEVT$aAlnmE?hhZk1gC!lJ1s+a_ckL&|F6RDjHB4ICgi;6^QaR)-Nen=@=3X6E<bS3THDCYd8o8Y$b5$%3K&t+s$hX)8Q2MXw&KfU#wrG$W}EsKyBe?GQs8p3Kgzp0;Uh)SgRCdPTGdPdyR0!Tp!|f+gMq_D|l%UfX@?>`9^o{;I-AEY|395`sm^q3Hg=CTAtQ1@uQ}!?dN@T4xWm!_9CF(^OSXq^?^ry4>OB*;NqKI;M3BHR20^s#qK`VX_brTHg3jhOG)s`$^wn(nfOUO1>Vb8u+L6D!0!%5iIYg$_N~Nb=R%;DDqvrIAD;VKgjMVk7$|1(EG`>?;Ne`hu*rl8k7i))nqhERng<yld$31v8f|%3&0i)m8J@Y8fbljj{Jy$~_&=KnYsALWa@Q!(5q$<b3KH>g-7z?_LxZ%X4&(TFam?6a7KkWc0dtWU$eH$yWofm+vD`3Hp689bBYdFYKt4<y*AJIBc*EtzNvIQBKuXqSlOC`8Jke$OZ2N|@Fl-ycwCB2k-#sz3R5%RxBVK}#?sBf%yPY>RHJ@kUYR+t*+d;0LJPtT-B#+g7SdGdY`gErpPBt^DEI4`))XT<mFE&*{;m~bJ$vX-wzS=?nuEYX4HzM+D0?1U$P_xNJIN@mo^z58ORegV<LSsFC@eP2jK9b;UWeZ6{{%C)DH@4cpCwX)gEU;C^$DVt@)xZ`si>+a4;VBqhS_l`uHdnsNok$y9F7an+D9|+{25_f4jwlY_!fEG}=zkSg`91cRa0@>Xb=BPXO_nm?8Fds(MF;VdlO|T^b&y4!sbF)WjaAS4*B?{7q03aC{Q7JQ;}0yL1D&z_7!fCS?d(L{VYD8Wg;_(q$#)`^T}Hy>^FUE89rTZCa$SC>@XqgHqwJTIcC=qX{?&S{nrXnS6g}Y<zXBBINAMn<=>q3F2YFSK(&3pz8eAUNL<Dy&!o`&y>{h~jG~Z`Q&niEI2X#}(OY3tur!EyN+O=TYIu5&Y+(^-EN$Mh4fNSqu#Dm7-+|Rl*?AW6V(0W6I6uwAi=YEK>l3A%N&&`f+{Ye>59M}twp58#Q<7MD<e;>};{u?+i9Wt-efH(BW2ajn4<9x8dNdX8-cT!1)=0s}MVhyGH9;0b(Ans~0CW9O1qsXCi(DYv!t{m!toX<+MKPwzJ?>Yvn=g$U}`C}wu<85MY`GzGQu%v}2zms<>K4ZeZF7T`S4kJ4IsEMi<HhdG{-jCY}X8##MpPdiaxK|728OWo}@;LbMZ`&H@X<<#t1kj%~osJyQgxqEO;qin(Jn=!4TU^-5UlJg|1uMwXwa#)pz1Le=n!{_zAN~Q(K4;-*;7wd?zk>T7Gy%pGe~|-+WH>Q#Q!-Aw3jCK)7+L2JE^3QmNuN4hytSUy1qpF-lPB^{$40@NWA<1(EKXx%MR*<iqcDZ2!PxkH7+q0@i$V=~+a;GkDqjE{xNpSTUkE0FHaG{0aLZQM;IY;~SjIO)aeYD^J8ocegATd)O%T*#%W#LW5V%WUgm+&S@a_7ZGC9X>P~@itBbLo<`p#d_8)rZacTS;|PDLQ(aSXqNZQ&i-{~K)9DX^XQ0`Y#Z6}N9-9Jj3PCop|JsOXNvrCPdBv_21_&#Q7-1|n4N@hcDry?~ozW#|Qu<y_ZU3A&)(9e?Z|fN4!W{L6=$z{l$vcxwxCila8TLQINw>~sdPxBubuf_X&Qf}%{=Ps39yw}ZmmZpag=Ah#1FiSE-&XgFsn+@HH2Dyq#8-{`TS)wXb3XAF8*sKT7MS!nM=&|TG!7uI+Ss@tWxfrj;1(V<6b6zs9mQ3HxYUa;L}#<+O4AC^39fQQKdxrbz^_C9seS>T64D+Q^eY%XXNHj%w$znQhj44AQ|2h@yLQuEcLpnb-Tm-#0fWXnC6(u!yJ$IBk;Wq7b9(;sT1c$|Q;AD+H>9Wn40Iq+JZR)5RHtxvV-nm0<csZkOjI~Bb9??cqaHnLCTGr1=J6gVRhIyZJM&v&&c-I`Xz-syaWQFBQeK06b;rbjSgqd@2zi6gC-^x?lnGF;lnRy=;d2Cqr%fdPRGcza!*{*DPiZ-WA&>ve_4R=UCdE%C5o9iMe{g#%q+&2zXA1JBG>f_FeFEB9!FEQuvNKY3xU_?{eH`>r2M{hD!(oD2<@-3BtHgRExVeVljNh4*qJA3m(sL3!y=Tv?sWT6GL*qMkIB-X#mNIvMDl^NRe^xdyKz!(ryl1ZdAu=GHtAq>pXSk!x}Z*i&?ul}va5!Xo$h-{;uSxq^?O&ie+6I2yoKS9j9c_?wA_3Deu2^HHhV3f&6TsljXs{4dfJx<j*IVPq$~Hgl#ntc(RjET!J{fAQ1oE=bt%2bSH~%p7nQMk>7mvFI=m6wbgI`meE&ox%qfJ9rLB?##f#gpAH5q;1JKq=%AGeakEybqs~V$=8^9#X0onY~hIcWmvoEGQXrU2^>P|(Q{8_rLL?R{C3ji#wUu?1)h4K8Ci#GRy0CpMN(yZsR%b%MA7*96_^#{3F}U`vu<S@xP0p?*`2Gz4=}O>>+OU;#rZNS{o7Z5$(9iPTMoB)ufmQ0CG$3C2ZPpRZ~pVW8IaX$kEX`L(A`pjS=v*<EMP7>zDyGwCoG4o`6`^E=>TpM5T(LDN>KE}LDbyP1?Az*IK=ZuAGJ(!H@clQu097Vx}#u$nKFGIy%=h~rIOcEXHzkO54_scepaKP0nOHJSifh4>CjEwPuuP2tr?DUHlKjc##g|-O^gmM9pp9o?7*&Rb4mGmB@#L99$C3+H%o$GL!A}rAoipd@4h3jTO=RChRQ*%RT=KBQsUkhykn2Y+e2}p2aM0WicY@f^o`mDCRh@WW}=fQ_a>M5ujyhpBDTR@iT6ge_pgwgQ6Jct`)?EUm~)+Jy*O#Q3?%(3fRRnt*apvTR=R5fW%Fv_lZqip>@>#r*}Jg8<tuIxx`bh~F2m*U_qfk#0BrN;z>%a-d@w(m=t|YW5yO03qN;+EOP`>Ng&Sn5$<Ti5F8;NnJ-qKJ{zSHD9|+gFFzr$!xK|}XwU-F<)g7BjXk`pWFLVQ`pk(4N6^H$^)N#o)N4Vq3!6AVzSWe}^gnT3t?nlurV-Xl^Spr|DMex!!&M_(L&1`jJ8>lqel7T~C*!QS?SnHMym%9Bxf|rdutusK<BL_P*QrI6U7tBoS2VIR$^sYykx??U~)zwQL-`h%}PYHtMM<v|8U6hx1y`R6Od;w?sAOfvtXTa?`4PZLDpwuc3C!Y(#k<MbQ|DXYiMt!`J+Nap;v4=@Ye}=v>c}{OdGt2KT#Z(<#TKL}wkoJm2i-ai9^IU@;48);--XCVUFd0#J5;yrnCN!-_7VY~MmXxbQ%grX-JgEnShLv%~i>pBXw4h8<C);Ur5r3J-@q^oz!;v#4ct-L#+bZ3QOZRVv;Lrr@{d^DR?NOpdJ{`OqyO*r+#Ux0I5d^6<N#u98Jbe%`1aB69F|rZqglPU79RF|z6;f3LtE-Q2@R~{GLeV8mK!ky`kQOenyAOFYj$lK(C`djMp|W2z!OJrS?W+=a`L_DlYAT3*t<QPXR~SYV*W$Mh5fo@u<A&ur*uuIp=zcJQW)n4Oz-x~G<$D_{-xlKT4!r``wPWnpHc{?q%MJ(~mf)@)lcsVTV!+Gm5zFkK3Tm%bg8Pk)aN=4v`Hy{r>H7ol;e`jVX>v7IjM{;`izfTHxRZobW}#P}G)x-LgPq6ISm~+>*qE6Jp$kL!3LkRd{_Z&PdO#Kq_I_aD4s*!1bR+1A41%wM(df8tHdA*$4(0DF!9zQc&0Q8kQqSpv)v?JmJUSe9E}aGvwmaER-<yzhFM<dcd0}4SEjG3FGF<)a3wE84po#wy-=0{8C-Xen?1*Z9NOCF@_Voij(N$yv|0%n(ZZ5BCVhIdNedDDS#bM=tf6?I7L9%Sk0~m-KfG7WdZ&70yepVR3vEQe0SGPZ2%1wf{mH+ZKq>myNKA7qg!(Kf7gEAf$V8>T&4BH(D!x!Vp$_jUIa9CE^ulf;&HVJXPGrEnW%ro%dB}2OQX%+Fl84vCjnJi&U5Plvgfpq_PREgb(Ti$O0GbY8AiD<xrd|~cZiX!)SVmFFTO9Sz>-%#OSC)i!7!|Kt!nDJj3e2@LteW#Rg=BE!J5&Q%`^$tT`&N$k>w~P3dRg#hYGht)55OkSM!5F?Je6$@$cNQ&!_^k~P+1`$`mJ4wngW-5GD};HOxZ$pi2-g?47(KEC98q2j)h{Q*)x(Ow+m{42T<6lrMI)da9nUYu42XK^g$mAU)GBs79PaJM7lBVu$Z8sGo8t!wJH@$+x31&2Q}Qr0G6ClAR;9XoBbjPo1<^KFhK(k7*@gDWwD_<jdW5aPGopaI&j!PsqbhWyb0*Y183L=*!{p?ACA!OcCY65h6A%9TYrA#@Idv+ja_{dRa`Sl+1`nDslZ|p@?~e=MxWyk9RR%%X<`IlhQ^D~?!pwT2Aw1n8K%$MmlkAM?WN6P}R=x5C`bRjxfy#35O6g*j-(<j}&;?6NOYrcoi8La&ieFb!%RXyO;NEl)C{Zybtv;eqWby^u8+P%fl%!ez{#iIynuz9|gUsA|Epk6DGPBfXTo@L`n^JJcaB$!@3_Thnitn|k?M_)RTf7y^y}#iDw@qYii#7zvti);+L+qaEhquKRf||1i3EQ3sw?E|q5wW6v&i7dKhXs{y64t=%gDG%cEQK_T>EOcRh1A#b1FmjM2K1Mwm!xcPGjA@vGd%_Ok#>CZLK3wL)Truyb<XO_deZXD0vo0e!HtQ^5TO~5RUhv{y2EjzIrA!WlrRFjOewO+^e$%Zy~Col@9;mq79mzYJdNrN;!wfwAEUg+K<twrSa~SY-LmgcYutD`;Hw2wF8gt(o5qoawe}D^BNk#U71^4pDOfqDo=D7#0ZqT%@T~F!Tkd`p1y&#99UhXxx=Rr_n)(JVk%j2w+Jdu;G^oiOdnU5f0V_{!Mxw3DUAc4*{G=@D%ZZL$&_EvF<D(*&o$G?G?rUHi(G077-b0qT7X8`OTb8cwhi5dh!PH(6y)*Z*kXKIZr}B6zUMr3?<{bA-QJ&74c9<w-od&`8?HH8WPGXjb<64F1q+e^vzrHpmMrnW_Cpe>L%v*T+TnY}{5a!JaiTlTH6TEtE8OyqsMw*|9fsyM7xUYPP3yh1&lZ0F7XI)D=*FFWaXZ|QSQxL=#@5VTOJpV^jCY!cLmP)tG0LwqOD_^WTPxf8<#9jro;`dbsoNM_IZ;ebOth~5|Jc!YR*U1?q&&vmz%|AldIdSkf><)wC?xaex3$CT#Bmwyr1hkq!XG<Th*OEX>(hH>zYl!le8?dxSh`Tu@3*HXyg_|c{vgmlq6{qS^^(C`t7;VIayRBqR=~Lc|kHv%rkAdrF9eiLVLzPV3c+Y+<pi5u26X$m#G+0Cxj;dZmFKb6;G+zyEG7n;d84viTIZVFYne5_ZY2aZ&YIr_|!KXHuyVD3ZISF!Qoj38`NIN7HjpC$~k5JJ+jW^%qJ2b15lJ)a8fPGgku2Ei0Umto)YQG9_DHUe;$7BuMD?bU&0XLxSKV``NJBh6??<4s(2%b+uaNe#%bl<afw)UzuMxMI~6&wd$J_*cz-6t|$`XQbn7h%CkAvnSxhN^)IqT(w??OtDp{2Bq8vf~JD%o@S>7c+_R*R3?=*E5zYtITpzj^NFqaQ^l!3)zlPf4FHWjoHV<D~*=b!Tlo=JmXiB=!t=|yhF(kE9VZB!Oz2mps~6IzN}b=A-8(K!6Joh;MQV*)m!rAc@B;`CBmC8DsU9T;9Za~Bu<^oZn`!?@87RRy%WrtkEtYP92Lfq#{%$WpCVT!Qx82^hB&FJjwx!&vh=R;c=G-OX4j%Z#l*yk;Q^1zb9=WF3IE4<X2l6?d?QCYnhv0z<!o}i@+66E_)D77!*P6X3t4jVJ1dTs<9$lk0f%3^VbioX?A^pK#4oE6^_EsJt86nSWGv5eJtN@P+Y-L&;hXTEnjm-AJ_^IiLow{sPVmMw*jM-xSD8%Y26o6`1WLl40$-XK8H-=tL!n`}IIf$n%5_N2hQk7}#3g4oXbGPsvV0EBYi~jJlxVc_$pL$h8b~I4Ve9QrJohzxeBW8bZfQ+L|3CNOS8Eq2omqnQLfN=&?+vUJ&Vz)S1Ng0FBL-|%MZ35+#4u?cs=A8ct9_b0Q))$@&pS<0Ps`HpN1tH+f5YIEqgHum>?Z^`HG`)wL6Q)O8EhIz^+<ElPhz2IM<0k@8pSt|fH}c=EcAsTvtFhSMv8OL@Vy)S`Z=ECZ)s)o;?}@STQyuYpiM58+py;17tqrqh%eQA;MR}>{B-hxlwkp8-zHCE)q{xJ_aE%3{C+qy+mWXu(}9<Yrr_DpJD9xP6TNP0f#k#I#AvS!3@tqZm-e2(?vpC;Yg--9@?|eE?F+`E=Wh}J(R!3!<VU8Q&VY`>Vy0xW0v>}6F@reXqm)8e@_s3-2|vwVT-reFtEZxoN(`oVh;oJRl)y>e8%(S;@vfdbX|cMC^8{__(R(kUw0D3#ZF~Z5O6oXGx`Q`<mFB-2*+^Hc3qwzx=~(q6jWorkLjM$5S|X)S&*;v@$vgM($6xk=_hA{t{nk6Iy;}s9?K4@Amo>aC{tK(DbHUkj1>`t{f!P&VE;GXT-!55>@4cd7VZJY@hJS#8HRB*?t1k}qx}m_r4_KVy1mFLA&5Fgg!Unz^2){ebwB1DDx78b*EyQ^5w+i9K{d=L(+5qdY*JzfhJErxuqM}v++br}S8Tw&^X*;`F!E`~I_GcT+e^89}fAy(d`W?8|ql<03bK$+mC|*;ZiMv({kso`VDz;0QLK!^4zu(gFqWU;m{9_;XSQ}&Hw?LHoxtv*uD^aalQMT%F4RniMLczO}kcIDo^#j9T@#H+IcrM2sxttG9(Ry@}SOB@WB_40@Yb1`x1M$<<NnA#;3>9*IjSn{I;EnnRc*1xAZO$lT6Dw`OZUTd+qJ}uOWdeP6&=sy9SWGr7&fu>t+Qk-lEoG0p|AL{~L<|qyhx-?6($k|~NQGq^;V+0JQ~pC3eL6<GRODgr=LnpwybC-d+ew|WF|%E|?BBgKunXbBWbarfG_IS(oj2Wrp-%!}R<j!RZE?icVs}`UsuCTilSlmTFNFy?&CuDUKvu;_<H&|k9DUfuZ{Dd6qB{)f%PB3)WvVzkE$+{>cS}*L4=rr-i6Ig$Tg+egZZodxnTR4m&Nz9<6xAjE!)lK>*t}inmi{U$nz5h>6K@K@oo#BkPECQRw{~G^<t*ssj{~Wh#W>@)4()IW#Dqt=cts`#)124Q8m}-oryEOpn?@kJPaQtJnZjgs?O2-XFg9O3jczgRhC{bpdBHvEBwTqsm)nwwyFR$#vaO*I9DW`@&yTy+cSabMS3YFPHdQd^=X%sS9>b69TL|ZzeQ@mTIkH^o4smjbW3h83z&7S4Sterv?_D#QZs#i)m{kj#UXG_fAAN?7qP^(;T_2mjrZBBye;5c6BS&Rwv3A`kvHm*Q$n#1rBp=l${LT5WO>{n5v}W)#S_6#!hNZD1(ma4Ag4FAj56o+cgf_iEoZpuLmi`wYH97^F7l?q6-#rqWuZ>gHN}=)95;{L%JoYTr!AbI(^uVd>=)PveFo9Rktp4RQ?c6;`yZn^=3W^2u>p76oq5xUd67<1IX|}N8Hr%~WA$glDIp#YJe!A*VNr$EEn7jdYe;qN}@AtJ*VK9nZ%Xr2{Rz~8eof?<bbRK$NO4CE;_i^x|IxRbz0=Bkm;6K;ha6>Kxo2q=!#z6|EY)%E0s2%j0RuH&v^M{<GD)5}~9GY9IiOA?Z#!oJ1L;Dqs(o9W}{9}Hs({f&qM*%Ljy3YS#rAv49O4GZM`&ny9J;u6Q(MdI{fCvom+9Yd1-*q{@PL*aEbOmfTc*nZ;oU3%(b(#$C8%J~8wCG#ITUf21j`u<m!R7e@dXVto;67jc`MrkRx~>E@i$WoG1s_jbsRfUl4ERyQ_%vG!&#t~n8g^VYYP__Rt{b<Olljm_k}^eEO5|5;$%+BRfn;c}>w&{xIlT0<k?qFg{9K=WUPP-MBwjoO6ZYF<TZj<OEZ;?MNfz+W{>sBI<$?G%&JMSV4IA~(HsLMR%fd2)r*Lb)mz1iBaPe<<;GdP3c*MN~p7)&xooHWbIkt{FKDrccsGr27oUd@ZGz&khU&KXkQR1Q%FTpsy<#exxBB(Zv!{F|JJSyCWZ)&gN%5TPyP!)!OR#F(+r^qQVQEG1I1$(ys#J7n_kb0|&cTPbW$^vI{hvqsnhq1jZ=Bp(B`aT^3>Nde=$NTtre*t-#(FolgdXSx0gd>HWP&$7#Ut`X07!W@Py78f8SxqB}-5&+@dXwnuS(+HDkj{1}Rl$$=gW&(KFZEx0aldmjFs>>H55?(&_RvfgE<cQ^t%JDFRRUto2I2FI>3oq1Psr<+uK`ZJ!_xIK*xqpk%nu-XD4T=FpPOhhX#+Nzq+<Q`r{sFN0Vu1!g<%6tI`I+1<TX0@czF!oxatN|b_r1WItHbd$HH%&2XJ-14t`4w#I0I7plbLRRf{<=*k1_S-#;S)EpkwOUx-^jC`>P${{#G9FFbzU5DYsOv6WmjNSTP@m4Q=u^29M-?qoe0kl~68OXMhf7YHBPzc8p7!K}${?46(^8YkYyAs=}zaCnsYsmDW@%Lbh2+emh&+2CxgrSwvU2wr&CfB`>_!l7-={LgRliHG4+sIF7z-sqen>Q)iVqU9qQ7R>_Qs33j!wi&ym_p+{1SqSgC`j55$Ztl--e(Lf(beS;>nI_Hrh$arwKb!@dzt-e|rz-WDKZ!1xCrg7;YO&zVd}^GQ1Y7mu(M(L5#kQ)UUYQNZ?0pJ{qTfNg!)i|O+DFVeBngXab}`pQ4lH(F1O`@LXMS%SL8$N%2<`~NMW?6zbCq}SGP?*aTo~pD1q;)*{wdrPk@58M!6(F6?iiMw)}=kE$rzC0jLHs_O_&o!ws>8`DVt+Z)&BrlKbUB=@rE&0cOS=NRxP(U>knk0E1%ck&`HJ*EC%DAXMD-865RBCB3$Q(rTodSb}*wOo8Z-TW%hF596ZhRgyM}s=)WG|#2Z71`_c*b{)lj`Vo`YAdk~iz)MNJ8W^^2vg#AhOz`Jx8eqPCEf;+|_;-(q?82b-Y$Ks&s_Xjdp$`ori@}VhJ2#!yd#J(>t2;JGif(?_vt-T5^rIka{l^D43^%c)>#2i}{;^4ZiDEF%IAZ9Fn4oVd~=2G8?aoiJLghK}keU+ix^@YG&G#T!D7m?|S$8ph91-y9Ci2it11s6{oVM-0vq%L?7{usFek!zOIPB)HO#>|7|wcXg`znZ)mNX42n+Zo7IvHJ5mFt%P0cYJY0LoG{~-I@cLuhg(kZ!unZxC?uAQi((B1-9_sbb4pw1yIcJ$KKIGxHe+}%Ub=Gm>G=0Uza#=lX}JS>~7<r+h4fqtA}s1a>>luURWFyfh|ub(;Hj@<Sh1rU8UN1;`t%4-phyPnPG78oCb**l?3~kF35ix4sS=_p=o3_{=B=Lq?aBBhX-lU+&2-lj>~bCQ_g~Y5KzIrk+}KoVL19%0?w58u&CelMxM35_+(g#+~0f?{CL0EB=ZK4+9(deFrE6IIslW373cv4KOlh&TvT_V@CIja{dfs?zITB$mCc0oWWX^Of7E{`g&_|nL;XT$Y@I#8*L6Qf1{M-f5=?+(jac}&`=X)A)-uw5a2ea<H5&@f=fLrEo_rTHpsngPOemlS+rCTT+Q7T0q-jE{gO@^)O9G4!65#BVMsWIsfBV%$71K8{Sa(_)te&;wp0(!CJS0NZ**1)Srv1+ocig%<eLofTzXS_YV&I+g6)frh1W!gqsY^g8e6$`<;}hF(drL0SpE#X1XPfh{6orGROes2)NOG|eA5nPnGTfqZ6Dy}HW1yrOo=Kg-4cQ!rl{&L%IVPYe>x8#KmE_>TC#+OyGNh~Ikb2%j_Ms*n`lR~7anC$%SA`vHiaQF=FdocfwJJ4k-NCHL3mBAq4AU1yp^XlQNe6c`#|}4)JnW1PQ_o>Ru@3z!9funeqIsG%&y7}xN3iRYy<qp_MdX10W_D`o1yFiBg<c$#!Gz$m(42b@>OMx|3~qoN8aWDuw<<8(<u1B>@*-=mCy>G&hHUbJI1D*C8}C{=@$);E(t*l1cw%%JH>UHHd|wiTO3`QeD|?(_TYM5|&R@y$-v%-3kl@M-|CvI^Ob&#ruaYMxVtBG=gki1HRpL@UpKIu@gl1(aI3kqAtQ}H$SxGBMVDT-q5LKf0k8@12SQhkWiqWQ<X`tNaN#=hLq^}l4LWiRq@wuLen@_AHGCMt3tIH(ZwjmfignIa0J?pT1V>(`5?t{LCw|GM5WVlD_=4kp@7;Hqk;Xy?JzPA#fo1_=R7C|Xq)_RvpBVBD471K_n%<I^*1*&Xn?*pW_!qE3p04!5c!=#CY#P)FmyxututVO-y>q=4D)c1~k3p9n?jfuD}qY%&Tze~c|BYt=A5tyW~0@5O4!8ZRT3_ElYw~#_uv^oQ#R;;0&o7L!p??G5qF$&)=HItZ&79gzj8GRNHK*f@D<|k(lV~=~`;#z;4xjq}xUOgve?&tB8wF|s{9l~ptKLaA^K`gV`5H^zwpxPydQzDvR=eJ;}6Iujo`aMCUzMNH;3$t3MYfxpM!RG%?CSTr(z?La0*tEqG3+}6;+}ovGaz-R>n|17#SnL%tK`{y*ja($zr!PU*#u2h$y*}^UGa*{I_8hRSGvHgKF=Qn+5XT%z3{?EeUKxEvk5nEvy+@wjNEd{!x#<8x&+wdd4GI^}Vi{h?(5?OuVTo2oE(M2i_s0n4C4K}-E>7l7jMJqD!&EV4*GI^Eu?g}7&(fMHb!5cyHiXaBVa2-9xb|f;Uun-?w7Z=RxrTmBq%sP1j=aN;t+wDZBbEJ*h(b%DmqcDL9JXJZfX}ZclO?%5hQ~F2u>>c5TJq`|@BO`}Wb-~fen-p@D9jyY#@jT}a6tl0bTG!)2M3spQYENOtA~(QG1%0<8@L-T_`z;5RNEC}qBG@E{(gh0rRPxb>I;@)+YHb4kH^4&TVlg`F{<Ud5)RcW^0z2C;PvwJU?Nob&nF(i^CRJy42dv_zn@KTT7)KkyV$bF|D0~-46e8NDrip#hi#tY_^IR~CKiR_M#q<AQ_~zwo%*Tr@~bd*=J`ifRBTGkh3=7&f47%+rUElsFW|9|PzYKq1}cf;Q6(vah!0P}_Mk<e)PID{O-Lorj*XDH)q6mtJBJYU?clgU2rLrjf&LY9z^4f?E9egiw3vqGq1PZbGziWL=;FeKH*is=2gWH5v#`n8aR2>8nlRZPc6#`*pE8%pL|g!}Pvl|E+52pP#xd~xu8Hk^Jig`e5ZKZ5l(>wkl9Y#GuvFRxi|i(Gb%UzR<?Cf|vep2@?aip~q7E+}+&9{q+eg+;b|$}mS%SXvK@u>(1!wH_N73B7OngQ;mW6yJ;k%|np5*}Kp2)!FS(BmZstx<{_&e*!J&mW2{vq>(SFrpu^YGZR2Vfo6OVkJD=|uApxO6ri;=gwi#qtfTc5)EppWld4@nx*)kuq!T{e!C|-9bJ^kj6EOpxxQcAmm(!%bgd3-1JB6<HO77)FcfJZerkbQGkcvLrIN>IFu(9fx*T<==8lF&g(tIh=054V{$xlJHpUTa6BxDYUOE9YhyR(DRYy&b>VB1JF&X^h8(}_jBUqz(bph`mm`#p^Y7kd*&>foR(2;#nyrTKgO;<=Gjd$Wr8zX`d@B<z%_pCA<tfi;DXqQv7{{fafy1eu=sM<#tIx^M!Ys;7n4(VY>|)?lj0dlOohz$fX$Vcl${<#x!kv45f;`ke2bZ7t;&?5JRan6Gn0DgD-}>}O&p}4&^U*yzg%zKarFi))3JcyQDLI|w%AE>UZX$#$w=M<;y&5>%^Tz1+#5{0+orDE$esHwa3yyP-F{9d%gt<Qit%5-|_rnNp`MD%Cjt|Fss<rTBS1|kgB^@j|Gu(V=m@Fg)kiB-4ud*@>K7?F>&8Nm#aC8nvudjwbKcZOP;lpr!(is>^Q-F>?0jP3d39ysC?CYQvW>!4__1Rx5H8;egqn8>6OwuPyFQ<Ug9S^>x$^_Um*NYw8EXD!?eeu+`wcy<!2By9F0PkPo-exffzd3`+8r^{5<7-f)Y#go?O2Jiy&RF*`8y{|(3n_cA!ozK1I8b|mSq3$cL$Tesu4gA63GTsPbKEiGmn^RG7=UEiTA+Vysmh6O>_2}guoB5eOP_@p_}c|KHjF{T&pLSWsuzj7JKitZ2dfgtgQC9-gbEtsE50U3WdFqF*%TL5USQMBWvJoyM=+<hp6@AsgZFFeG;V5CFu6u_`O_7Tu_w<RNyN)O{N;N98bcdk`uUqI<&r#R8u#(nY98RptuSFt{TBc~i^E*4HE`jkHQmr&51Z}=QNP(@+?Ntb$~&@}t`GYKpE~QHGEtOk<k!QxSteZhxkK3R?+4BOd=|Ii9AxfkM8EHIxf=gM=;dpZBa52(DP_0tNl_M&++&AkpFMCSppJjWG693rn%IHcQvZ@4PIf5Az@H7D(f#lTa(r$Hc`kMt_6#lq+m;5l{+1AZlr))6iEe>#aWnX)WsWNk{($G_ok9GA0{ktQ$pSS1E?PIU&SlnEJH>@0R$M2Mu|XhH@Pofu^CL@qvlQk|sKQy>KI4t{li;7!&!Uy@VOY*As=hrFy50_>?yqHZ(J^%{T&<5)+N#i<;S!wf=WG^o_X<2~kmrimgwYB~GdeIW0<%v}r6IlM+&G^;=0Elr*J?Rn(>e-Kb6mLQJq@sk{lJ$mKA@7h06HfzP`M(B*WQNmjs(8q$E`ia(}=zfYb#8#af&AFnA1iCn)vLf@in03zrZ*@lT7?m3S(olpzY`^-nUo>%%a0YA#yTj?>&xt(liJHIW{Cjmxpg<ezHlQx8tJbE+RXi0Ih$T@tept*p%M_?~-e9g~2$s<Hk<#I<*P3L*!}pTNU8uXy8`s9r!sYlzh&S$MDY@%r4FKp9lWNA99MEn80dSci}zBC{#m-Dcjh=!|s^(S{^iRe}k(H6-?7xk_qO`g5^2G@a~;5WQQ!l&of&qqfX4h^)&?QKAV%bossy}<00t#y~g8ZHn_jvlP@o^kH2dFdbshx6m2)n#LsGyF!kDHXv&%mhuRLZ<BnH(=a1*|vt72bCEE2cZoN0^O&3AIvp$%0ztu=R^AUcxSplCD^f9yOD#~=ug*C!O_$*=>FT-;I94N?Ur?M2dr^8R6aKAd6>f2SJtfkBy$`}Acfh=$x7sJkXXM>ckBtSfm6})d?#Y^(QTW<#F^_(?2d1oO=Uz`SU3oDq~+X|2}yAM}8RJbcWH{sLDF%T=ef@QlKd4-iStY>No&&laBTv!o@4@xFeIm;nf<`RiQLlfC#<u72eWjrS{BMakZ$Wo=05?HufoLd{Y0n7XkqS{9vvd&tLIyDp%8#!yP>V_^=ZdRg;rAkmGDudsuJBS9UaaiUcPLGILapD%2$+}8ekm%Tf6O$vL$W4YiuHFMH>}0tQUOse$zaKumP-R!I_TU?byDX+ihphB^2mN!RV8I#_Zr2QNw%2bFI-ID+6>I;X+|>0j>!c99-YZB`P8k5f>u6N22<0b>p+&nA3yyx|OGy{NibWg1F7+0X3=?4n{pawxS0!t@Z4PpKzQb+1_1OL^9im#EL7iMUalD4?Xq^y-4ykia6^}rA#c5Xm?4Hq<xf>z-{SvyrkK&p10G1Oa$B6}9#<WfCgjd;QD3qIzr<5*`;z$!HJ#hd^G8;kmWG&ld1(;TG3U<`*h5gd=!ALe5?ek~CLAcHe#zLU0lH<*&zlACvdDLcpIqdi5aI$X+#G0C6!LAo1E#D2LQ^n}sPfhsK_5dz$|IL%|9?xI5?*<Y27>?WMDsJAka&|~iiUw>Khcq#Ze}-g&&KG=v<lcwEEzLOHEgB8$-?7#YFHk7#8aC!B($Y+Zp2gaDUg;s?^GZ0i`a1-kw8nstuV5LG1j*}ekcLIGct0lI#0SL`W38XShn+H<qb<-<$JgXpLo)1<>wpshcQ9<@3g{J#Kvj!<Q0b(?-4^czHboYDwOa6y%zS*TN#K$FHke$-XWj>sc^L;9Ku|&(4AyLdZO_B-mrW4*&7Oi<JH46gd<iPO{1MUdpMa~|h9OeX8g#?2qpO@RO0ECQzuypva=i~AWlR7yn<ThpN5nw(Z7e<>ehV6h&Y|J@OrGqnZ1zV&m>!N+#u}R-mNaJz?AucYclagfD0zvUD6+uP<8H)y0gos4X@u`Jt(S~iYGC)8Suoo>5TfsN@J`E?u+GpoaNH>l-YIrt%cBbjizm<p&(e6mRv5zGi>5GB<}-irdlA}5$CD^8L3GM3;>+nwV#5YUm}jI8FZ`Y($}TeG`&~Z{AFfwI)}so1m@ban`wssvF#^v0J@rQAJ)e&0Jm+SFybrB^J61J<z0cC~ykZpQyB|24J?u%=Jc0avy(|Z%yPKYrJ#N1>y~0SmyR%v}y<|ZEy|Y%TykZ?$Jzv(Ey9<7xJ$ucbJ^crJyoA(rJ#eOPJWBlcJ@W6(y#wK6Jv(f9y`UI_y+&4#zJUX<Jzb4czCUPnz3-9@z2-+#J7G^1KK>kTJ_LN=y<&soy`5|-z9y$DJ-R6wK7A1NJ>xdxy9&PUJ9<ZGJ)^0FJsF|ZJf$cdJu?>$yRk<QJ|F&uy}_^@Ju$a9ybV*ryxH8-ywv^iJBL6;JQ9<Xyl$<GJ%>X%ylm$vy#;?MJX2~2zV!DfzEuYXK6dwtJnnGCJQR-+Jw@h+y`+ODKCR|@y??lozJI=jy^bpiJ_V2KJ#pZKJ=#(%KJS5GyWA{0J|<)0J*E)Lyog`Ly>lOQJry5%zNAGaJ=-M+I$OUbz91-#zTam<Jp$PtzDix7Jf-}1y;#Dsy%0!_J*0_By)fulzNS9uJdq9gJ=OP%ycbM)JFoZ0JyqEYy};1ayBQ>UJrBu#J!;FeyA{i<J;%TZz794=y&hY9J#9n2y*34Jz9l^DJdcO)yQ%jrKJau1JxXngyrJ3qJ?tjRJvJ2BI|pc&Jl|%8J)q^Oy_MQDJ)=hVJqPHFy&_cQJY~n(J>0FuJ*E*PJ>PkwJ)U{jz1g<`K6lw!J}l?cJI$3ly~4v;y{82FJ?4Ocy--U71T6pn00;m803iV2Qzq^I|NsC0|Ns9C02BaYZ)|UJUt%#XZg6=401yD+0000000020000000001+>=)`A5Xs0;#!#)El3JWxq;934Zj)xBuA`uymS0p-l$aNvUzCyx5_e0?DNY577iT0EqyqUGMkYF%ItsN4<O8lXCmC#vr>)zg{mt4YGo5oUS8}86%xLC40tK;qIaeOAS}=Lv?uJ}jn{`#@Hb-)e_tc(r-)nG{dCzmE^ERC6X?ueij@rmh-m+`@pV~bOOmu96b@Oc9*U#L`?|yE#d+*IX08mQ<1T6pn00;m803iU32;+DE|NsC0|Ns9C02BaYZ)|UJUw1MtZg6=401yCx0{{R30000+0{{R30001;$p2H*1sun5MsRg#0>K4NuLgK+T=O>Ahb7LvC=3qkB6sJG!|tSA4CIbvjPf*3ixF^R^O!<#$G1GhP7K9;LD<3GAJz_a3VF!4sRU?<EkIfv3xhEG7kd8oeAHwrKiZ$o2{;;Xf}g7{`Ao~-&ExOMWB5WIKd(spg;sSWr%0Qt{?F4@g(d12UUFEaQNMUWA|vGOiWl-u^8RljcR0iHgl!rWz4<%Yk}qRNn+}ESJ-NBClO{f{uxyp-pjwtv?;$6=)h)<+=P0EPIp~?bmrMq!b?>)U+<Ie)>i?2c_O@Vl9#gocAoxQa*!Oldvv4{K)gyXZE(vFqaUGcLZ6rbL9BB%E!07mUiBjVYq`qGa&q@zm{49BYhcG2ZmBf}s*y&Ssl_Y*@-N7Bchs=gqe3joy;r)%Ivq@?F>kK;RyJD%xzJxyG6#cs82?cK-L0QZYI0c7cSezxFNN1MC)loz7G*+HjNTuYGO_qSU?lqJTOyF4JjAd80o6MyPB;I-#k(CZoJg=nY=>f;e>rRLtJ-@27)gkI)AH@dxVT{Scfqy1QEm%WXS}h5_d(3p{uVLm)6N$V{WKs>$fr{JsV6qa{Q)WC$*u#ip3t(FOhFs@*sNT_!4fx6VzzgeQD>OW-q~^#@%r9LhyRD5Br8kitxJ2ALVJV_WD;>8(5>_>dS6f`P!<9+D<@#vT#$9MHoPfHSL3!O-Yw1%X>?#f`)3_l{GGQUDhI}12$?MZ$CQ5E@Om?Gbb(&V|PSa1l9Jb9fhM;%zk--mVLw^26Y<zf}f`;2r9z05-*n?DLxQ+9X0~FoViR;`k+x|~B;8S{_*qlqwLjmmew<4gauSHi*J8fpfNlBMxz-^Br!@1XJJZ+wyo*g9N*ah11`3MHx(Y7xq#vwS9g7rx$ypN+HYYAfgQy%&-BN_1#5ezRd97|zJ^r#gul!dSj`oFPK{0dupu>cDhb5_px7RtZ+gw6+dQ}<;F6{b27%Clpy=@?b=a~SQuS){va$^X(xa(Wi&Pkko+=>CJ&MC}Od5=l0hd+2_shpKlEB5t{vPK}0A)x({AS2|naS$)~|FHlPZ1T6pn00;m803iTm7~4Pp|NsC0|Ns9C02BaYZ)|UJUt%&YZg6=401yD6000000001F000000001+>=)`A5Xs0;#!#)El3JWxq;934Zj)xBuA`uymS0p-l$aNvUzCyx5_e0?DNY577iT0EqyqUG7CM?b3bhIp0IsvUf^EZV6l^Pho7*{l(%!dcE#E#Rv1GgHj(m0uKmY(xO9KQg000080000X05%Fl761SL|NsC0{}2Ea0Ap`#Z*pH@V{~bDWiD=Tc>w?r0H6Q>00000089V?000000GsR=>KhQr$WX>mt)7xvoLr=CrJ!z;W}>d6pq`drR8o|f7oT60k_r-cOUx-w1&SAEBo?Fs`5G2FnmP)#3KRe?28ITE+?as@08mQ<1T6pn00;m803iT#*jaP`|NsC0|Ns9H02BaYZ)|UJUt?u%bY*gLE^csn0RRvHfI$EN00000vM~Sv00000?Rs}iRA1LF(tEE%8G7%1U}in^-cdl1rhrluML`h@Hn3B~f+$uH6npOtJ0OY`RBVWVBBG#z=smwTdB5B*H(zdYbN_r-a*{bSd(P~$_F7NbXEuaP2o9S#jxUd|z$hhk_LLkW7i}YMn!S;^wozJk&YYa&8Hw3BDXIUw?~|N0JC%8V_O#@gsm!^tv$?i|t+k1{_CoFd#}J?KDgY~2n?irP8g7mHL%Pr$!UL+9e>4;`)$Ea`poO<HB5-56GrZ;+V0v&AR;hAvALcO9ACK45xM;d<jNp_&tnILd={!w{@_S&BfDt(Ais;?zh`w@7NE*waaj^|F*QvouNg8F=X1IJ^0o{4xNcmubcP_Hv2nb=}xCzLxwMY95U2NMFg%je|Sl6tM?CMDnGPZ(8k_x<gra)i99t-BPaOX!VE@e8RB9D!2x@pL`?u@Ozx>(SZjJ<WvIQLBlJB%k`$!-p^RQ1sEF%&7!?9s*3!}v@OOwBUK&$Zh4xSoSYRt6ZJ&cbJRCp^hyW7SPXuxnz_q-O%nNi4{9Ct`+*E|yEl;e`D(IJE0y<wXgoElh{Bw;uK`l|ZRq1|Ib3!aZ6V0}h!8*=c}?A96U~GZilDbiuwUix&@)VKiAA9m7&+^qYk0C8pTENChsdLLjr$5*O{Xv7rtS`)Y#l5=|K76YihU!CxU|v|7ib(2s@M5gE+6GzA+yrSV0S51Xf_;rm$$m~H+>uLh>VPFV)&gF~d2l8#?{q!Fj{jVzm|LOxgm{#651z)Hp28WA*&8=!5sQqku+4%_<Q(egzJh}Rd!BvEF(ObixCYD1f=fJUW2jMP|TMz<ck8vNk5To=v<)!=w04jFbrF#7z5%-D%I=JJXT{%RqQ+R3<Y&_P*aSLpPr$xwUonQ~p4Xk%syoSc5p+KyJbvor;Y7T;;#&t^JiIT?vY&uK}@MGA;Xgv!htr1J9!rRT+?R^<_OOuk8$k`wS}96yw-e^9g2c$`Tx!|v&9^g9GXa)&P7@w4C{7mN3$qa?5Rof<DpLO^^Yc?qAP#>&Z%lQ=+DsT=5kR0ftBw-IN5EsZ~yhKQj~QjkAJUbiw3VfB&T&pJtN;nSec_ng+QKSch_d>We?NOVgTeX^T~`;NycZ~8$>b{!A?s6Lu-r;DyWh{1*D+R$)Q!GbrzSg&h{!T=3a2!tVl$>kNh0Sc@NgGS^ovU<LUmY<ErAI?g8dohpxY@3R<ndj+~@OrwIl!@<(FX)KgP8yt)j)`lYlhME~>R*$FneVUAfYe4x?GH!u*cvJcDWo@%VfeCWH+j45r1*w#xaD`#roPwIVH$>TMP<aym4<m=5R_)<VZsMB*be(*y%RsK@8d(z5Rj5uLuEM!Xq4rF-@Aj!upo?%H^xC#v4WzTrjc~TRJ4pgL)_eDbYN!^x-=VTZo_<<@Mb)|dsb3+_f)zd?}q@d<urHJEGlgZz}!hEY3aU0RI?)#CKvwF)F*=!Ztahg&5EESk{CK02=0AdgsfJ@X09(Lw=1JtOb$nzJn`Q812tU!K#9gKP~W$jWMxVyMcf-J0v6C{@D#e17LL6E8)<^`EP6gU3bVO~=-SUBI-whij1>*k%iBON?fmgr`XWWK8b~QM0KXl(XydWxWZD~up&jDr)#FF^IzOE5*T8iLc_?)RAz+U&jLvG{?Rzg2hU?)(f*LHV15jU~37MS=h~;>I6FN-&B45a)j0cYecS-W}RqFoZ4UGdW)M{{^o*M>XTU7_S<zA;}vZ3(WK1dBMkLmKc0DLMRqm!jQ)OyGl^>w0<oi;|`_#-P!4SO=BAh$RW?RF*@+`+=tT>*GB)eNgxTJUM}!FgqKWM#AQ^M@}^?l6YcP7N4WgkkMbW9XTw<4b-p9zRpX0~u*#?FvP(ydu`z7RLNp;o!fffQ(u}EExzw$u(L0sS|+4k5D9^Qb%W~Bvz(|VDB1zTntx2)U^;~MVcdgy#`hchM;q`1O5u=Ls~x&ZL(IFvWtxaH+h)fXM{=3YDga?IOiKd-(3|A4*s~EYmFz~y0|<w0<qbS2zq1$$Jz)~C)=UQ)euAep=erXh0TsS;O`2?z6^6nkOoTkg+PAT5})!lQN+y0|A!4`ykkRna{%^#u*bH?de}VK3z}>znEGpD&Tk-GULREpl%Tj#k-ReIkXp!gIy!4BUFuv!r%MXSL)wt`7)8^k8f#jgE=~EqvQ&7<iMmzo$ZMA#8IEI7dF~*O|3D0t`bp8%&DVHA;+;J6>|vhA`yk>7#nYC3vGlXAglhIU(yg#NJiI%{`_!OHf=7+%k@;FGHm{)aYX_+LX(8R-&Z3Kv+N5J?OsPhB)F8Wt&c*DdtnIrf#dtS`^e2$l*+6O>@+PcbN8`UlQFmT0_09>Pjd7Wjt1*|xIY!bp=fxx%=SdG|5H;=p${X>Cr0uOflx4DsBra!>#)fEGE)BXnRf&!!Ini2YO=N9jQ)I~wZiUM|7ry@wVR7~-=XHx1*G3?i98S1{U1^4Gci0HcamJtRhKP;V#M4LiXcafdVozOkX!D?w%)=0ugT504Su=Mi|Kg#&-5X_vZn!J$ieEE=5NGTGgDhuMe(^!N5(naM&5%3hg2(Y@Xo^!q{W%AC&CrKkyF8M9+v7wU3u60ZP{^{w3|(d1dLe?&RyW*?um;}?15A!{#d8B&?9DJiyAKDJIRwM29QdDcLE61wc-IHw*IyTe9*MxX>Tr~f@(^7Y3c;vg$V>xb>%(v}EdcA=fb-TNFst>%;7WI-fAmJJsWV11cvu?e0KxCZurzgn`FK-|e$v4oUwb@GW`k9)3>Rl-oV{%al{FSPo#Kq7@7#aoVZkR$Y_#_S>!crixaQa|Hy-Z8<8gMaAv7LEU_>?=+KZW<m=%hex=7TQ86u=T0*=WMI3=KsY(?g~o4p{uz!Ec+{Bh}+2h;<(2%qi=H#t|dg!1q~+ZIC?%uw>l4nIfLF=2@cs%DyEV7m?C-rK<Ai!Qvwxp?30fteZNXks8oK8@j~c3~*0c>$skxLhTKl0#moI1r9Ebs`vb^#kW=EaaaG;mK@Yv}#3R<kL8u9CgC6G;hea$w6zo3oa#j;ZKo18q<03`Rs<Sr%sSt?uY@7BS_B~rUIInVW5WBJG8*|F-NDX9X37S$IxdBOvv`ehli)>@_sg2xA}m-xRLJOF~Q@>f!O@1jXtcfgoI`!9MaoK*vT2Wcajhq^@QFJxMJ#^I9Qq9rSf`xh@A6-h)gd@9yGvy2A`K76u{&@W1Q`AL2JDp79FxeNg@X!4ldy1*M+2kHn_zK7$<E4?Fo+Pw(OwJW?iJm_+fp*O0w&bgzQ&uyt}iW5^`msQ|W_x(>i)Ss0mKPL})3WqW($?n6)Is#-yIEtT#rYbu7wvY^Cx?O5jY6gjL^JYVB3Ru^Mk=n|IKrZUr36wa42Oc}%F)#r!}AtVrRY+|meI%=&XGWl`jzgIx-au-$iur01xEJsg0C+o#fuy+Yvb^T$&8ObS~sgXotN;G$bho3?3SV#5?zK3PSw!CH{vB_OLUm;9pSkmetPqUL<+IU<AI8nIa5QcvgXlwl+v0bA=<^4Ti^Umr*82#|)1js`+z*kk@h4i=}Hq9D`+vQnC8J<sSEWk;kNbx_f7Y1~}vgVvwvboa+cnw#p4i~>ihdi#yCii2S~=0k$_#K1Q{8aLR!<SRTx6CZ{m$<vR@PV~}wU2mLE&8O62DYPU+!}aKKIylDY5V<JaI@LkHBE-QR<zV_p7NT0TQMu0+i>~spSlJ3*?mCdaXbq<rO9<_7Lj5dR26yyv-q8yYhfWgb>tAxW_d>hmBFd=xMBNL#;rJq(dfNZeu6Lm*&B&%dZ$3~-j~i<83dv#fE2`YhLqp46axxHy<&j{Nsl6aWQx<HCg5mL15Xlb|;IrNZ1J5mS`K>W(2F&qw7N8VpjutN!%s6ca%rn7J6LYKyGQ$T=D>UtN0eiL#azAO|P`)R)6|ZUeTQQV#-OzaF4(Tirg+qxCZe6%aT5tJ~JqVcU-6oBJU!?xR1zkA<B)&)%?_E6L*(Qrx4K|G406R^L(0Nu5?#Jw*@5{sBLNiox4M4UY@cw3q(GDe?>~%#@wJFXQ8{%Z3JvPZ3p!~5RhE2^;IiiEDiI!NhKm(>r)sR2O9<R>GW7kJTs5d&HAWIH&zbatj7bkputAdR)R52jujKO(^2=CK__)Z4fX4t~9i_u0wwwRniXlXKs$B;3^l-%HJZ-R=IjJFHt;nh8DDC)}N!BjW6y)uOV5fuoBGW|NFi>x4J96s-es4iUutYV?j)&XuW4Pd!b7qyB`sBJWbE8{!mv^eP0u|)O>Ym9SoMV_}61g}_P^Ica+>@marF9z6miwCQz#_(#@#0wq5g;)CMHc-K(`5erJ77F&u;@%AhbS_rK=@v1RE|wvqKUpN!HHX6TmyyiUD0&l^N_ji=Xy*iwrIaA$EH$NKAww#wwxcuAD)c+tfZVxC^s70N)(qQ_mx~1n{1l_w4dRqtXGI+=ShTU#fj+91lBs<fef>ItD$Q-EeXc1TIh{m%tsUupPZ+&fb%c}@_s|=oU34Tlg>>&Op{>p3G^m+RBZ|57)aDEYB$iOl$DNd6P)(bI*U{G}E9r4q0cmY0CgIiD#4Z7CpI%0nFYX|^S4T%sO;vX5$#IzxNfz1B`jQ0N6|$1DzMP;JXHU@%lR8@Ll1hJWS`(Wol?g#*T<P3HyfwjmJdssxu5X3Exc*PP|4n9_>x~ySw7<8K@{VRxzK{U@`?Is`Dwka*>HqcJ5feezkypj8P68H?C{V{%FDab<ZjO_t8o07V3iA%yW6g0BcuJ}wImr#1I-L<4sgK(x-cZi)gfYJrY_56Y6m#t6*<xL`E1b@|qRhzxnLC}(IOc#ill5`vs|(r;ZE^V{8+%I$-+ImPWkd@W2V5b2R}VcFYPj&m5ep}&W8)NATwY;?gsplINS1}eF*|t7v%-@7@_2E>4t7sCIGN4D^db&~SNfuNstLFjE?7J#3>jx^F@M+|H%CG-Bh3*ydDbZU?2pRt)_65+is_$um^E1s2d7$Ng^nXmoM$76>kQEb8z@fDMujw?y3!P}U_-9W1%`}Xs?1k~s+lF~%`7n1ERGw7`mkuV$Au9gBtQcV=3G3xA&fjj4d9j!rmt6m#%g7V*Z3mts21!+<so{-2SRLpINemi^i_fA<}<|873y%!@xw-0UG$mgqF^hbuSFB(Ax5}()ee=SYS>%rg#9j-c(Fkf`wsF@U}1n_55|vL8z7a@FCvG9A@N8OI!s?Xg$_~s6n^AcJHy_qhcZqKlj}l9q#gQAIZ>bJ?@mW79~K7d=|_5|L|C~+5<Z%Yr+w{(^nG&Byg5v@BVK3@mc@!`f=Fp}!{HbiJoQz^+9n6Y*2=<7&;r#=F4Lx}LG>vQlaE_Mjp5|wYo#E}cmnxZe`($DYg+Ni6hRB$(Mk7adKGMr=ZO!9mffP{VH3nky{DLxhZK`&1N+s(#GUh$;sQBH85@UHb03o23>R$4<;TxGC#g)%4)Z5`p{+Jo=r_w6Pp1r#<lVn?e7iYLycUM&0S2#Rtnu!NG9<LD;WNh!PL^z3Gy6p&KTWZ2{x5p<<Ol`X>!COGA+0aoO#<rLs8YB>t6ERe=MXdOmwHY!6mQWUWd}&!@1}&mt@KC82Q&DGsCncPT_|>g|DI3uw7-OAYZ+iUr-lAD6;btP6<oaEOyAbLqM$$ec)02hWh!YPV~Z7bPFKa1PF;jn>Y=bk9Ra&O(e3@l;KcFa{;n<LtfUL$+0Q6(W-x79B8$T(kCS)O5<2|F7{|x1(TByQl-_8CrK~%2-)95OsI|nqdADf$_eJ#mrVf<%pC$D>T>7*?2KNuu)0k}(Z8H*sPS;twm-3YIYc(Lm8YQ<THguO-!u351CL6TzO_dEU!;|7RKd2#29|5Zcpz!P<b&uCayiyO@_7WBLiDA=%Q#3A`h|i6MKWRs4fqN8LIvT^f<Roo-7eFzL2D_SYgz{{R=<W(hoE)p63X-Gi--f97_8yXqoJSiK1h6~jHbu|*K|7<gpsykf8A)^agjk_vmnP=?Fv5yO%={u)D9e^a-f103XNjRGv7M}5>SM%ilq%0wQAwL7dfs)Cdq^e~E>*&|@wb_pilK^$ia7e~GBpK6(~dYLNS9wG&%-hFSzih>%&ySEmrJPkxhM+wT1Z#w4n0|{jO?;uvTakvh>kH1S<B<bdM6AOSVGHK1IsSj<ByasPDyEC)qP{Ee`NykVI_D*i^J=_E{4iQvB0W}x;TbNs~?BGyRXuNhx!oT_mhs!J3@UYbujvDke(MDru7n<cvLw^eb+7!<*-m8_LCfX-_V3&HV%ylqF!DOqq#;HIw^}sVb-{H&>XkxRq-d)1C`E}sAcBENJ=bd(8p>~btLoy?ITt=%HWkn5Ho(N1xoDIAzrP6?gT41^s6BJivms`w8Xg(MM#@SVT!UP#wIA>jGzn-SlOV%M;SLwR3PGNi$^kQ&^Ts*)#kR?psImUwgU=$Y>?Kc1*;Z!tXgc1f=C@CNO?lX(G(WajCP*NgVRqP+!|BH)Af#+b6f+qJESq(<P4VtJ(#A;qh}fiaowgc5>vryMzb6D+TzeT4R|+jz|r8KW3>*(xpQF4ylz+1MVg`umL74y$>nTJ@8{wEH5>FYzBEw<_}y%RhwoL<`GSkLCv}mqP7x~8ol(ztWz#@eT<o(!;$lS<3W?wq*^sle9jUhlQbfcOavmzAtA0vkm?uGPm4c)eVNW-G>}bcGcyhQMOPinA(>^<8n(<eJ@{Y-nWZ^pEdl^cV5q>md%@jI&-IyL7Go$t{1NteNN+Z}!Uaf0sd;DtRmgZ2v^F?G?n@2}Y@~K9@o<`GmQ$*fzQc>JQ`@8e$>4nwQ9<Ye!oLEU+f9gneu!5S`AE2jmGH8W#7EK&*quZHN=(BGE#gERV6Z<Do-<u_LpgEqD)jet6QlfGLe~RxAp*>wcd8b^bQPYJu(%rk9j>%Whre#ZLo{$5bDO0ECqgEs&)5INTQoxH6tKd4!3*(Jku;z9|h`I`_aCH6Ad&$*KMTiuxHj$6ZY7)+KbGf--v&;87e%B2BtN(iaZ#^L8%;yrh|JA?F-|RcXxm9M$wQyhNs;lgS&`|@}WlO<5G63H<T0{1k8loIRu;-gIHs05S7k3h}o_JvQE*+>mkA;G_Cmcf!VG=hHBg=ddoUV<>{S(m0@L$Cg1DufvhnlTFG}ah_dpi(Kf}ZF*Z-H-#L6{oigfs0rcq<o%_+%S6N3sxF6^0itEl@#f_`V<v6|RmbQfFgAX9RMxfz-VW-cFu~y3AnA=rO=l{&*bB4o9W8Hat|5vH5olrZ8SQU}6%&Eu%3}la0jA$%xOHh}ii$D4muJ^Q}=>eM1i!lO{oWZX}AbjS=QG9<xIIP&dyMR$>uIw{$~YtO3-{Mq<d1&`@9j=_!6-AGU>npdtp3L}1efPiW?{(7ZPu=iDdYaT*I6XQyCJ^(5GvYhbCsR8$Kj!Q!(P=BlOO&D1z7<Z9vZt*Pk#m4tRL9fWzO;&5g>Oy@Jbk(7j23u7QDrUuvaXv|OuLGMBx1WScNXC4nH#I^BgPAG(P-I4Uj8f^=F@vF`bhi*yZf`2d?j<}(=P88!hqhVSQhP#b|i2RX^OtW}&?h}JaOd8g<Ct@Z?0@b`}NODewZk!mj_oQK_MIv4Vi6Nyo9)X{taMXYw1?3Z=urm_r0Rm{gF#+~UA<*rUMA5cL%)9S}10!rmCHr9BJvWpw8u7GG0A7#d-~!`U=c-0Rj2nO#ulcaSF&sVX{gMCRCOvMNgz*X!FzM5MYQ8oZQ{7{+@ZM{(-I|7r8x!zwLl+6XNWtOsILI$~NhtyeC_XhFyXEgw|EvixC<{jXnP%d*3rEaqKlu9h&`4DjW;ObQ&8LQ+18$hqM|c%(iPx&W_{(udv8@!wM|j|vkSqR7>ms*G!j+fyP`0n4x}9O@9`nLXn?1D5APISEBXLjb1WAOa!6PyjXZkPD6`G9fXXBAIbcXu%A|Uz63%dvR(ODU9#I?9!#qzx*TkQ_TAQxO$yh|3|K{)u<4PVW~k-iHMVAgAhsreiMUrbr%g3>ZM*rYpQzJWD<|9VcbpRFMkV2qg&YiM;X4~NC9u%>ekl~0dC)mI-BtgE2TrSYin3dOgoS`w^Dgz@G`sCP8b>UVyO7iT<e{aR{lw1;@034Wy)k!YJe>Npl)3tXTJi`~&Z(Gg=;_%TVx6YrzFptj!vTigQipo)i?8yS7u?SR0m7I<{xGY$AzqG-Ay@{a8!gC0}H1G0gMN%Y~p7qpuku~Z_PCT(y>=Wcu0+RY}Vy-eTRy25i_5zT3EL0G#5YC`hqV!I7ymKwqE*hac@%LyCvtnf*qjh23QLz}fDJPX92^vD}ZuK;NqW2nyX#5=Yna(b0<>H-&k@7v&sv=n5l46!&+6R`p<bSF^{M<1%;g~D#?W4yL|feD5sHV`}B6=ELN*gsZIVq1BLI%tbhzU^c(#Rli(jL_j<O9wWX!*Px-E=gS{?mb2i9M#7q#a>!@74VU_M^L6b94>pHNyHIB5xUrz;D+O}hWH*N54&0qoV;v>R2v2OPI1A@0~Q!c6F}iPXQU`t!mF~MY%@9Nsj$Mz)K|pEc%<(at#HrzF~uElL(yvoNHo2qU5x%{=rzW}*jIFVhcjMlS)gOpcd}zVzU*ZaoRbkk*?xBnJhQ=kGi7`h_rOG^*L@jWeU{{f7aC?LH&Vyb?Z945Qyf370<mwdF#KhT;Tl<#{*&V;Y@8D3L-kV!>}T``m;0Ndb?q_L!3b|x50U6%SN!a<MAwEtbiC3QM(z4|rofMjO0KvZV~)g!(irgNVeE(*nir^`ZJs9<23bOM85_UV+@WJ;hAvTU@H4#Myg>=KZU|vwq7^o2D`MMY0j57Kz;#r{6CVMn3tOT2E(<4`1>rQw4qhI*@N5vp_iOeb0RtSlE)Jcoj);D51icaoEL3p9u(TmY9!uk-IR_iH_3>tfEVT9PP#4OAbEg=}Kifh{Ss5?=g^@km4oZ6%?@}y+We)Dt%5P3Do@vq1(s&Zh@u77&TGS~ZMJ*z_MEqks$#*Y!E<>_(U&E1p7fR5$*+MkT5~c;)M|e}?3~1UdEfUF+Czjb;-t(UKyx$iU=t!>=@$np~ahm`|9Q3F6dx%OWap*Nmh30U_)87bl5-IVa@<tE3@hXqB{Ub<2C4_eNgixu@G|G4pMGHU1khzmHT^XH8_k2od&7T6|gifWPDVdZm6G?i$hGdwjLz~y7Qu+H(s*QA{NpIQoJKT$g15N4iazUEEJCI0lIZe&nL?an%$vbQXxt6BV`Iq_R;bTJ@!V%n?>=xdSNG+bERU|6R{ki}9o<Wt9p-bMSi~ns;fopuor6Nn_|N1O~H^g!+D)Pa(t0u^7)j~r<5PCmbptsNzxgMeTdd&(C9Uai-Fdnt}j*y+>ffnOv=$dd)@zV!6tx?Embwt1mZzxDaLMgxjXM=&wH~sN&I^z{G9kA5_c=E~+#n%mSsg$WdAwzVZ*T!xI7d-6H!(ttEtg<x5n<G+KxoR9<DKPlHRUgrVX0U7ZN5Tdz<m`9A&Wuo8x~K`>UJtyU6$#8?Lm@8+ALXO4^qmgob%Z0ZV>~whV#B2>93MQw(Q;cGh7rMtI~|ON8TxSD?FZ8mZ=^6aI#b#imgR2HC^mpC#{~1=yWo|I9ugQ_j4(9A(o=FM>>eZe4qu#9Q^lbmM|@+2Agx&e!5`hRurnCn)<_{bG7zG>0`TXCIHJyk;N{E!q?n6gusamzLjCb~gAfAU7%lYO8+U&SpuZ~^3Lf5YuN6dIW+1fQc;o&<dDz_b#3ChMykDY+)g?|C|Czvft_xQfVfkk}?0TYxx1A!$X>-S&igCE#>I~(V{<xsWkJ=6&R3v%hvGW&lni_zrsa)*Y{*u7;f`=EQ!E4{qj{6>1tmcZ_*WS{f5f2RgaKc#fGm_uWXuVbrsy08R-H({umvf=}@jG3L@WL8>A3#hQx2EurSmFVdQ*3zoS;Oj&3x=<2BW|@4p15$p+xL>b_B$fH#{>N1-cVnkCn9>yF!|?EvL1E96h2e*_noIeRR^qzwL<lsCdxnHh&*Q9qIE8jRK6ANyXs=0+HrDuZi{nQj8XaHAW7G|;Oj(dm`=J+0X~G(BsYAC6i3N07k4&uQ5vI!l3!NPI%fx=>FTf_Rl$)!2WWq}M~aH}xOtPH@V12vzX9T0Hl|K4r{aEdR12!(;<~M*XJUdB2Nqfc_EDCH5rp?D;qb!!bc}6?@U?8@D;%UST`Np1)4;*HEfo628t!j&A-n%Lt?IYOrSq=1pDuu&NEggM#PEccJXCru5q{bN$5?WhC8mIP8rFDw;5N1US;08Q73(V+sdkSmdY7ofs<eotyp8dwS^*`o#dJ(V4?ag_uqkmZJy+Dm;5Pvb>K0M&APZT$B;g^sidHgyE#aOVvN%N){lgetjE?@KafBB2Fk0=T1D5stB?l3EtTS*zoVgs@51L}|s|_q|<?;K5JaRiNAjEk=yf>yO8M4Eh1vkm`haGw^>0#;8aylJrj&?CANVlia`t@2!U}{;|Ig?%nYarkaKiVqOsC+96G)oYCc4;(4Pz#HDMIl?agjSa6AV*RQS3X~%fe};e_vK*k6EQsXu)*L(7YNT%frp<lJm$I}sfmT14-_%^w*!{05W<l&#t_VAylg=qNqAXdc%u>SR-GYb4RcK9*MO<<7V7CY#J~kL{AetrOQi;w?Is0z@zoT-Xz@NxF*N@!p{*iX_?e&ykNsE3W047R-7WEU;Saj=*aEV|!L&#6sFpOt`a~{1F4IJ7jV{LP5QLffnAs)=HzK%ZvoLI>hXqQ&`Di($?l6VCw;N33MUnr~4kKI5v1RPtzviZX+8A$pp3}!ZGem#4fV{#xs`ar(<8?FS?(3y(H5QmZ*9yXq_@Mj444aZ1@MO0PB<34~_lO7H3r)Cm=%d};4URbmaG9-zY9TJxT-QRfwG;&J`k>6(6dp|7`RsSYqo?eD_052m2evWy8f|WZtX3b~-J*#-xwbfa)*X_yDzFu@1$QT*T0|LZ8m&-z0Wi3thNdh_=$>}NND>=^%o_a|^uP)|1DsoB2%maiNavZO&Rq}kFWr%7Z-Ta3btK$!!0kI~5X=<AxfL#0ou~o-KnbuIuiwL9x!N%WeEkB%o9mz~o`q~NPl!L$hv#b@yvp^(WnB}bO*2O1G9Uc>VT9ENMv#^8#QI@<v<hk?^R64}U+bZ|QVju&E-CakM3%D>K7DjUYq}m*4$2~j=Yr@pT5!sgMtHv!TsU&LSvp34%B@L%9q2^vG8&yTm(B$j((HR2a%vNxfQfE&>5&sDO!cJ*t7K}q;Xz%?GikMDDtR4KBZ-0}B0m#yn>UT<ybqmQrAqC@A^9iMDdGr+jMU1=vNMA2d|5_ys_`^&XdKCWw<M`IaWs+;MZGdt=$%$7<y4eYTlW%DF7~CnmlLR{dI{Cc7*B>eXXviub`r>4N1yBG)Bc00v`}RY)mInONI@jIJz7nt^b$$s=n868iKXqb>10$^NCC*D!p%x_Ak&Z{HgM^R(tH{U&L^!Gl~f*8M5EUdN#eOGH4A!E^VMqZ6qmg`1xqzv|1>Yx>wo@m#?RCGZ`|L~v-N*`=J4;hc95US{_3FV^$FfT=OK3+ToSxjx*nEyM8pSqOc|Ad?JPTNx+smss#54Rwu9m~246ztVO`;bWJMN2HmRa{I~Ozg+1T}w1y6Z*n3?EcqX7%6?cBll{Gb0YsN=&QVCh6{_<L)j#hQy`XEvrW{H7eu!CVmybi7i=xe<GmWwP*;PaZATOo0&@$fS=_ce)SW1hVncPzSA|f#{RghQb~r`169H%G1KP#pal39Eu}{v@j#x1ZQ{=5E9gfe3cb$>P6$3h7lxQ*~8jv0!AO{f$xzmKCqcFGxe}!o;7Z73_`!1A%5<&z?5n~6o1vn!tI7Ayg*30qYsB(HadnZk;m}jhFPKz7@mN_k9s&?Vu@#;6HwyMhUq$IyyPZf;DIV$sk);2*c7;~*1!QV9&{(A;i<Md9xvu$lUf>b|JfgTNKm_yit;l$D17CP4fT`J9jyn^)r6Sr7`(Zl2X?b93Nj+ly4MUf;+EhWbVtcSL#VTKG3%EFg6Alqn!)PkO9?PJ&v4xy2Sn?p!)hW6nJ(_A_Q`^nq9R<v{PAaR27LI00b38;G0eadFFvF@0|6}=h>{kBl%EHV$Y!A|NF36cK4`y}36p#UOwjg)-MAzi>(D^U2PfntPlQ;mF;;xC!iqc}sIM@_zS}xzX1HJJmLfiW9-~6W|A)WV#$$VDl=h`T%tjuk>)fC#pMklIcYVv~mbr)15x1q6%3R#AG$#X-Cv=e<pBwJIPRGlJceE;+aHJpu>)VFtfQToY1u`&)(E%|&KKL+{0M}wQ%sl9X1Dm2y9ia^oV-tw3@<#4iJ!F=uVd@=od`nh>b?bNP=br@LcQyF0vco;2DKMSE2lYvg@aRs#mW_{TNvI=Mil!oW>?p-=v&6yN6gV#2K}R)=@w71o8+G?l-5panm8W7+>{SZ%w8P(+6olCJ(9t?)cy5bEF<TIWdrWX`C=_FizD<bL1z(K^u2yO@`cMhyo){rLOa^z{e$(kCF<^U2V(5SgQbpo1v%QPP+DuXCmk2(;{nQg_1dprnIB&9olw397ayA||W|`EtL>bd%;<4s?HcfO^fv{^lZ2MMHO`;~=HOHe+?Gl|fFu_XUNQhiv)*;4-$xjHL{Z_)*F>N?y`QU!ME;a`-c<pJ5*qd^Avy~4sU1Ra(whWs0m?H0c3`~Qc(4IIQXg5woU|<#fd8!2pj>1(lU%DYLg}Eu?QTfc29Gpa8sx$##s?AAlL=?X+N8ztP3hBI%gVCp0#B;7t0K@4Kzd{gw^DF6|(8hGfV6+q|!jtijX&=2%ovV*fVGYPxSwqH#g#x>Am{}i*wclkhYn&l^kH(;OQ5S8UqlX!bV{k41G~K(S16$AWSo2^R%{;4s7>@{)kHyekHA%#kM&Q8rG+I<FgLJDXNZVGCnx-axRz~B@vj=1`+W@s^BVe<g4+8oIIPDyazX6)iY&AgsZ#SfB=;E2b5?s3Nae1>AG)Ko_Zd(ZQcgo?&VKxdo82@uz2zrdS__28c29jUW4m)j_9SDVM!f9&Xtc1viA*eDwNIy;~;&p8>ZdD$k=V6MFPzl2kmFv{IR0R*sV{lt_l;Y)#&~_yZllA1_QeXhP>Au)rz{2$Nnkc=;!_N{8qzlTSeuooEk87aoqbTwYg<)}zK3<g?;%(x1tZHS!W~%{&6vm@tgB;$48=z=o2qxa*$Cn~?nAQbh<rIFLI>&<dwGh+}@Izs;28;_MQMOSEX6E`RlNk@!WERxE8X?vo2%0}MF-2VmM`gXx)1!j}57kk(orh+oen!qHBd*&HX6IGFVToa4rzd_s)WN$dH5AVF#J))^)HE?QSL=nRT58CzR)O3*cX+O5@^MiSf0Dc~KU@|4j%paV=mp^pHI%GT$6t;Y{7YHTO;<<gs2jxIsUfma1rEW4jA!boVJU-u3ZvWdbWn3&6~TvH;P^=g2FyJ2^lVTQql~A|#h}%sO6j(Gl;&kiyKRhU=5kx=Jts|he=TXsv_w+K`_4;tKf_zTM2h?#uqYy6j8}T@32%4H6`sg57M+O`AiYi@>J1X1lY@VFoeD2`b59A9uktls=JiPG*rZPlSA@xUUkz_y+7aHddjq_y-u!gqKr3&H&Jwy(%A&7xzVQs_eCPS~_3-T4CFs`$Av&gho7Yo3hh&TW=#_*ti7<TsWSaz?Jjh~ddYIQEr9i7=-DsSBDE&$S-55W>)4SEjvo&y{;I2$+-X=r`*6CB-P990?1(Jol5$$jXRq4l4Lc>IA7XQjCda6ZlX0PR4QYqs_y?M*s5NyjmTJX)adL8G#^nsZ307veU>woEkZgO>A&o=r$_73=W*K-_CE^$@+y@VTfR|aVsPE@zjpEqrqACeQTuxAGwJGQ$cli}Q#FV!$K#KHUb`gr$N8P~4+;`U8jBqr-%#WH|+7PxXn1D!89DBr-;e47e-d)#4SY>sKuG@zu&MfP+9Y~8Ga<;y%V#^}_#ed;Kf?*mn3OX#rKkd^a>qm2cutk_t(*p<018;1``;~#J3;ckGeOchvKXu{W50_V>PVR&$mIL;r5#~Y)iOUB{Xk1w=w^ACE^{Fs`V!`o+=Qd63!_2FGIxA;tFPKm?TZj>}N$4HssO;HgMeEK8`em-k_=u$&$v<!MaaxwZ-ACY0IAo75cyeV?$X<%|P2l_gim>44ob#(y@4UUk$t}q@wY^OcD9+0R=H}!STrJ<p9q@i__e0}HAKE_}6bU&q*rUgW_iHyzKsJ}mfoSlm4)r&^zXkSVXTDOpZ;3EnO+(Tkw^>pj@GYStELr2Fb-MRIbcJI_j+(aeR>`}n)-}X3tN(;&=3QP|5@brl|o;)6-Et@aX-P>oFv9~EBa|5+M-9d*B-K1m3Y-!8JTxM>ENkmwgWM#7H3**_xM|e<CaS3%izr*B?N6IQCw6ds$G+5%KrW{4S-Upd$g;8HmKI!W>keqDmzsBZdchcm^EXD^(Fq&Tk;{;rhGgBYOjw$2YHzOuTa`5utM|kKyI&o|(UBA48e*7pQ3GuD;=;40)(3MK3j#ts~<JYNR;dH9szn{uVTWMx?G1)ubByH^hiej{IO4=p5cl$MwTNv%DT~GV=9j4zS0VF54gtly{qwMU<l$3Oaz7JlbyZ00r+!jD??ils<IUzq+2b(r4V8H@?Jb0jlZv%YT&8$yw;5|xAd`cM^52^RV6B6XVOINS7(t`QD^!D{@nl<MHed+HfFV9iB-}H!XGz!AjW*kC-KhxoZQux>{fOYF?sCw%|s@V9F_SYXJw)Pb&THZvtGe6S6mrlBV<u&zv)`XFvH14;EW9Kd&s;i7JZ=Ncen)G35sfA~cg|KAtW%BbsOW9dXbn7PL`J*<Gx%mmYb9*Jp${wLjn{P4qxsq5amnnT3lLyxVT3UF8%F265TDpkbcsHoF_5+27t)#S+6U-VnGuQJcafxNLY}o;_cep_kVpl0++B4c)qlDYHL~!<uD0)9R!@<@N*RHCedb<)cM|rGVF~;O}H|gk9(%8s;0(TOvUpJlR<yVrAk23RH@znjUipol?$k-s4)~($}o66*9=<6&J;NMUC_UhBcbMwi{x{iK**QbckL}If`C@NBb65?DbG`x(8mmQ~_)rTlL?jDtt$S`%xhx$4`NXfV&iqS%DP)BL0DpT9?;BxuU)ip%9b6zk#`kr)kk5PJR4fXe)qs$BzWu=?Yisf@CH!q4N$4@6Aq3skJ($5?CtVu3TbE&+Pj}{b|(ffDHXwkxj^ssFisVLWyxM(4h+d}H?+RpUH4;meLOD^0`^x%O3HgA$gR+c23oIG*-uo;7K8n}MV7ESl`FlDkdT3fBban?Y6tvuA!<RBz84$GGOA_0Lu%9-6p&8^R<xrq<!*7T8<#wW5ce?-2X*Qx7$6FJ!bb6lXd`?u(y{rBtF2Fc#$D=k_i4IiIB^z6wn(^HI=<demTW5Sr0?hapX6MX%uhS_uc&~(!hlj1dTugMSjbFEOcTou~deo$7jL2a!rgJHfXEwMt)UTsvAJECxrE>0d-fvSoTPM=hQu%INOV(fAIh7N<H43Ew;hlntX!8ln4uZ@wPr_5lf6sBbm>h~GJ*hn3Y9Crlzn?OK79V?4{;p%FIWegTaj`u_3Wh*RR%y>J7BcjHeV_TI9mM-x}T&yhugY>auXArbC9WeOS05@*<BPYiZ`SXnN=dUM%f*mlz;J#n5Kd#^4VW8g@jg3JlDRqOKoCC)31>w{QE|X&$?B4BzloWg1yJv(21)j{kSis+32Q5ur0K+u`g4%ff+6#MjnnOZd3k3_@aQOn`<!t3~C?JO9%6F3gjklC??hTopI!Mo&d6co{F0XL5EOqj)rI&vz=(2_aWs0Yhz@sn<&R$NPcdaSoXa(I&FDAF6Ni@EaOP3z>^E@6FQM%T8+IV|2HFVyldzY%|hUqXZGg(es#9C<!gI{liE|NmI7Da!bKzAGW(6(jeblAe2mX5aYzG_vI@8U%y>bQ!g$vmbigJQ}_Y^8?#D`;kW16|bFM6ngyh-xCJ(X))Y`BspI_G;Q_SWgWGCrP+y6>Sw<N~splc$qU4X~yndyl;<H$@*q4+4QWW3f~p9eR2hFR$v}a{&45Ny#D%{F<!%Y3*P^<H?YWl%6~m`=+bKCZcbFEV!d~CLe_w`vsgR>|8ma%ZGYiE@t^ol{3res|B3&^f8yWqzW`860|YGq000O8001EXiv~{s|NsC0|NsC04*(PZV{dG4a$j;`WNB$GZg6=401yD6000000001E000000001+>=)`A5Xs0;#!#)El3JWxq;934Zj)xBuA`uymS0p-l$aNvUzCyx5_e0?DNY577iT0EqyqUG7CM?b3bhIp0It@h+IDd{mUh1qP3&&XR<P4^RJFUrqF~2bWn#ww1OQM=0|YGq000O8001EX6hdud|NsC0|NsC03;+}WY;R+0b6<BcE^csn0RRvHfWrU)00000inRa$00000oLBc-&)@rRYcG|SltNQW^L6e+c1043%Bo0_$gYrv77Yz0B@K#1i0XOnr;$iTNr|k?(s)x=RzAM}!uR}k&UKyZT-SXbGsA7keD`I-LfeHRCIzh7;vY82QFjtqJ7too?xeM$VOzsiZ(JD~7O>|3&S$UQuw~7^^DXOEZ(8$jZ)js>VXA92#mH25x9<OMNjo(^!-{(o;Pz1o`o+!yoicL-ncCl&j`=OP8?HttUec%E8Vi}2Wgl`ctKs7A3-Eo$bL<E`K`P`!pe0rdx=%?%f{7}9$PIC^V|S1XKkZ>h&QjP}AcBMNfjwH0jC;PWBOs~9eLq}ARMy1`<Sg~UZ?im|=&}l)Sa9%m#(Qkmyh$3?<udW~Ogzb#v!b^Tf#!~3+ls1)S*H&5om2s0@eb(G&dB86U*wC38hSt62YstG@mcm5@YO3tm%cH`^=%bIO&tQ)>=g3$P%!+7Uj}56DzCmV8ggyNK$P}OEb<B^t*VYx^$UZa(W(%8@D&Kp91&E{J&5){=YsuIPO!i|haC48=KuRPmOp$>npA~2@cr#KaMg<^?7aUo()9Tc6em3+j?ty?LpdBb_8f!5LpR{$@(twJX;sMZxd}Zg(`nhEsq|M(90b%|g)4`@p#0V<V3UB3=<Y|Sc?$ewc@=)bV^!$Pc!nPjN@L?ZF|bHdq`$JvXzeO-N-Kou>SGyXN#`8s*Xaj^Exy>#Kf$}lkHbi#B<O4DQ`MWvgdH7*eP6wJf7NWb5v4#6nnz<>`FwojcMSUjoaw#E|Dn>oc&ys9P4J(P3tee4o!TXPz_bp1dc5p9N~8<XUGwHri{mFTidzmx?6y;Ne?@xnej+{H-3oiXf5PDx)%bqrY?{AKh7vbrJX7zEVx7H6+84v-`BuE(UJss$%mjs}yJ5={Z+I9|&AfleLyM*;ZjlXwXu~g#bB`}%KPOwF)a+&OCHof47`u#N`EhQN%4_(!do;@5UWeg1X&|!p4PKwDhsP78`QkAuG-0?`kP+vIH^#dVVcVtD&v=I5@1a-V9B2%N1(SJ`i(Rm=%Nzpyn*>|4F0em;yC84&P9i#AgI;{!2?cf@%p+EuUvyT1*2eRom@CE0o)|*%aTGr{T7x#PcjosTn#&vemy!tZrx|*b-any3m%Y${lK-yYlBT`XZqy>Wd6EWy@xUrPHn^7dBu;~{at?Hkyn|e;E@)O#<P#IDY1K#~l!Zs*i+fi1&Uh?()*OQiyT9Y6CT~0|h$Ii4uY#w|e?+Er9TXlp2M+?X*uKv5WaxYntBDYzcUSykk&4^6sMG_sU!O^kXw4q-GxaQ|<2?hn3jO2^H#|Uz3uj<_eG2+J++&Jbu@IFb$*y>f!bIN(WZsqCMC7wP)sjAnHx9Xyjm@V?+NmNzq2>_iOWelI$#%>xKMG3zPQ~cc6KF`GEchFpAYzpfSSalYK`+E<j6eh}5ANVA&UWB-_gm0hlZ&adckr6`JBaIYQ7YxUhQ?JG(K)4h5a&9YmsRSA8)2tG_|g>qT8b77EsCLh%>*hdZbVgT-oqC0GmsD)gHOxV=(WNL^ioe4Rv4VX9WP$ty(Bp%nrd1n-Isxn)RLgV;Xa$=Gzq`{(Purrx7mAJDKdENG6;pqQR!DgyuqS)Jn9#N9g+v&<(x$LQY_0t#Dv-TQXzI&;Rxw|c2m&4{U-af;wD?aD+=z3NW!}=DR?X5MJ87qf|4)e$jFozIIOkwQuT*2!9xF7Qf@RJ<R0pvcJ(g&SYU;xf*P>(v9zGt@Gw06n+)MWw$OSsAIn3;kT0$xMT?ctWc?(}J@=9<`Vvq#Nl=FKh#FVn_mKps4}n_sQ!+QTmf3oD3Zg%o!>c%Bc>5%b>_|C-UCj#kV}?A8zrk4MfPGz*?PX4voF{UF`M70P5l9X0XEI;qFx4O(_kX;^4jRoTC#R({PsInI8nBGroy390;&l*s_cypsoe13_g=l#<8BCtUIVLUoL6nN$vKO8@^hdZQ#KnGu@E##NE~!XgIN9LCrT})Ya!XyO&}=N6I}SFPPN+MS5yKt4><bvR0r<(HK-F_#lJ67lMcyWM?9CSD+Yv)X2MX~fokDn1?HoDRyO8a<wO%mhN)pEC?Bbs11)=gLMAOwlbyF%2f_Kyh&UMLT^pUL~v2#wtjWt@B`py!HFpm5@69xJ!<api0Kwjs9JsJCKn1n6b&pJEX@bs%lOuLo~=Wfhn@oPu%h7khPk2r#1_68W9(u(;v4nbI$A?P0$;-l_Gz*p<<c>UKuF22^H_m9S7i?20HDmg@!6z#;2x=6HkPlNy&MSMMZj=(PXH9NAc7b<*=N&OLFObZ`dciG7vQ8o|W|2T+8y6l;j*L36_3bW{g%URoz#ZW7u1G#xe$d~Rq3~O-3U@%0^ARTIjrr;90R5FwyfoZjOaYk?))4yd0mYp?hcI01RdSmd}4|)3Fc^poEoP^3}Gco+`PSl=17c6}a;;}c57<Wz-UKSLCP5V{CNAAaBk0`P*(}0;+O4XTcZo(5Ob`S^Q|1{1UYYqX_|FU9_*gQ<vzW}-`Rfr&HEOAuy!f(g_Iy&@e0YBpi>V)}1cmly%4=LV^?SPc^^-#F?5J}lQ3mhG+VcwZN*fn)3ybiI(B3~K$@=_5r>dz$BZu#WTzWH!9_ZMt?=71x?#dv1e0mYmvz(J6J<6ep4hh=IsOr?z|tf<6u*|t!hTPtW2QKOMLhvBnBGfr+$#qD1b*<6bf(q@x_xg+kxRN#W+ZYc0yEdt<Ha3+q9a>Eyi`#8OCAJ~?0A%Y#<uBd!IANT%Oh*6ogOyZsfdfw3`3mZisy=EWWGxGp5*%kO=_$Ds+QvkF7L9`_x2DYwThV!q^=N#O4tS3$EazQnyUSCJFP8!uYDja~Q$oJgnD`znG+CyUc(4M$_UrA1oE<7l496nz@inIUWadeCcY#6tX=>1J38=SpCKoj8Dq;yWiqLwuO!;M|@E%GVO0(T8*0SlZ;BpVIj=fx&yo1nx=VkJ{}nGIP1*#O1iu;TM1yyl(`;foBpxpW%N-Ni`2t0K6iQA%Wm@?dlGQf~UgxtQ2pLvnv_Bd6OBv5b8Yc(bM+<<e@{mAqYSL_A9{@1!sY1XozS;$p1c*CzNjw357<$g^jg|6=?5h2SMM2U8~<!vpUr>`Z7O^#c*iwJXei=>?8(>J2R5?op^SEF|?dnS#$(C*tG_=}?vM8h+&j!i#`%^!jGU%6?8}>E>}v*rN$AJ;;P@8CqCqSB(>22E*;fvCOn(KDidDNBm!{W7Th6**kY1ysw?f`ZgJWr-7v7j%O1gBJeQOXRqWuRelhWvlBtLP7U|uY{9<b6vr!`0_+$g!f&anfT)H;@anJ=c^{k#>)AQ(_~Ujo(0(PzNXmr2;V<e$|8iIzz8_?Le#7$4vn)!(9X1}H!*uOjusTl%-1GJ79&RoGql1nZudP5EM|OeniJMsUcoboEFJRKn<79rH93EO>OO?0Wg@DDUV0M~tortPEj4Pgxg9nmvT81o6oR&=rKNiA^s77LQ_z`)#r-jHYSWI48i=o1lBzW-C7cQH%5{adv{H4Q5c>6~ww?k+TTlB$<Qy2<|#C`hk<%~LPNZ-WCySSq1>{p~*=@sefTMA}2N4Xp~Z<ao%4PQL76|_yCNy?AkfUFmFtih!Pm6l#bv!ka_XSO+Py(Y}t=XSy7);Sm;bAyO{tw0a&@VfHVDrogOjC{3PD|o%b0AgS+9gts*Lw`a^@ts^0_NYh2M=MC^=j8$|??)t3u$q<sdWBL}1-RH6;LpiSc5d}ua@%k~aLgVdVc%%nwJo2-n(cxP^AKnY%EHAdHRQRP1=NbmV0oquN{pKd{?o$hb|!nmikdu}OvGtyb5q@}oI%0Js0sYW{}P$I3gQOi8JKZ4h+MijmN)!khPnCAq3hy77CW+<c?T--{OLp3H&lWeQ@`V)144AGi78IpEzOHYs*>e%Wg*0`6c_u|ay#EIg|^B@RJa)pIexDk$6IED+1qIBo7u;%-t#9YdRuU<rh`1H31)xZ77|14r=%xjG@8ySM6vV8BqT#Qq0h(Q)i^6~I<*IUMYV9(RwpR^*+-Vmn~1CAuE3<F)g*MM2H4E<#w^d<cw$l$ncrM5NGM$gtG0hYOPA~TEWDfqzLsKLmyk27>?S&HI_&;2ZAe?wi<fI}!}0Z&s0C>#8~lZw^-3iXisR|XCk}fvO{u^(4N}M6C5slG!obB!{FOUJSbXRRTmLkWMtp6=3OgtMQlBIJyhfdLdS5_y(PrEovKie)B>5%rsyv?_4_AGi`PUI<{NO8ZnCMF!wD&6VoR>0Pot=uVSNrf&rXLwBIR}5AU4qN*_rTpnAI4cp@Si@bQSZV$_^?Tj-&VN~El!%#KHWt4lQ@~;Z%<Sx7=+2jukiS|IuK8qL5DY8N2ndd`%|XzuL7N@%rQgW_ihO$oR`5R*CtS{1;*4^R-Gtyi3nCM{fW;Tj6kk`5JplI(f{)v#}%nAAlLpGcKs+3G(}H`meUq+eqR|`cdZ)aCe)F6B5R19wJcv_s>*-K7tl3iEC0ydf|uPQ#JpG9((PB5QJ;&FH2qB*w7TBISxlT>YI3BnYef0LxN&f(B8skv)~3(vKSRc`?bN6BBIGZ*hnxP`)8P?KNPgRi8bwdJ$Rr2U9CZ!;`c1-9PTO(zy*7boU>(fq+CmauO3{`OC0I0Y9woKLP{$esHH%<8oRA8C%p@F#Dhx1c_G`AW{xOW(x}1a=*PxZL4Dl)BaA)Z!{Gr@gHz1+FuW*~kEX@~!!6qR-*Zq!QU?>7aliXmz9z_zMt4Q^dX7E>_8Kx8sK?U4Fnb%(I`STMnqy8{F)L29Z=9$v_3u<9i_BHI>tV1sdOVb^RSJ*BQA%0=fMrIagK-GF5<BXPMKF;eJOP#ivst%5&T+AMr*8B~^#%S<!)CS?#e`O%Fcn@FjNVG0EA(~!JTudEiSx|?!0o0(c2i%4%`2Np5aJO?i-A!^(^W|TG>5iLRYTpxBaV-js>yM*rkrCvNG6%UTH&VVK3GSsWh1EK9p;W&OvMicFXnHET1Yc)|Qx*zVeL7wje{X<@_@+Ym>c`}2&KdmOqKSSfNieVOB9s54jLTeN@WJFJ@H$?FFJ&T7Hb?@JLUrh`J%?bSeGfUSu0d0Be=^VAwL~iN3wXrLK<n}8EXqL=3{_V^+|$YQhJezkyV_vhn^F9O{^|5y#w5C__bihhe2hQh+Hljf36wj(gHN;lfKMLyFqO}<{%O4qRZp;o?9K7yakDkdwMwDs*L=YJO%DHiy(i?{7r;Ay41IlRIW=}up?6*CplJ9F?tL6j-S&9V(XG$0Z{=M0?VQOx6&{dd+xOs`Hws{qIK&BWPZSL5KCJ8Po(=y=i{ZPI#;hSG2i0@d!yUCcsPs94>-1+s!;NlSv-D}*hdYT3l$S%5gfKsl<OPCaT{!>dDN~O4j~%TR;RR>Q*}8f6>rz-2_Bffdxs9n{`Oh~_{qyLn_sp<*Nx7g%K8>BLSPum+FQKj6C<j#ih>hp$=-tkjb=khs^jmQoERgCUmYTN&xs6@WkmZe19YVadO(7<`h*3+q6uu?sFaD@_$x749pe#6$o>tvTJD!MR(x%@yCqb58G`6N|BOLjVy$kTC(<Iv0<O~Zd$Iy{)T`+aUS#a{5$5*sGgeXNFnm*2edytj_W4<31bcgJNDgQcPw3I6>oVyCk{MGnvE5_s3;4kQMRs;%^6yUIdBK@r_%P$-gNut!!NWWtnDPQA?x9HZo1@crNdLobYmFL3@>CfDXvd>_ZwhFgRwq|4RkHQ81F<i!<02IIRF9%c^3s!qvth@e341~8mBT=*hD~i*wDEJCgA3q1J-%<r(^~YJ9$|rcxQG{;0bs_BWaum>)!2SG9Cbs)<$<4D_W<f7&c6*J%XDhJr*CqH^CW@(I(b&6t6dZX>1&`b((%X4WaC+5$aDIgtANRZpvQwm}r$IUHUUr>KiPfQ-gw?_AS1;_(YXm8?vD7SckcCe&rS?ZsKutNDO?+&EWb!63%sm0y^z-n{hFmf`*&RM67zoZie1*@X<N&V4fMtmq|7#!zAD?Z2hN(@Ybjw*RI-pN%7YU=z8!76SKNeIar-1HhC-gX22v06OuPghPx2%U_;HvRhyq{B!E_S!r{<Z|Pb;u*90;)M1(|8=Ii%0iA>io18HM+6j351Gj!Ab)T<jMkYj+YlknSI5F8m7F+q#hi2rvW?C)rrygW#qa?E-Hkq609qt)Nbt=qSB#<9dDzdH^TtDbRLmMg;%g(>JD_T9S6(fQpw8=2Ds2fmj6)_h*^d4_+G{k&t0h`V-_58+!pAJk98}NNPHt-ww~lHR(uzT^)0}1ZE^PT#4DU^EP)FqFN7Tf{%n-}3A`qrM)vw=!i}tC2wLC?S`|&;8PP%d{$7Q%E0VE7I)}8reTeJ6E(fve>%gO!z}0(z-AUqnM^h4>(b)l_;SOA1g&urt`p!ai<)P}w8Qkid1+@J=7b<mzI4q;U=LV7gG`yh0cRmXZD`o>;87w})0Vk!{3T9N^<>W0DnQ+!ELH6QOHW%))uqURtW=$HnPPomS=1%7uHl0HKCmeLvU1we*hltVN=d3r*3AzoeL0SJg>a9A9xr;*B-@lo4Ct^|{J=BQbwfzhCxN8~qz5dF4FDvtFb}WPtSy3!sF@Wwd9h`Eb4?K1IfU<VSaiF>kj`vt&hutYGdu;+syZczSK_WV3xpGxz<xHk}ACOyD$i#p#sCcdbCDeaIc1;jtIpeT@^#J}kd<EogO#sIU=~%?vVC&&(7P`HXOZxeYNm$1bv7%!5^&ua=tWSg-Yjt9;luBH@JIP@5JaWxA7xdzHV1M)|co6lO>(lUsCnblu|El#-Av?k`yW|M&JJU@ZSAWFv=cUXtW*@{H4aNxVOm4}u=WMx)6b47d!z^xu<Pjq%P`knM??$2Z=q+UUWjSY1SHSd3ZBVIcJpNb`OSU|c=BLW>SUaqXr}}=NT%`rx@-f6k_Xo*unYoY|I0MuMIdlqK2x^8ryS~yCi-p!hp7dYTC)uRplnpkFnuC`g-z2+?o|8>S(*#R?Ohm=rKFC;inkgT@2a7ju0(+ro_%yDKv9F`xL)0STm)Os_J8mX!k4%}BK1cfB_AsN5=fPS<3SI>XxT*dZnAocqXc}X~=42H@#GQHwSG>kl?j(b@v>GV9D5-0lehH^a-T~!(wxGFmGZxFXktw>aI52Y+bDDjP71Bub)S3s{Q^P?0GGXJ@rRm@WLu}sENcy(c<A%rm?5jusJ{t5Pr@kNIem5iuIHM@o^K~MIzSsjx9;nmZS)<@~=mMbfOJMqf11#e2Xq5dC4-+>vk-_^M`C0muJ-L$)QRk{qtIZi%*Hppf>O7Lwu8ma|x6sF27H)~ngQrWEu`taxqF;2FgHyX;(dg0CS;`WQe=0@kmCbB+^;LLu&Iyx!)L2N^F?J$#NYLuK41V8HqVmr1(6Z}1cwevpcfZw~Roq$RppCftccYZrbhNw>jEl!Fz{R6Zb5+h~@rsHtF0CWDSW$saUwoI%i;rSG*)w5TOCj!_6O1Q&8LXQ#$mFZy(c5wz37Mh~XIqb9L|hDB3O2)@1zC7hBmsLwqL7z+f)}%@aFuWz=XmifJGQKac@6HvIsfuVYW_)vL(MF-#FbBU`cB?O>fjl#ahRVNAXt;Az=F4!!rL9^Sbwk<_TF{`&x;dSRD>pXkDJLV5=F>4+k@<&_G)<Xv>I(|dP$VzEO4th#uC&162G`C^skL)kIsEzYTLizV_yw+?bTQ4wpT}!wHsk``!JE$Dka&<wb)^~8oq@{LSRu7Sa0hFjhPAXp-NxCZcF1%+tu)M%11$9)e+n~cQgd0y5fIbpV+LNbZ*RqD(0KjT<2e>%fye?lgg;y<ip>Mbsrq32pWs7LWG|sJl)y_R|coS5j=(~UDZf)jTn9$bB{O<F2Wdi2pUt2aJJt~$W6G-b*A4C@TaU;j)5~+{tK!)OM~(I@BiTb!#rH7lZ+a}B}D$wG+ey#1impOxXa}jTmQxoHJ=8snU}MPc%hh}e%WV?TlCc4>`Wzov&m)8Cl8=b)e<mF31YHr7IcnD$EgZou+}dekcrYi7JY0<gAmnyEX_<yM%d96aX4ajk&Q|%fzAJwG1<uRAmow<>POylvV$p%XsFQP$Fd;e6wiHrdj_NoO~~8Rm!Z!hmK7~YWjep)=~9oY*yFMvyuAF;dSEMz-Rg<sgNrfw*>ZCDjtizJY9lA2$mgjla+M1&;;ipVq+(SIIrpra$^9(k4o)wu%~M_l*9IH8c~<eLcTb-)8rcE*T81!=j^-c84dH*Yuj6FL)%dCM6;xh%h+S&$F<|u%X#F0;xTbVWtXzz@{d@7M^DLZp=o^|`eo8h4Q+`9AB;RbR4R`g=p!c0*%$w8$wWgC<%E{9lu5)sH@i~XJS1N+hK2yO#DoUM9`pKgqP1<;?m_5B@h8r*#ezn&U#qMzsr)W=3UD*hl!{gcArK|8rdllYVUB&7`GBLC@8sioEaaH*WX!<X;X67Ytv`(5KP@g7AVnijV_=Z@)7}djYH|8JjH<iPiivjSX;R2o-uZ(8re!%7UJlJ=m70UjE!l~nN@GP_#y)NH|$?CJHsG|>Tlr!Xo{!Cz{ef{uk#vcA{ZxY(BQ(+h9XVH>~IC|s2bnb}7WZHGdif`<*qb7T&QK`S~_|0b>1Z_V+Pk+A&b=POnp)eWxe4;SzeXPzu=#`=8_NUO<PYXy-;V1H-PYQ}2?sIIp`kjdgj*=aonS!Bt(kNJIg3l+9fepLV(4h7-S(Ii9;bZbqA*+O}{1hz+yY!Set;vJuBe#g+-(n{FbSLV4$$>uhgk)$>hpYRiz~x_Be6ZwHKF@3xJ+CsISF`~>@$OY}(xx6B%pFCS)n(xY;{*^FpTUpzDuP>{7396;EI!t|lp8bpHYa@XJ48$yPvb-Cq49VCn3#0qsnH5F<kDzL)H$5H-<-d^N0xUjilYn8&E?-TujW0c@F?*!hSqmj(NAfAzy+LWWRp4n_L2+L8KXzTDyQ=sMyH^0`(CPY$A}i6nL!h#j-$O-R!~pZA)NYVCjI^0k$OQcF4maAY<2&GSPv22_iZ8$r(T5QwKLgCy;Ue(qA$4bl?P$F<516SGv2=EhNUl`5y|omY^uf?Y@PX=jT&RcJgmxa^y<$bZ`RCCT!=>p!F2Ric+Xsx$)e<&WqeQDDBgFQJ58A9#>XUC@s}!u$)E41L2`{LRnHzn4}ZQ!?AE@)m&Qh<&9?v&#}?w?5(`|n)|CE<(}KBny0nUZg1_+wbV-*5uc_oqccDAA(_F~1N597pj#K&FrsmXmK$+Jtn8!b{zsbq}il=!G(x`V{3^X5Vf>9OW{IO4()GphO+V?y01Aa&FY0eIMRS-!Bn?vY;lLIB=PQZqP`h3jmhu~tffo@oEmYsGlVyCWzq0O9Eg8Xk!S>`SicK3!R;oMp=)l3EMTUa{O=V?Io@B>mh^A_6PZ6~Yh%Hgr$T0GH^!rGSjLdF;){1FllKGW>jtANj3&VS2b%g&R;bH!iI&QO$J)cyjUe`dnG#&@hE)P~=({6B8ypaT7|`~~@3=>^&!7YKY!nouE00u9eP(4%)o^Eoa(Ou_yQ1lc`;`OarROzZ+|U7Ji#sL1jkF1-P%iN_!<$(sLoay#D|7s973egcIDcJOCht$10NA8hn0Q|i@|PLpRdFnys%<=-mtzVeB*Z(S07xE(Q6X)mexwUc%vpMXhGvuRN2VLDY-n1()=<G05jr}ZPIRCZVc-ln`IawLaUhmOIHCKFJwh=S^eqsi+LQ4o^02XK|74%hd>t+Sq7RN@Dwy|Rc2_TE8@mlw%KsaNP^oWj<%R<Y)j=TK5m#Ag4V&#xb{V#gNE#lC?{<l~Wj<Y1)`Ih*W&cTX^g-QPeY|7pyU{lfU7!V69fD?-}I1~9HaR5!z562{zjf&83rtpC|KjQ=FdZ%+upwPjC8s`Wl>pP2*ky06Fq;b^vbyeO@FdKCqt9ys;u0)E40b(*9*lW!cekAE(_m$S4LrxBIj^iO{UWH!lAn;c#Kd;D4|*0u^J-Z0{8v;?HhXAN~!G^4{pLbP~aAx)66rUG7<FE}-s-reg=_n-fa)8~1C(z1(8-T4ids@zAUK6<h{=m7aK7ogx@*4=kAl^Y$~3VR)n;G3LkW+df}t>OQCa$`A4%~QtwXla_&ktkSeP{-DMTm>h*AEV?~RZ{y`jkERGfEVUmVP+TP==Wkk$%ro4Tf7qXv>Yb$*AK7&X*c*?-^m<IZ=?6L4Uj$c75<qz3-8!m;Y!CFp=aa;y#DMyIWgN9UBhYwTl*sL<KIg-&ASy}8FwSgxq`Qhlz=O2V|R_4N%`C5V4!#h<>YNh!+;Tn-j<+L|8`yJ&b4fXryOqHxRQ<glK@k<M1%D|?-Ghr!Bf98;pXLQkT)+2%oSIo0Y3o^FX*ChMJ_IsGlI-&O}Kk!H%_170a^&~wC5aI9T`gY-n-3&?T6SY?gM17jWFDw!!$+mL9=&|4A1(=+5hW8VnIR>)L{xr_?vuFnG9?5=Hjx(F8m<*NZ>p&NWPqm!_Yg@Afr4>AYJ>MjJh<Pe0wn$=ASKR8uMo2hIS`ZcfQ5V88ZR%pVx!^;x^_v$^a9-{_8DHFPKEBGA=P%#o}g4f`^1TUSBB5o30oOCziV~QL|z)BpF1WJn@I=(gHB={LL+EnZUMgdq9Rg5=qIUBTUI|74EE2Ln}8qbU5ZoR+d)cCEG&w^LRP9<t@fnn+8~{*JxbmJr}=OFCn9!P#An{!pX)qprn!{A31U#mgoy}W84h{<3HZQB&|BO$>0D<$^OQ9xU@n4mfPe}rvc23-H8_}Wx1Subv9@D6Z~-YH&W9xsOaf}*Y)z?nAmJsIxCM{5Y>Y;k^^mDTu@<^9=2LfW(TbjnB<RtbC(OiA9oW#utXC)Qr*D*p#e6zN1*)cX6ARSj9rf?h1L8b-gJE`raJ_4;>k5!|H56^tJ8{6-99Lf2XLsOgehz;U?p~)82Div78zDz`Ur*ap)fcQSi;_x`+<y*7EQ}pPGV%rSVX@BZ0(Svg_R#+yuXZK%c0lUU?GE_CX^7tZNT6KohZ7YjE#G9l@&lNNmC!gJa>k%ierz-gke22yVDPukz2X@J5u1y%`K=@8;kZeLEvTF!R>vKOt#+d<j!t?L0;;dg{W-~7#JGBJ`C3iTudILX=OjL+E)f4?@e&g*HeOL**u#27lFt%DKzg|OBRl|#O)zASQ|77N*9l%I>&2qW{W)BQo9A;djrV&xl(-Wk;gc2P=ZhBi^gd)YnVaoD3m*Vnys<ei*qHDiIMCKXl>t%6BZTX=vhJJ%!%>%*Z&Y#u;mXId+!eme6$1JiY~;ADSqtwh7W=v6Dww<J|A>%$;0|78wGofpRsqzQZUD@6i!SY3q39BF#X0(P&?y*DFx^8h7gB?nt_lV=f}@_sm0315%_zw6XX8PLFMu%97>ZxdmRp)Z#7}BVTPdX`YaHvZ)F<~q=3(hNN5|lz*;pgfJ0p}yc_fHEiHXe==XtQ=lAHWI}zuQcc5RIj<+quX|w3R{y3sYKj>^=woh~5%!EF8)vgW`1})+9(RdKK{g6F>E6nfuZ2=D))4|9v8{+(@L*d!0pz0zEy6YA3MDh+WZ_KarUgyplrcJ}zfD~|2j%Rlb&T!fHRB+P*Uy#r|Pvqp4Q5o8a!Vp*I{mY&I^K1zQEk6t&^bo@|3y4SeX0}J!9Pedp#_;a@&|P*P<|HXGYturE3bI4<{!QR?Eu8pgd=W&(kHCjhngVh2v7j>XG*eL9i#pAbkUd_Ay$%$IgR<?YDeuk5%iV(HORYFYrx`o`oO8Tud=s9Z7-sv9eyPjbVGDBWk70fKrMg{)d+=o4ZM>VNg*Kv_Ky`hZz<`{=w;E0?>uolCJ1xR@OUz=YA`ihRpDvQrA_Xe9d!VdD9T&NGkwB?r_)fgY39%_qsM1#ZLGL?BlNt}oYG1HXWdS>RQ5)Yy1T(9#qsSkR^KjWI5YLO)qRQSE_*^Fo++q~S9=l%V8ha3OS^@>$Z&#D{6((qGv=@uqGnr&|%RkS(OuA*{d9N)Y7`XEpdcQx(Od}3Lr^hX*jXS|@?U5&H$qCpoK9#)vna1v1o`DutqxqwH!R)~NYIJxXjzK0oq-&3ZYbrOGoOB?h96U)D*%^`o`EOjyR1v1SUX9ueUWP?~YX!@A7gimX#6IeY*1=-|eQlJ(P3@fqD{t>+O?yJfVTTNyw|pWRtS%zsvkq`x@9kmd3Q21I^f`06AwfdbWJt@9FhB0BGW|R57!JWAqVTSt$XW_;Rml@}`(vxX(zYB7#u@SRjO1Y4P&p{&Ohc_3Qt)%FJjmPX@E+$Eqp;Qz+*Z&-9!)I-PH`4zS!+n{tR93*9?jgQq+|GebTV;rAHd}SUx^G2!drVAa5ufd4vBXYQK3W3Q*s0!yqJlpZ4^#e$}{&N&)Qw<T~V#^8V0$gq3X#3B9`m`Qtubz{y$YLD0V$;`g#*C{_%u+o<jWF7Z1smQ$HYaQ6?7~bP^q>-)5P+Rnfvn9Ot%H!U7u&-b+IT*Tve?s7(g=t~?rV+f2sye!Yxa3Vfi2FS3`(f-|mWXxeiKn`Os>UwQ#%AM=25(Qn~N=>xK`@)-R21e|dBHFkYP6Ryk4K^^}dX8P+Kf*QxYbk_h+z5yON9wyP!i|~APJp6U3Wh>WgguCx_;80#1bD8x(aOmYh_Uriq-0wVtw=BuSG!IX@aWli%DGt2QCJ*-Jku>e|kfd`RwW!pFHB?k~6d$bPNAsh8z}tHP{HU{|F@HibjsE)#?ienicSba*V)%FXq+rdal})D8BQ<E+M`ske`V>lE41?b?9T;Uh2z|-*z)zhBLWA8{rtE^QhrY4r)-#E>YYfz%ozFa<Z)ds<3V3Q$J$~~#gF26Eu;m6tmydhcvxq5hCgl)LU$+Q{pH<<HMcJS!x10qpR^=7P_rrzri}=2vQT!uCLn8afohIHd2ATK2;8T1CD89|*-{f1+n2VBB^^YjOAt(qPF8+Wg6Kv==A1nIgoDppja;FnmCVJ$_(N2;<C#Kk7_Z<_yh|J~_8q?rb<^q1sfh@jrb{KbZMKTR~IF)|f*9G$X)9GxY%$KZx51MQY4er(Amn&(Y&f*dZ!GEDWbUb|~_}A$M#OZym4IkL~(GQU(v~H~#E_-E7ZO^2@l+_`8?zLb#yLk=$@1r;w8!65|TrI=z7gpu<B|c+$eJs7SHlF{rOo>k(ElkfXSV&wSO7Mw>cTjo2g^xQW!_QCsfs(@aAop`T<{W>CxBGkH#o`j&+F}e@sv5A=aX$+C3-R8ypUKYR!_3^~1+0*lp-OJ&iOKzR6r4?h2<a|pxvj&0Nt}dr!D3WXTZQUNRzs(RB#rB~pd03X6g<kcr7iu=Fk0~hPWc(YPpUqKY?B6!TXYvU=)3TOO(y)0PexR(Y&+Fma2&Q&+t8v@$^3(_Gig+-fF2ie<AcnVpk<CXy{E;&^C_9MRA536?r@;S`~Pu8xtv}bwVF=S$j4-o3f)~F!KCpAmYB%UGq(3(k$yM%Pk$j6I0*3>sd{YA^C<it(n#JdUWngnnz6+BB4}EuLBXE)D55Wo5f36T=aD21oK7QwS<~2^iTOC;AD+|8=Yp-wC)|IzlZ6{Bq@{iXXt7C_Pb{7WKTj*sxtU2&9V$l0NCv})XG(lyT?ou8bcL`JvAEU3%^{+s26TtCsiv4UzEPV5A76HW`tKKLt$ho2KaryHi!||rj}Cv#!<9dAz?z=X&fwc7>+_B}U&#i!{Zysr5M4JxoE}-e9TK9K^WrV3wC$?~HJIYdVqz8Gz~RF*e5N!#t)oQ6Zy?AhNz%3-rRa8jG-b8fH0yOHUh!T5S6ZHv2cv~xMDrjS{;fit{BIEJp+G!zCmD}w)v%bLDCl<Git<|jVP_O`OwnA$idPErv(8%aW?!ZGQxc(|5m^Sg`Oz?XQ#b7L`O7_vO(z=FTS<xQO&I#3gx1Gbu+20Q>Sr4Rd+x+Ni2X){j;ga`W28xwdMd6*JJ_6T1DQ`gpyk%v4(Yw7EaN{_Twe0d{)O~K_;v9)>sfjLhI&=7XMPLooi`01P3uRAJ-@L=MU<a;$dp!7GhR8#i@&$#BbjlKP^;nHR4r*9B#Lf;1y?NjSx3UDLfkf5_I-r?dS!$j?y=Np=QH?uV>7inXbZi&ZNQ7D@{y+uKzIB%sP5}0JEUvz@f;6w+QEZt{2GM$iHp!bLJw<2mO<m~)12C10dA;`hWg-8T<5<6UfC!TUaZ!!!nzIjY%fN?Gd$UN{~qCHt^|*x3nAg_cKqUZ5ry5ZL)rT&BxHdxSF80eTRl61a(-s;V#-1I(Ysx6YakkhhV<FnX>#=51X2D`g)%CwR7aoPQi7$iE$}4%D>pW%1>N6`XFJUcNb=&(Y~gJQKIUc^cRzVq-KH#e?0vb8S?b#JN8|q8mn??-J7>{4_c8tuyF<Lmdin>{X}T|iGxx06`Hn62&WSVNNKOD;I@yC#3(Zk^i6@oRxB&@0L%?sAq~G$SX}0_~G!D#xvCWEf<y8Y*?|&3e-#iQQVxn|=>21u@TtYa_1H`ZIGr9CQhkRQX4Y{qtI7cG^MKW%%+9)mTKk^M<J=Ec*k9My8;3iHY{bZPC+Y_>T+<emEWXmfyHjo|5hS}mtNo3N^b8M^If8?g-N}Tagg=V(&z>M!vWTMPpOiz*Gm8#F<(Y`h=s?UoybO`fS2?v?W%_zZ{-}lJPISKeNRTHxZkD^nT4UTwE=F@zN1$!g>82{`vCMd>nJ9inu$-2gWS#BK2T8;vxq>0F?bl^byQ#N$tA}%_86{J@uplpd2I(6Oyb>a&S(;aZE{8Mu9$3k{%+%eX8x&^mo_;9j^Cgb+}6m%=Bg1lY2eC0?MS>8Jn%Wm$1B~@8mwANyrQgnpLzW&V>9L|Rev$bigUmyG)QODQslyT1J^I-KXowEvDj1ikF@l)n<_PO>ZM%p)$*UIT6(9Rb_ybE#IZYDa2zh_ZboXCL^XV{~uL_L#EbLTCM!6(SgQM|E=$?i7cKQ)WN`u!8oZkGh#yhen&$sNJV6Yk+z(?XbOb4?I;aT0K6)0oJwQjCA(jM<<2u~|D0+LoL1B7NEzt<XtYZ@z~@UlR<TV*=yaUJE+Dbil^mVe*v|!($tF;(EmxSW>eNe0p<m&#M&tJSG$TThEZ^MKQ>|&maa4UTAsX4VpL}!Avb-X8WWBC+$_`{coPYzcS@eyK;oB2L*I^d<cBZqRGwi96m^T$XOY>39zdHw%k^sFTOc}V!#@_(`5i6Qd+e1*&uUw*~XOKj-^j_T2Q%kdD4Hlf*2<pB160M;F7cwbyBN@{nE9}OztfCbaflILU}*B-&;o&y=8&xc|gL~zGdy-MR<dU(lG4PEjas7feya5;k53bW3~asT;r+(f>f<UY#MxxMH@N<Lo1RYV%>U7U0NcjohgRpyPsfs%W5vReH!_cKAzN@R1%x8^PKRXW(HjaguA{FpBOwR&61ZOW_JR%Z=1)tE{rGp%8o*>W-+Rq%we*@@5qp*7>%#!LaBzOm^?O{?NeEYYO|K1;f4|rvaki=Yu%V9u)=M3rlW(;QB2$+%)hz6m~2_TPq1g@WisyWbGY^WIIK)@V#ngt$i<7pWZi+&xO`ZNG|NmNnG>hM+kOYI)#(7-Pz<-$IWY`qgM+5yv6LSn>M_~S-m#60TcSk0w11Jg3KdYH6b+p<yIJuGZ`@xJC}`bZgTJRrlK3Mf_;qd>Iz-6ef^Ks>Z$E%Ho}R{K8E$MS)EAX{*0RYpt*kjti4=|A59hBZF<Z@N#9c>{?YeQ=!A!0QI#(&9M%)xKa`YX3EbwApIUhkJ!vW0~u0o&4E-t0o2q$gb#(k{JL=!I)*n9LPQ_m`aNuAN?q$33hb4^jqp^n|`Erb)_I$*v3Znoh|A<pQxg;LcH5~`emV*YyYYO5?g=F|q2;Y#$b-$eE~R*BE+$%UKV^=P{2PTd8s&&*`TSSr5kEu2bVa7BEZ;I&8$%<s_`d|RLd&Ul0MuPcN-^S?vzt#)_-axg(~n~CTef~bxt+P9xXVP#!x47r5f$D{>`*Hr{nt%_8_pb3BW?!{asC35-54Q9IM73%GBhOovi2QAO<OxGhCVBdNCctnU8|9SuyGuvTS24h6S1bpgTF~)f{IERD?tTaUH#1aOXWM%`IU!6*hYF%MD+i$?eLlW$Oc`an9KE|}ie=%d?U5uVthS7!67;;D(R*7f`9t}ltYn_)08h={Dcl}(PoVyUd9eu;9mh^D7=Ed0R833toI@!$+Us<*IA=vMtMJM?cqTGIt{H***zJ)g6th(`d=<#*f{j8STd+#J{Jzh=<I`@;tMag(Md^+8c_fD|tQz<vUD+=n4%mlf{^K9|6OlT-L!MOF>5Yc<U(RrFbYWk|NkGGHF{!=k<LNyjU6BT(7wk3{bcOCV+Qy^Zl8e5wraH+5w4&4`mxbhuP5t#->9&zl4*e@_Nb9FEr{Eri8EkuQtJ`iX+0lZTkp<;?Mb#rln{k}GQ{;BN<D`koHg>(2ivza6XDS^{S6Z2abgO_KU*UnUGt7}duVM#{|aNV_BIAqCCQ)wG$#~mQc*R_L%@eSy{;J{ZXw8LzH7Cpb$AFi)_fcbv^a`@3tEE=r&T>-D)sIh=|^=-h7m0#hIPaW4_qs|M@(ZdGsC@d3>Wz#f{;l7C*1<#&{@mZCk_<3&yw8_SR@{2@Aky>4-ABqHn$lpw8#wqTe>~}Ovyp9uSK7M)gmYD{Y!;e3g@qx|&Gud{K3<X|d-lm@gaZ85bTU;{e??ZI`><7oj&V=6=j$_*FcgSsh$J*~G^G_qE@*TF)a6iJHfBGPZ&;R1a<%kB-S867-p!Fu4X>($ujb35hd{Jt)&y?DJFh*iI8Gmh*r|H|RX{2*F{UR3$s;&}r`($fA$UB&-TquQEGe4o<%|aIQWfY9*toZjotDr}F4ZP7-0F#8N#OI|rzyD=B=ry{Kr0Xe|^+uY>ncX3M)#0%D!~vMGZw5PF63#qX9d=&zKo?zb<Oh~;Yg`|r>n<t$SuG59ER?@-T87U*p+@a)ne+EI1oLa31d*)GFQMzeMd<FUfdRu{>OA)@1`A8mpLuKOga0P-x7$O><w#Z9w5o_sc%2P;=U3AUcL|-n--kC-wWhL1*VA^L&$wK8JfARV%G=L$rCqw?_#WRZUb!+FjkYeQQVQBsczh7_#ri<YVln>Q_a*e|)o}Qp86yZC&L^)kbHK^wGB_ITqe}+H)40A55VzwAUJ2Yr6`NO4!9GKrVc8Ew=iZW&Mico%ajzg`<#c*;$zG`M)8~`7mgDIf8@wvjz$_wX&@Io5`CaD|u}HBB`hI$_fCo1*Y|2~QGk-Q;eNl&}T?8$c%h5h(1^$GZ32zo~7Eb@r;^*(G!nITC$;NlFsFpi|mHu^Xe&|@b+G!k(3Vp;DbYF+^jgI(ICl^d=#OSYAo^Z1LBWF9N6k^M?Y0q?3+Uau(+#9FUUwV__>##Ic{!|Y)P9A2uy1~4UkTSozaslnl58`**IP#v~KeMFHccA;PFFn2c1hyUThnMdc@-x0vfy>=UdQ)1OulV_v9PnL5KbKFXfr<xczSS()Dj7v5^>(5EiWyL<7Dd&rjNtkizAWU!c|l`V4y;k{;+)0C({F)5w3WY-i_<jNf4SRW?JjZHB+*G$>>Olns7D){GcaQ23%1er1C}^aE_~umNU0lPPqcIK{E5G8M!y)#4Ddo*wLG-%)#k%o<@jRJaxh(?%ugTI=9i6$V=Kg6>8|@4^hol5p!4YusVSe!kNKyAx$Smz$2t$bAmTE2U(1jB6}r=mAFgz*^)K)<C6pf!<qO4D(gQe(PFZ;zXGFR4>|zIsj*LL{9>QBJ-N~n9M{#<UJ87JKG!^M?BQ^t@L8MBLm;WM6@6OVsF001#xz0k6^^ZqKC)&_D-546UTa?BQAEX|ViM-rZJvzTci+<EnM)UdmS*F_@us(d0f$dQi5O2diotO^oEo;H!ia6}qSS{F-|3;vCJs6j+B{<w^OS+;CV@Ako90<r`(rahI@$<S6mmAB@f0&O+L9Td1CJ&D_+F;gCO%lJo0UFC{VWIymQ121vXB_c{QE4SGS1XNk&?SP|Uds{46o!v-AX_#cmrl4$9w`OE*O{Sk?A<Uc+G2!F!3R;VSdo9?TSA6YlF{{vDcl<KO)z=MN<8_!4re3^_zMe_X@JTq-u$&MzxKKoTI}$lKmGFQ$fJ7L$~0lw!*Xn&{s*{smQ?hQ8wy!DWAx26v@Q7}sFs@3mf3UY_{;s^*F6ut-BhW%y$top3rEZCci4se5w7mQHJBWl#vTlg;Wz5bLc{q9p!TR(Aiwb&sGc{*t36BMPr)swKRz9<spK$0Zxq@1A`FT5F`WLb1gm6Zpz1>`bPdemYo@)0A2E^8a`txJ_o?PEjxPlkybiBUeoo$RjsdkC3oy+nW?45m5-3t2XzV^GXbBn%&owLYS5r1FO<aQa56UpLvK0{O6vfhqa$!EG;QIC=)E&A4_BMr_$TbzHNyx;&;9vOo!zOGym;mVmR{ZzFGQ88w7~;J*3#zaNB>EZztq;Yhc5W1g?;1saisnGvEMvaqA;qCYaXL=a2?p0I(8w1$aJWT-Hk_8=#VebjXzx+*-lvF`A445Pb1Km(dJ_ac$Yc|4#gViHkH~Xv9_EJc2kqjCRB!!$7$d8SeJ6i_T1X;HNxOlpwFYJ5@?h(ZvGmjMOSop`iJXWi&2sz?_5Mo$Wvvi6=RLr-S43gAkrxv>r-aRxXJDtkJ{+GJ%gk(UlWy+;JU_S$<K!3eYU77Ei9t71KR<}c++TPkX#!n^i5S$_iaRwra5!=l#uh)t_q&?mbBGGxbJ-T3#KfSy`3UZnbwjoNx8SB+E!p%?gIYh0B<**TQ0n$PXmND{>mOUmufnacHY@^*UD8Q;j0FjJZHWc#w{V@_0jw7^;G+xz`gT_Y`FsJ>9{-Ysi5TFBT?#pHP7D+EQn1Q?6usXfjBc~Op~M-=mUgRxk{}wi%rC&YcxRww3d$<KgWv#PRJ;3=oz>sZCM>Ij1G);>AajqHucAaTw2@8QgSaQN^dJ6~3+~LO=sEr%NGRt)pyLRB{k;_wuHT0*D-W~x|9V-!pE92^O_ljQI*NmDJK$-n9RJw=y4|o@Jm57=SR<YdVNLhZq+S92eg<NO`U;$8^c<#U=HSK+$MKPlIhnP+1?>MWfT{_0g0$l<B;IZkyz5&lICw~$G&C&2X_J)%?oL+Fy0@BDZhX#)w)?Z%1r2Dp)0Qn(8H-MecDQpH#fm)^+<}g0{AJ*XvZ+_`SLb;6BDn;rW@{1ivMQWt7!KP*0`RGM7^YPlIl2ot2-mA%nyeA}n@nJI=ouEUScdbzF_l{MPsRMz%kh!vArLBD0lB*_lA6~oaNzt>NSix^PaT`!!Ny4V+IAK~doMzgzBzla&YX4dZ;A9)fAZ6JF%G{d$3I*MD^sjRiCL*A6>R`MD&K)R%k!H?H{#557Pb1U4F`I^;(*Qq6qY>=S1-Op`(0&@iE=W$_RX=VY?Q;D*))+A3^n7}Q#-&@s{#K8bYf%CI|twQIizEuWL-q&Z?a+L1n`{t2{z9O!JhD$*wpKdvMqz`n|(byBmb3b6KMR8Vl$7%>g(dTvCKnM<}weNqS15qjZ!Kk4U{w}WoRCh`lZY=WNH*LCZvcmJkQyeW)vY&QHVrQQidqK-aqfU>#lXz-sgPx=X2INXn9JMQJUrsDPQ?9z~uljUn!3m`i?4cWno5f8_f-fggMd`WN4=pZn>(C{EC(UHy1NYO3q=IpCsM$?h5{_Ev3(?J19z|py}^1Y?v;@oCwke^Ns$nt4o($KfR1|{G2M4QF#u2Ng<q?rGvD5Rumi(ybp;^)6vZ;5AJN&$5#?-=unD39<`{&JF7FvKDHO7m%o6dCO)*EWC}LZk<{a7(m3vcpAVR@56*nVmhEnIbMZaA9_$U<%uU(MPY-bAw*re_Vl2nIFA3x)opMb9EgOCIi}tDTlYlB`Qaql8jq5z9V}&e6*c##SqoQolu{*RRibLNReWeZoHE5q$j~(0J;-J$s_N<fw8jX$dHZIHM7<y|#M!hlE?oL8Qt})wo`W);O*TS0_OTjm9Dhf+Ir~MAM;aRpSUU}dG*}o!iW|WqNm(d5<qScQXf*Cl?+L)$F1d}J-i@`Cj02+8vq^VB`yB+_+?|XT0qihq7UND2*ol#&~W=&>Zd`Au(cu7VJ3&^6hbJ)0uVylTAMn0`2HoAQz{hK?!9Z9hmpC1BoACk!x)fhYxeiIe!J)!-66ge{@44cMZ)A2}uJW?M@x9v=)w=HTQep~_%F0Lhqt{@C&*-=h?sfC4WI9|SZ9L+CUqj&29_!-*@+cL7TX67z@JO3pwO{|2ruDOLn>H&-X9yv#b+Zp7xTtg4vKzOom8krc~gM8Z#!?_d=`~A8(o_nLu$Zl{)k(d3*=z8EXAq)8WQ^?}=(<s<_cpCFW^$w<N=3}mGS7p|(?7+UK>G*SoG^1;N0A_FXfcyW+FjCz~uth|g6*E%<fySj|t%M~QY7X#}&-|t?yVRgDIRq}=I*<2f406)52(H#qhADq`fJ1W}<mUe1nzRIQR;$#~v%dP^(cy#((zW5-(+DC5@!+v03pRV#LB>Z_lwRdQhRl+&b+I^J&DCW>vo6A&!V4Ua6SqM;#2Yenc9K0lSs4GH5Zj>Ji|LE}c*~dYfX5uhxzbbFGixbz&;8ArSg;U(q8xir&>H6~c!n)=5hJsfF;2Wx)cE}!U4FC^9{+yO@d<;1k`T<~N`UF2epv6l3u4x<VLzWtrLOWk)M>jzqMNRO$B6{|&GjeqVt&w*I<Kko=qy$sWD|R*b~=bpe}tDj?JYVN?dAORP>08B2XTe*FXEEZMwf+;a;ETFf!Vqa&0NRv@z4W~yT=H5Ic$J~=jBnb=OgXTmxMj1t#F;THXJ`PIfpHuEY?=L1G(x~Ah_9s#=MTFr!MaR)rxQ;Jh{F;*S3<buvWUFVGjFd(iK+?h%n9vq*&uKD(p@9FGNT)jFA?f!>B*|3y#I}K=oh`B4-oRr*Mm?iz9B|;)Mxc-+~&1g9u+K7(ZUfBrQmUhp&5Su!$~n#u4D>=~PU!t|OWOd9<lz3p=sHf!A#C6Iw4DfR#rh-K}>IxAk#pt*$qoF|Q-*^uzI!@=aXy${aFGLP*h9Y24g@l<5CFLrwUD&~o`pbdq0+f2k<WoFM~V7k|OF3nds_HlO;Y)KV+!Ec{s6LKk+jJgw3@aBtfk(7V2f%1<@Nw@fotoYq1r%U_XM-J{463E*xXsV9bkCz$3_kx;HXkE8ZRn7OQfhvpq!4%JJR<CcR}=#XH>PN&D%XZ#mo%ezIWbY~I!bL~%5+W7%EUpY+Op|8NdOPGnx@<sOHK?W8JF>0$M*<pzZ%vIUSsJd6fb%_Ax=d(MIzQ%-c`^&>EJ`+It$BERdPrRjj`7r996RZ%ELbFa6xZ(O6@^1q~*7su2>H9dPw35?WzK=X~R)f?RN^Gx91-^GWOk$1Auz#B}`|IE~?uBnbq=%k@PfkBjTht#O#m{0dYKmi1j2|rO=s>Y^d~~au40H5E6xyDSMw3<Fd5c!R0&}YyAU|&&T3mA{<F=PADl3f{b1fYvDq)n@^?d^k4-{r1zWBi^MK@69+XPuzufbkh5~kT*1uya(*pLqVXeGuf-nfQR%ce5I-g>OQYAE}-8)>%je&*8tCCuRSOVD_ynYWt%E{+E-Wft*XK!uMFTQc^M6Vu_uC`kmszL#V0q(PIh{7)WK14>Y9%K(VfcY#kkr6$|M!2G-wdG$yG4h`tg-lz8P{0JXiGG+u(TlZOLC@jJnAz74s^o%2slum1=MMLH@gc}pHXjA29JYevFYM*bz)HQZceC-t&hh)>Z--pq(*A$M2HRDP~7U#-e#dpjyaK00cyNgz{MGKcu^>7>Tn)Vi|=Jk-fUga1%Vhats|AP77JmS`^j8&^olHDS1U_MX{D`OtPbYV|kp#FX8KV=83ub*_E<vF0qS%JH?0x(s53)|BD7c>uBu)oJ9{oNshmv(q1(_2&mjSeXgl@kHKCbCh3R|WmM&+@vC3uD$6oyEc86u6Bd%#t(Ln1$yxm<I!G(8nKz!xJ&g_Lu%l;Q4kuuD=R#H|0Fr^pxhLlyKr6WmCR#F1fg97k+bz#iKom@T!AL?ziSpF{3r)KZ$U3yb%QITy^Z)=Z<6BUgCWIBrN~*io~g<L%x{-3YB~$?KkZy4Y$N2Yh=Mbrv{8e2k<ey54RF>>72m`_~Q5!H)JcJiF+H`_Y2?(zd>U1IuH)4^0TbR2m09PDUh58$QR8+vQ3U7CXj$XHXcCxMsrSAaUAAtw1yxBYr69OYfQYu$GWvn&N!PQOnX<y6VDFfJ<hXXKL1l?_C6AauI^U&-K@_hedaJGRF-Ky`yLaXzaW2fO&MiA*U27q!HG<9CRVN)=AHeAjx95oo%Y4>?_&V&+m}FtejPxcPX(Y<$i-Hf=eVRx0{UieM7sxs`O;DjcvXxH{xiW35xtxzlWux#LpU1IXpVE~ap-k*fw{hlV31w~68DQqcwaA?3>*h_X+ga2orvkuZM;p_-QnWNCY-&nog<TCk8XO3F!XJbSq!&g?V7zPFJVhMbZ^5hg=SptE6JMPUkCqa1R36G#0nK(MCqXwAZGOjQ`1)CyUawMoZc2}xF1TFM}*^3`y*uPj5=&zn@#c*g7GUBQ6-zFoCljiV6?@a9MU-nasPtoy%+azyV4*G&bEWgR^2%FfhK;CILNtjqT1rRt1lgQt0saXt59~|0eq1sh`E+ei2R{2-i$30c#(S^Z+Jw*$&ya`p(O#s6nn`ajRu`v`Sh1{5R5daF>l{E<GhGHbgx4!l}K)d5LFEv#=S6F{GAT#okNcL99V8C2%BfE;mG)hQ|?bQ_~{T%k3E{oK<zcS^yD7C@0i7`za43DP$H1(RT#0gg;98uCZp%Nak}D89_+ITL<{pCY_*=j2*q@Aw#Lk57wk&H&u%RCwj}W+93EqbhBagsR-x^q8r&0JN`t*CvE$o)fW8u1^(ULx<0}QTOwN%D<HNK^+y^t(#ei65DwxSET0F7kC=EE;fm2j|(EQ}MGUi1N9-Fri)y{mS@vgG4%5W!-vy5P{Z8jcWzZWMU3-$<@v8938q()PMN$BK<LgPx}BKIFPw>t|WnhoToV*s`HHNpK^XX)=TaVFU2H2$=o3(AXSV0l(2Oe+M^xIM<A{4xWsg@4Jx<EQYX<3cd7+ef~vTZJo4XE9;&4G`-n$hsMcFwO3lLB{DNxl{HC_vK6}wp-hRB^r9{p`!z!zC#OJH=jcX<Es#rc#}MGFhi~ON$>8E!?e$x7~N5cWl@cwzr=_Y{?UQso6KpEhCEglI$~H}Auq4c4OG}7l6K)JO}UT>hVJ1QkVA<A&jYjtPN4rB7dp46n|>0}19|C2p7`@j+&Di2cXzvEXRJ09il0KgKMr7!*9Tw5SD|aR7w=qf2&pgI$N6J?8J6T!mDwMz#^6)Z7=Czww0f<@?S@%kFl8ak@(d%+68j;wMSz*PX+Qdh7~<f{5exZQZy@!+8S;7Kbf#ls(&PU)V){)1#yXBLZ|mJLUBr=@DqjoY=Z0{>PDR+s@IlzWYPxLPge_Y46%{TOV|#!oJD8)u%=VXIcfFLrEAu(Dt*D<QUTZ|TTbJ>?;TP=xosNys<7nCP9=J2U;_g#Q?ET~0$j<$))b;*Mc)qHa=C9`?Q}ZIQ-0U_{&yOLwMHxKF8a^88cNF=JJi%(kf25dC8`*ddn9=@>R$Y3GJ!7^sbbc46n*HWHmAFVgXE$Jx+HBBl0CN6WG%N@f2j#~t=wvcVx{7UCMMDk9V|G!y6>-?|T%0q{vk(FVdqDPKBFb6qhX4mZI#v1>HT6rxe<_NLR`N=`@!X18Ycv~e)`Y{!Gk<ZZs0D=BOfnEP!q~P(vQABid3vTAM@n{q?ehkRQd)rvAFQKCmb}4$wh}xsfjraf0?w1fe$G;kD6T(p1CDu{(yqLJ80#v@E|3n!_%vmlaqtILg)1^U?1foL!{>NrYaGtDXrcRi^_bah+i}`t?@vmt0m~T+cpKzQ$;)+j(QinCxK|6q+w$+c|E&AzsvxAhj*Wx5KR+hqmC?a>rQD3t#ppNr?zD9zuYN`VwOcHK6N*B3SH&H^?>9lurJ`)`Yb987W)sdMXE;^!vU&Ua9C1uzCwZHe2VZ#+P!}uCehdkNv$ulrZ_XN6yr7-h<^%#`+)7Q~>;bhJ9t=rXbDw8u(M?w4&@x^Hi;wexqi#3UhZ;a!j61en26!N;Olq4QiO=XIyt3*PKKav4Ds`XYk(Ryi--5Yx*h-SU)I9<lmpIV0#07BG^%6E7mV?F$4>WyWhH48|qtER{IFK1jYahr#a>!IhK=(I@SQ9p3)PbwodJ`<|mB_J^U#P>*LgW_CARDT<c&XHgX&H3GL(30he?%ILx}Bn_H^rd(;ZrVm*;@SfJ{E~uG5$53O~kf{uq9Q5cVvAnXs6%9Bi2_@FS`@WmngD79^~VnEkK8x5<$)7I#leq3(|5Az+!(lK2ZM(WUw0w_2t<4#=mHO8G#i4r=Yl5j8WWJ1o}oX;2l(o5=HG`xtM|G!*VpR_%ap#J{=|2HG|-Hh7DMiz!g8LM!&Yb1HUKh8Ofr@922>#G&%Y{CVGj0Rq-*#&$|hm=TofSbP0UwHj)9=>ojmM6TXhAFt)d9@ZoO>XkTu`Om$F)BjF!Ft8pjnzT%6~NeayO({JI##cANMkB5h{B+$@23Def!C5mHVFt@A^t+kZdi|56e=1t3Z3N9DXQz#36TD;|~arDPW3HPY5W*ZrIP{uuC9Mm$|4x7vqc<FPev)%W+NU#kbt@4PcCt57P^5GObGboP22ehH<WhqH~B#vD7PGS}jMOJ7<(>CWUT6fHZY??hAZ%5B20vq<?Co>gJp@$q<GIj@Un0&^-iv_giUo!9Y<eELdHv(_ke}F&tuS1B{1ZS?_e>l@T3s)~~#0&L`@J*zQBSca;ubhuSyxldN9cw@}MGB}*a}7^tJC7`|6G7FUHFS5MB9#@Hik}tRNbTQtBK7G8nw9Hd?1CAXyGx7>2$x2Iaw&K{r5>gSRl}dE1dxai0(PDPX7AZRoEcf>afvkQt1iZi8n<Ar0w41%))8MteSmT4-=tnU9R-&@gmGaBMsrvnMh@L2=9{Id-ZCvHjuwNJ-xor6=pL{;2|Sm-Pw;kGHK{r23MzvKF+6XG^C3YA%$jB)x9lQOeD;LCwtm4GJ#!aXog9wajQ8YBiWoS_+R~#_jA@#?DQsBk4C_w_GR<qIvxYtD%s$U(FxXZ^KDSEoUUWRc;7jdzPSuFb^bH~9N0+h&{$bddrpQcp&g8W@QT#LGBd-1S5qFoB<DB*?{FS+r#IDw0e?1Mu{2eRdy|5S_e3wq*Ljqv$ns72GXv@_3{pPH6j)NU9hb)RGYRT=JDR9)`5IXBbkWcxRyz@W)V#`Y{tm&#D-Z#aVg0MBD#8HFftcgS-rw;c*E#S)oFF17~n>Z|4j0Jp0kv485SH=~wS}z(mi`wCf7a<U{p&VAeE`bv6DKz*INlvbep>s`5n6T4(u-w(3xF#*4>t&kZy`unb_Yh;^Uxq<wK`%zGmxAbuhak9rJ&oNKh2N9qP|?`~b)GE2*>C58m%g#Zn1L|3UlC@2Z#C$gy+*v$VnOVJ8;a#w^Zs$Fsra3B7JN+wko#sGinXsr|F;x349>&CkEt|whY0j^?c{8X<zcjj0#?UH&^eX4;Ig}ir(pL2JGDHhn^8M`msdlqqs!spg+?%m?83)wj>O}H1f%xel)k#P5Pj#^(a`w+$aa%BnEk06vMb)h{Ik87a&$9X3;zL@d!#UQVh;AWR$GYGZbfT0lOC~tMYjdzkoT)Lpq*Gc?47v;I@En(nTs#Hi&+6p+VfB#1L^XiDy*Cm!Q($Sm$|-Aid^91XJYbJ&`sRS=%EyigN~<ok5y}NL)=x!m(oU$b%C5;t7VzmC;ZI5<|Sa8zZC9%NTsh1X|X4oZ)1+lA`GzM#}L_Y+@dy>sWl6Mt8LZv#y^G_33;;JYj5)&>m{P1d=0FMUx3)e$1=GEybVFi@K-_@njiIr6{-tBOTrp2Pj8~ve8N#TjE@<8I0S!RnDOFs)3Lu<gLis76AtHIM`JS;Zb?8DG0s~CyGAbJRqO5WN->vg%4)?MjRl~xuM1=c9+BPV>9{eV7I%2&0<EpYt(n0na^XKnn_o|Aof+^wZO9xvYrqKD8&WruG+Ji78bV&JrPCgi(BKbq!15~sWGTSj!lFrU<r4L3OU$WIVjlgrfH>dpq`@Q$g6-l#MJpS9$|+QpJRt_(RJaH4<)h{`dz=&1fSIyfv<lT@u0N{Ai0u)0d3--)IVSLg<M&|re0x-gIgN|la<O0FCKj(_;bw>cyS`3>T`qbBvoi-#^z2<!d^d%eHuMHdl=NZ8krC>`Sp#miAs90E0`F{DDU6q>py>u-Na(yp1~>KLx018ezTX+IogN0)(O%MIJ%xE+KES!q*^Jps^x$Pj0PUKu!rgn4iv@!^*m1HL&f081nO148ymKWvR(u&GMZ@6SBp*g6>oU>?edsdc4^BpZA>;f#d^o%nKN((ta<ql;Ti=PszK!4+R0W4`yhTNsBh>De8u)*CLk60o7`4(&_-hc1#WtEG=FBtT3M}Jnl3K-HQn5jWhAym|*h=%v^Qh(ICSt$M!xhJ`kzeN>;l;bJIODue*@)H`Ja#~b?b!VrZ(gk>w1b1!bb~NswiP^C<U?<jxZr<%`$^~bGpK&^JkBfqLj77l^W35Y@IWu}T6CU)vv4MeZuJ2N?F_Kws9=@FS-f++8rke_`okiZ{5*7zC^qzy_gegHbhaQl`$-PN)=7b@UK1TMlgHV->qO&|8}@CDK&iev^z%<eSTWE6u`;*G3B6BXGWv@1Y|0CG=W9oPW!B+v`ZQ9b^?~Z{e!+R5tj8=W)#dD~e~9TiB5eJ&b+GYmG;#6bC(EDqfPJGRJdTfo(f8h<6vxNhZHdO?7m_$t-OFi#-vbnue?S}le71P~y9sZ;NTLZHM?kjY8gJ1aRkmZAHZRZ38$W!UieIO?AbqO<?jL56V|NX4yyy}ZiihCeULTCVI-6NAw})D@liBGRh^PAxL*lnH7Vh&yKssY8^L|?^8t!>TQQsYG-ekiEH(eB}$|Zs;_|TOR!*i+07*v`7FN8&~*uj@<*b)gL<7a5ySt*>gPnx|USOBl|9;1u@KV1J~J!yM4i&O-(5wDi#_|f$+aM|<pVuuaw^?eBT1<LIGgp0g9^)AqpNCe&Y+TeM@gDSe&ApiUTdgQq%E_aE9C5w72^!D6<)+?XM6(=S3Y*#tN|Kl*6zU3C{8DW%^ThG3`8H%fQx@lXAI9+wi4kf)D*Z>V_=HAi0_+Zf;<Z0KTYv2U+aqYxk1)X@KB8I8fVjyM1Otxy~MNBw2KsI`d;*@(WRJ80pd=GKtaNe%Pg0mE-%v^?J>08l+Pl0*w70RjGIg^%(RY7X<UGi4m7PUN1kZ;p<;HPCYI6W^Rhvs>r5}AR0!yWKY;2!LJEKj$n`k}3fF5VTDV-yDRaA);9Jp4nRoKw&zdsel;pBqQvxJ@m@NME3jyf;B^b`b7+JH&Bs^vARQ1Twl7Fyd}!;FRV;ViS@Dzl3$!)}~qzv)vCB)z8pxL>lf6E@XN?4B?|)^O^8M7OclElHZ(XXrU&^&i^Hf59T!C+wC>5v2r<E>Z8gQE}PD9eO7XgxMX7GvkY9PugLl}e8VWdTI6%z!I-|ejKc-LFtn<MK5x&Y;+N$x;GiULtl0x{4_)E;l=soa2K#X8v{NK+KR=^EF2SA7I9k{?1^%-(hXW;<2n)4v>p^FfUt<q(VK+cW{|4v7O@b+1nb=b2Mk=4_!NjLRnsa{|ZmmCxH&-r#+P6-0MCc_*-Y??F?OTlV`;PItRvd)WJ=2+>*mo#BYl7&SX)*V;w5g(L5b8$6l8K!I;P*HLcA6lqo!^Mwll_)mavOTB#>v=@Q*<C|I^$s8370e}zMsW~-pg0XK`~vJX=}u`D)rJwxyoqT^q#ku7DGaCDt;{JLo0(akT`MxZr$SIP5&gC>m<MmG+VP<OTXc3<J*vED8SwfY{31ef-#TLXD-#9hv~8W?8U)ZjP>hISov0d@xzq@Y_jw^)^TMhdT*|#e8&dh=dmKJjj2SpM~fJb&#H9k`b!w8{}6*U$MN0iVXT~;0lP$_N#1NV!X|$~s!|NQn6uPihbvmH35DeCw`fA43C=nmPi%NnC~J2JrsjA;!!}nqQXzqZQ{M5kgS|Q9K{jBw)d=S1N-z(n3qo9{1l8t7!;8aDXdg<jf{tgwqof4(+BK3f%MT=RBn0#-(x~MoKBi!K3-8`bUl{n0hXU^Q*lCe~n@a+4p>_wjyCz|j+AsWLX3i+h<wNzW3b^RtYxH6O1?^jij=&7oQ|dFZl*z`v2qD~NTTipZC79{^B$=qYb74;NA;_BY1q{Bjyw$cn=op-U;RB<1dtw-4BjuTVVZ_G+U3jha6HtwQPK$jhRt;=|l6nT)!fxQhxnFSqKmqAEq=o$AtvFSmk7+$PfTp^Can)!Zw4JhNzx_(Z2E8xzMd@#tDz%Wg_?sV|aKoT5KZsrKIGag}(`PKbD)E`ySI*(v;qb=u3kbb<O=JZ$nYn<l0)HXzV?VUJq{88BW%AOWkFEbcxj)7?!TdTwwxe8%?H^vvIkH)Txtq3@aicSs!*%sAV%LF+KBml%CKIOTryNUGq*4By0$5fg2|xO@nM?XXDD=7+gq3wUnr;+!rU@|$Z*F72uoT#t>j87>8xQwHlGK-Zn6Y1#_u8b_LdsqirE2!#xtCKA7cPSWvwb}N$EEoF<Wmw#!|Ay%1Kil3!sPvx1iF`94eFQYp{sH!lv);GM;*ZM!4dNBUm<#R`xD<4!pv)r6S!URJZ>uGqo!|j>Fqda!r#*l+@VRv$qyqp3=ad<{6w1Y6n8O=LFL2-SiRq!>{D=sD;ASH7dT8dzv#!x`ETfKgB7rSZx(%JTm&m7eYLV)4dZX616{ElO?{KOd*+p3fng0^;7wtNKi<O+dxK#1*Qd~M>>SF+SHQ%h<@E8it8lu!mP{Y2!NGw6QfwoD@<aU`Ugv+L^MpJG1uEbh=V4-hB?Ft?i{SYU1suO929dt1)L&Ye74ZK=FH{wh$kSWlo8o1jxYGk_a_}E5JEg{YB#c=c|LTrAJPN7rzep0>{mdf2W|+wL#8U-xN%*&T8rnX6iYjwdQKl*wX4##`myL?NgQxS!S2GoA`Ar^ekCxFdT6)Zoco8J8ip7{HDbBrPaa1L_hhAS0NqgqMfq5?u!k2RlSZn@<2rUI@Un78HjdD!?$@Q@Jkve@i#hSUcx*qOHTC=r09j0lUFvC$6fV)#W(cow*?wBUP+Rk{4a}HF30OvhPEmvdL^xuMKGM7lBKq>ORT*1Vl7g+x2CnlsDv0?Qe;n~GR98=W8g$GVS#U-u(-+RS{HoIZYg$PQMBH)Cn63r@Jfa#6LcunRAbJC$~-R@q9JyStN+$=HS$!}s6C59`cL#fS?S)_B-KA7&BiXJ3|ev|*fi<)3S`E$;sFQn3`OT}4hzXq&3?t|Sww8-!~KD^kv0>8U_gTSap*jf^WCN0-s^A9D=KN}40d!{m@NpkpICkP!YyzzYeX_$3Km}D<=WcVMuz_3z327C;K&nx$Ef-`19go-qX|1oA>baY{*dLlgYs{xY(8t`<c5^H?`an7!<IN`aIer&M<k9s3)?fFr*HY^tktJVWIxdiXK_Yu{nzp*l40Cj#@qFL2Jc(wf*J{q0LF7m7;A9vT$rKt{B;lGl1H1ZNQ-(SET4v|FT-+{ot+l#huy$bK1ZU%$uXtdF|MbuIhpiI%2#!HC9fME;~74Ie86B@k#u8q-@tOc<26Ca%NP$%+&6A&|S6^`({LGFSQ#3ebP<_S;WutpH-whKvNJBaN11NXM3;DxSe=n_7L`tcP!L#Jo(!aEe@{paD11u3w3|2Bw<o3?0T&j(c57zp&_e6~J?gKkZNkP~_fq#C;6sjdL?{na;outc1(JC_0rKbN3rbpet?8tlWN8LUf1D}E|&1DTK8S%U(5EW0ulcV^y){f6cE^zl`6THS}!FMLC>+1Yq%pd3tBOR@@sitKZ(`$&fuVNqxhY~Z^A(o<ZiYkE8>@CgOh%?)DT-ocbPzA#+c#hWVgjW(X$4hKT3Eyjk+$lWu`c!U4#MW2&$#DBC5M@Hj#8f%aW@7qrcjRHx3*c$jZC=O<+&#>o=4XNZj22E{ol)U~A9pgf%Nbq%FOk>K%YsbK5!vF}pUW^cBLCj~phP6Qns3{?dhi`>IYD@zzJEs9-qqj+5QVp-_wh5g1mq3(SC;3xGlV??!2s_onK|nJQJHI8s88t5yPHiPy)K=3c$Hy=?wg`s`LXg*xPlg*3P|-6KHI!Pw($E%ew{OOm-&vgQCQSXjXR(%Xj;w-W8mtd@!6jz?SRi_YT$FMq!Ta*@YuXWvmYkfi2cl@*6)kM|wuP)zm$vYHyB;hf-jnN77t<?wNt~;n#yAV&8&F=*7Hh+=K=1l1uyt=Ub=^IH^%mKvss0211Z84MXF8sgSOwQ)-%ZZ{Q;^!=ihIIq&^=Fvy{T@6H-3rZo;*J2;Wr02k$#YwT7kwhUQq7eO3E7Q;h4Jwt0s{S*VbMqo(GB`@bDpU7HNRqhg>wj77G7TOtCn18cZKAh82#pn6l5Ov45v7V-zCGOz^A2!^N{$RF`LqbahxJ5!j|ue>hX+gGZ0~uoL#Z@Z6x3n)#LEB@r?9eq9O1bEmNWJEU0kFVztA<q!7mh`_~pGjOr15S9}^P~FSpEcv?*Po1Q&dc|_;5U7Twp6#5iQTJdx>^qz+l0%=NIv5#y4H*~iphQz7<rb<_1NX(4!=8YnlFl$Mbv^1>#Lx!{QFuSSktm!mgSm6ku<P?*s{K(O-pvR=E~(%R9;bM_@FH#7t4u=0mT?xHFrYi43waISHb9b7F8RG%2WOOoV&eH+_{(ny&d=vjeob3wDOZ{F#f9u~vp193914XYnWVy`6DsEGGv^a0Fg#h3dG&lHBWtFQ1D+{pwp*G_eEbIGA{o|c%4wK8Zo;}KS)kJ2ejpD7poCw8&7B=Xr+5-J=~ExBdo_gDFYf}{`vt8YjH7sV0@iCsV?~n+ME+7Eg&(4MjZO}@``293{#zA}c1VC%?`#M?Hiv4y=a6UT-7)Bh7Y0qyr5gJN2@Gw=@xOx5B9{ze{<@er=0}S5@5cAuicIhWZInxIf#X8b^nlSZ)T#2LA3dJprlqs-Qf$42;*NQc#JkQBFj{DF$KR6L?wUa+wEU4T&;zW*G+^6XdAwG(20}ztAnBYCQ<mlj>%!6@ulxf>p4$X*7s5E%EmqhU{RPH8eu05KbJ=6Fg_!(q1x6>vnQW7Li#H5bu}6}WQEyy;?NNFKB5V6F-#iIFN82#7>rF^;xCHz2dJ_Ws8%2Ivu^B&N@$(^N=H&bN>_$-o*0M+i`xKMVM{5;*{j!FHHv3|*m=TOt`O`EbX?S$}4*301X4L;Ug6~i={0K-P+y+4m{3ru=pC_RH-rE-E1jEVp+T&#TSPXsNbse0PyV1DjD;+tU4bO)J@WP%Vj5puTmP=fQD3Sg2ns_{HGkVQ6c)1LedqSyauoAPmvWr}hb7XgB?}V5Qh2Whd$3}JEqesQ8nL>Xq9B+t+jU&nsz$^iyoUiC-5e`9;4b-?w4lhdSF)C{HVD#FUz4_`r5J?$E<d<&QYW`Y$x%&}bcq`Aw3H?H~bp}jvk1Wd$ZDr;4-@@mx6jJ)@H%6_@$GB!!);;MCz7IQrH_TIk|8FEd*tmgRXYPX&ojLHxx)KOkiw-I~aCPVhw0=4T$&(N~(|4YVzUie2n{I%8{bj;Cc!>s$*8tzHOf)#&N8LmJ&?4_u;4_}i3pbkvf_Y1D*jgWyOzSun@w@O@^9?F9M|*Lju`SQic0FYH*1|5?Xb|3*L6Z2Vp^9)N&YOOi+#UJFJ+@{sN}ZfV3_{<~-`p>}GBkkNt~d+~?&a-Xl|VNcwvt`v=0VjSWeYwpz|h!y^wp5S%i&*$nNB`PRSx00?&omST^g3w^iiX_J#^}&Z>TCBgS9K~;5btR#kV;)_n-l4B_{CPN<<;7e}r^|c_FM7XUAmMBU;sB;c96(B)J^}Y@<;7>}Psx#!eW~>jjrAVK(86E~K0{W1iP^E$&n1kP+VkOz`A#R{mInnJq&Q`96h~XNs}w_MeAOw?gUJokHjzQ$bT&$8r8;Kk{taP5SXf1lj%eGd}Zg!(0E|<j|S(k;>d9n=75b;f^{^;UuD1R0x%<-T{vv34p7IA;;<Pe^}tGj3FmKg0=i2IC4muZ5>S{2UC+VQTRM<c{Udse319-LNR#Oap{+alW?cwB^mcofZ<mY$nW|boRqq-@IoXg7m7k6KITkJ7!XaFR{S+!2>xCPNG9ucAtM1wlfp2%@hNP4XF$3S8KRc6KJNcej-J0+N=_K#>*6`Y>Fh7!ekg^PP6Em5LJg{xbO`MG<4{+BIxRG<f&+85L2Cuz#+}{hBUnzq%o#<^<xLQunFa!`I;eU;nyF1!$Nak%$dw&N4Oc_<=&us=x_kv6$*C~oRsFQenU6ij3xdkz9;|!aiRDrZxNuIh>E4a#1g#w1{R}giuyFoX7o3?m0|M3mVeGCcOl^KHY;aGe0;%S-utu0M4v2-e&dXq3XAcr4$#CUg7S5aH0B1ym82PvjWI=Hh2A`geor2;_PFE4`2q{4+u`e9!xmQ8uaUJNqIshjxZ-MOk&8$&dC2WRPdYBtCS>JHfPSRz(6d!PM+*mL=(?PACOlKU1l+apRlh(B7ff22wPBW9?($puQX7>|a-%S8(D91Ew6!Mn({lK7qDQHl0izGa0!<PozQG4E2Tz9t@dyK<Dw5ptLOX@&z%hPzgED@8YX5pg7?O1z@A6`FuLu&S>;(;m|=G<ie7k+Yr>Sa~%ChRI?_O8O-!vbLab{h6)RFS!xLTQh~8BV3-9SFN!O=l?`ploC!G++JBi8$R%&Htr=fr<k7`1uoS*&KYMCBTLWI>3kbH*uj{6?`8Rg%etr;BU%Wp693#*_g|~r{&$8RgTv=qH;WJYT1N32l(;u`eH~T)(B$1Nup&2%o8!76>FAY`}Qz2o|A|>Z@a=@rzaRcYa8f2=)tKbX}C#D0=}P(hifxuvUA7oLsGsk5uV<Uru}NvdBJ1YBFU0#k1jxziZj07`v|Y^6Tn%Iu0nk8ArR9Q!aC>qctFe^s{|%!Tw@Adb|;q336W!$Tz4cSzmSHuw33pd9Wdvt1_(sWU`+P2crBKT^<}TY%(IR72y@U(I}J{c%QGb$Nj!ZglKjqbLy6z9DDc68s)cDnQ#T77+YjVT>rX2F?F#9?oWb!iNVHJ?^OAUN%Y^Z?Q7}m91RH%7R!UJCgkq*bLzOlh__rBNB4(3qZ>F>Jo;$F+^uB_Otr<Jyoo3N?_9bL_9i~D@_haSWP3-w<5jG(5FOB9~$j)jHBiTu>(759r9$umW&L^Fr>%KRJ%L~wvV-mpoA;J0lt&!B`2{Zc+Oh>=?lc2v-ki##2n`Uj3CxXrr@af<si<Rj&Y4O`&dO*Dbq6Ge;V$E%|>b*ctR8ef)Il-y9lLPxj=YUf;#Z8vuctEcf_8zvw0}(UXh|p8?*UVrtAh8VUyf(l|p~>FL9!JT$!qBzl4GsLOkGGDc(}TC<C>wu|?wOlH0~)HqzcPWA#SEZYvorLh^TTX$9-4lxq{RX2a6Ddxogr`@I00@<Sa~2>wnmo?eKU`3Gqhy861~YBohUGeNgp@d#7cTjhjpA~Fwh>4oPts8n<mfJW}HAi_<>QD|6ud3?U;5W9i28QfqUgIy7!|Vy!(0=&j!?TWM{j8v*=Vfru-3GrrXg4BYLFZU@zVI)sWotFC;Rak$AsKludXYfGc8Rk)OW}b1bc>vt1Ziw6w$5#fr>;WiGC>{f-zO1sC4h!Io`R#HG#@(lUJU)73b#-@cBlz43sy#!rJ6Pq-Ynhf|pxl{*~&%PhI=`-sSOo5J5WHUK|FCcpbkn!Z(272(Zv>0=kNevt){loVvPIDO>lCnX?_?Ig=CNiuKW_TmyPNk;e50!I1xLMUCP$Qo*IXZK7y%Wksxiu&TKptoO!y~roR79D>F_kEP<HJ$-GKdBU(+?3ekyVdYVuN9{2#=_U1^0@xH4$D5yfu`_ZkT7bDRw}OKpqmVnvu~JZ#d*hhBa(s}WQ_>TE5RH`b+kRU6gE4|CCXW`cvNnXTz@`5UTryzgl`y>0?&el>m{_5zqnZB@^#*(HQLlDe1yZ*+yQY)i8#fm0xdo+CR?m_VdP~Y-sXvmFlP3zOg@%J=%QUzyi5W96fD9eEgMK$nkbqN1J3_rf+t>|g;(~qkhkFudDyFgmtHEsZ^f&;H`O_`k34~jnd)F!eGG!#-ht@pGJG|p%nUkjg@V6Z=t5>U`}6J?g07B5wdrHBJ>n%Om33f>&TO`7*KF{gb(cg>9YMEE=B#aG6W*C_%?gc_;dagh#0j{gik<`BH`@=}E9ZkVXN11-mj%-_Nh%o}gokRy!LLb}jD+ljwHYTs8^&<O<?B%KL5TJB|3#Gq=5pNMEGMnUhDczj5ZbG*MZ>ziB;#N*2yR>m9nYlE_oN`0l&qwy3#(vYu{NAE%Ecv_<uqesJm^*}gv|}cWWM!z<Q6NFz=%v}(VEQDC4YE-lE0AeT0ux)6XxHdL~7a~l$1V(=wB8P-t?E~aOUEZu5Mx<Ey@^g4CG~ee+(`+mEl{EDm!kj!n|rug^gd1!_V;!vTDB#YY+VF&7-f;+xG;NWL`v=`Hn8E(`Fj?I78L<DVUrs#x^aXxFte{-R;B2zBP)1Rj;3OhW}|`T8%3NM7jVm)Pmvm*+61*>7B!6P<+w@P6jqw++5p7W?HyYKYdfGROe2vTmL1u?HAFA5<W(0n=Xf^bRH#cKL*19f^t>K#OTjA!qaTVoahf6ooyDJv)%zv&*8`L{XV3-TpLzdM8e~@a`@0i2COCo!GGM0kx3WCey%<o%ZTDQ$V9@t@NC!@G(oC{uHk*LyYM<h1#*=6nYOVOxINA#S9DfTW5w-oTUrGY6OAy~#hmGz&83|On()QMT`<jT1fBUaL5L3Hl?7Mv8lNtEJ~W#I<o5vjJF%`)C$RcL2>i)1ha{I~jA?1blEw$Pwo08n?dgfxM-ri;xryv3^23p*dN}n}J5{+C3b!MZpu({THU0jQJ^hxDJbfQEP7mPO&0NT6p5KNx8%*)7w=@d=_5psc3KU-Tl&+ZA22Cn`u*Rc~{Hr>_E1O?U)r?-EccCq?`jjhuKnrFm+{EA7Ur?pkkZ5jMf}EKxpc!t7OL98E*d!S*<jO!(vpS8}*-Xv^s8P=yQ5^Qn1Kgx!j71KwX~D^4P@k;P;9Y)d<lYK`vBKQjqMjt*!W^dl`_0i&Q)kTQ3Nh<~-=f-P0p?Yz7xQF|Dp@+)ogMi586$y<E@jKuyIR*_wxTgR+FguRt5~2f_#x@kb=vqT1BFUXu<ed^tm)mSw1z9mgj^P2Lw?j@pr{4sVni*mUhhf07Nme-<R@B=4aBaZoIcu}j~P#6!NL?_O8-_cNO;BhX>5sC5)h1G9odtnOf7AEK}O{~rt|T$Il*RB!ZjaroAXfBrkZ31rO@Q?1elY30adxukUS>??$I^0O2G%0h6j+dYmS5Ewud-yPYrf|?8b51=e$D}+1OZ<4HplFLY%@uVpVt?1ROVV%6wPSc`|Y^kfTIzddkAR>)sHZ@P#&xUN8GHltXeqx-oG&qeL}g(h+T@GTQ$IaCS`cmlZpKgZbq=iGV!3HS!hA=}x@-)7fGmBZU+z-sY)qoWk~W*JAFa_b3$Bj-g&N*p-}8Xj3_ZfA^bD^0FK?nr{c?iBj0*m_V%$SCHKDJnrz?hny6(Fo>V+P5nn^!R@&<Fs)n*Ma_LV9r|(5%o~BMo^9A$$01jmbufCZ1Y6=Vm%h0kLPqC?<IP#dASM<EW@8>us=f=G9g}(6-@GLq8@)M~-WG5M%sfcHNIAMHB-7W1Rk-5zd%XK%oOo*-#j~q&$f4Kmq&>y~Z+>z?bE1JKYZ__Pzabnr^pvcaWX0I8URcz<3Ov<b(f^$81K*ja(EfKJ+MaL)yjldO6Kr@(DvsfNkNfn#=sl!<-|?M$7ZmMXj|Ox<O0mm9K1m7xE1%B_R!Ffq_nsqBnTllLb^KR%nfhiOLgP+`?UxdTA;&^E{zs7EJE?`bJNX&&hc|F!%S;lzyBe#SWy$I(E-1aqn9RI$24<YjKzSi8R;F?X$jlNYFPo)s$?y%FUhxW7AGu9A##$IYF$bPqFTkXES#&PH2Q23QkE^eF4?I3jC0n>iiLyV?m4*=bMU@=1<nc}_WFY68G_Wn_@ND!nMy~n;Pr<XF%BDA>hBkqH&I9D0vnpMwJVH*NlY-ub&A9loA1+q-M><-L<FwZUV7kAOmg>EPyj5{<>*GgwSNxnj4Zesc)dHdaZV(*%_7Laq$$;bjhiLx!7x4IO91(X5#w-5kxQC~6K|?@*F@JjwL!U(<9S{L`y({#ZQ7HD$Hn1?iwSvC3bAr7$vZ?L;V-WfG8Zj)}$uo0|!wgSNdg)jNaZp*n-n9vV9~<A`*hOpnqwxk3cFaO)&UegFy?~{i0d)D^2)usq6=p`;0l#!Lo`0~F6+IUV+w_m%sJRwzgtLIe|IugJxGAitMH?OS(*@<mY|^r>13qewL4idsF0hG$&$}nciRfrdvogVwHQg4{&T%lqDiIc*4}}%37vYwjFP*WO!Wu;ihu7|ghoSwvQ8zU@)G@&|{dxonwBHfGZ%4_s#m{J+nJKLH5k|_5gT5I`wCCVSSgErB3=f{?y(|956O@^tzf>O+tHD9^`Lmp+L@5)q_1Ex99Ro8~`4Z@_AlJ3#FcG;LjFZkEDo}X~j?|9BiHno2Alr?iqh-)trvo**Jt(cS2@2}0L09W7UM!Vjf}YD!p_yXr?5j?!{w_a^&FR7FLSvSVmSN0Ad=bulMJsU^V5|d(Xs;_wxESF}>I}`d@*(xY0{B;>%^Q4?OqVZxNp+Gd&?<R?C%RxOa%Y+2{=Cn`);1Lvc|7GESUeBD*7KuaWjJZL5(24-r#M>WF?grb3uY|Y4vOyLF!WE87bB8Od7Rha;L!^F1xO4>B=LdwBV76_h3;IN1s}7_Nz$tnIHnqph1FeT?h;8{7gk8p+eS&d2qnAr=R)S)XqwEg3Qvw+rxC)|7;Za3a$TNtelDGX2VGO>fsOZhB}Wd!?({xxN_`pfHS2@MBEk%vPop8T(<c903OUdJ(LFO`;qp#<OulJA=D(;X?bm*A4z)|;kGs)mb%P(<e-x4pJ04S|N!IhdE`%o+*iakuxp;Qvb)47aPV@e(h2K?S@c45+N4E1Rd3;(8n)6R#MYjYTY`;LZY*R$x90R;n=!9w~QYN26Y0lFKj?crJ#G+J|qiP%nYptT-)UFF4Z0rk-e;;t#)WorGjVBFmOoxVOv`7)xVdvGKL;avRWZ=jijLL}wbJJeTGnB*pSuJ48T}!VEeX>wXD#Matag0uTM2BAc)71HaAduF?aaHn1z0sAte7|rC+cRmxSy?bX&yrC^JAC9j9n=;)p|P*l<1(8I9DipwTEDjfUt~_uW$iLJHHgP~ap)Fqd$Av{X$fQcFMm4Ia1NyIjiiJ6g^Sf9;*oo!mS`Q7#bdMDcp8UO$o$-0;AGoRvc3wiPs*o*h-@+bu8Ae=GDo6#e;OD%Mbk%Km+(`dFeFSDLDh4H*pqbw;_pRpZp<79mHaVKT6~`<?-s&KEze;=odPTlyG1WYk3!ogLFT-M3p$m&C#ko*;4UwNI6avMMHSvS>MhPrnHGVmA+Zqi{~0xM498z`sYHM<eyB{vz_m;0$0JMG<BF4>cIGlJes&fX3(2$I>U*)sHj-B@6oTL73~8sZ0t!UtEq?Jz82-k}!~KsVNOD*H|Ga|PdsGBAGffsXB3xYgv5mAZ>8E#G`0&iNU(}ChLyBUniR#E3=*)OYJ<9gMci(1cs}lp`&U~_ERRwM_wPdd-@}pCPHOjn_WWN<m@<VYbcsbcaI`1w~l~3bL%iW4%CPs|a+#aIQS&9OTBFA&v4p87*0eAM_f-5FwFn^6Z`0D52Q1~Zmy~G-}wwLm>6-BAn$aF^2HVQr2x#UcCE2vF;B6lmk;0?3QgszPxua}?47m^B0<|-pz(=7*l5}5@DVr$5flQ!&_bRcRR^}tK{JX|zSm2HukPEWa8U|2pw_x<_KYgFKX&$g}9%P5Yd{o!XmPrHa){;EK(@ny`<d4S_4fxw-~rI~V~%z{H2#9qt+7j20F8!IW?5fBX*RGVn=(dD$&?HC>(6@-m1j>2*?VJh*@0Oh-MK(4t3>w|Sr{6q%GYW$;*JN{wBttm9M|2B#muV?Ci3__QJE*ZCOLDzv&_)+{4JdTO7WxsaQftqxZv#|k&)>qRrX>-8ifGo(}$fMHJOn@WD0oe>5dTkeFVr?(L`e|#}@3x~5cX}q{NO~;9I-9X__ao#N;9-Q151zgDo?Kte$Ib|MW_>=shbQrRaI|eZUe>>gGHab!)pd{P%~*LhK!gi+!mjM?%c^XS&kyRA6bIk0#So!yA=Gq(9(@q;3{)PxCLaRDS&Lj(NSB`leNPAA-;NaC4bwv?G%W$1EM0-|Lq2#@tCqYh-$Rye7X#}~gf7lJYWLrByrjAszExhtonzA=-zWg>!YDqmONN>EmFZp2RG3nk4T@Xy=+U|&2$p3S&GKWg>C_1F)t?8ATW*lt=7IZP+D+y~2zh@{k8tJR!_cI=uk>@p-f8VP<H%Nsxw;y!DhHs-r2+7oXoab^Pf%`#D#T?uGoSX~CpVh~*t1^G@MJB6|LHAc*`qZulivpGi*?ymx;^yb`kTB5E4HxVUyWIPeLi+y4W|rLzu{@6ljK^cKEA%j#nT>p$?ySpm}u}uWqu_rT2}`OLxFHg`zkrm+GydEGftN@H^9GgWmf+X2l;F(F{(j~jh-sO9<y7F8wB3-X4y}2>`yy3#l8-H3jaqlM}%0fcb{pGXDxN~UP@#f2I<XXQQViCs(EW4S>rUZd@OPK%_-LrVScaXXWquVgKV8JT(o;3*|Jxed<Gs!xId+T=Ggv^Li2v-@_oa&O=YEwNQI2>$qMn@*ISf|R4S=7DGgCVo3bLxiiEOdXYc2}o+w&Gp(qU+l2K_Ys&C&v;Qr-0&f`2^uj9DsCP`P+DU^euYxf{(N;B;JCcyLRQRQ46g?UvkO?VxDh0&@+kIB>2;tdA#bGMiL0oTfY5K6j4hTpH_W;j2<F0XI+HR-(h`f6DynV7`|PrHmRNo^pxaV?3tu^PiAo?%?THB^*6jdja4ac0|1IHP+GGtDHq&%C8rIdv2HVKRjiKl~Jbmzv|tws44Z-GtwQW$<A5JS;rF1dc!9XnaK(ZHo8>zh^4JpO5vV;vvgkO?m(&$CBXO4td7*%n`7x9HUwBSI}e%Kc?K)LQUH><lfd0+}qZKY7RlfVx0t6vyhMbsUSxtE-i&hep8-pd>L%JqX9<K;;BOPGFYA21@9kEAwRiy<a1*ube!p?vp2p6rBkaxBL5xuUe?A(VKbrV^Z;1=U4Z?We7xiXn!F<>W?XpaBwqgCK72WxN1{KD;t|mh{O{CqGz<wujce(|`$9367#3i>hc0HTr<+gRE5>OqDa99+TJWq!itAsWg@1pGg3h`B&?18m)U$ul!p>$g-G4U})Z8S2j^)I^rJ6383g~BOgd2n3kk*$E8AtI%*!qgaZ-2vBpU9OM{beiZJ*iLkWVNx9OG2R{kE1J<5~z6gbl%bP!YE;1L8cxOW9`(=)V)j^p?~?}!SQk;_&6WJ8>`pTp0g|AM{*zuo^l0RvV?hB#WCdlYEf)iB}p9qDnPJME6x_Oq<N|Lh|Z@ba&Lx%xkQ8<wz@uqt1nrye%TWI4MzAxHUNJA6hvOoVqWUo7SyfoCG(bzL9*l&K&=E!NZm>UTs-lr=WOoh1rgr6*^-!UJBAn4mcZ}KS~?<?j*IU(ag|Rcp}bU=SPhTi@@ye0n&*L6MHX^vwr_!#6N2#OtPeUGSmN=7CzS4p!<A+e3{H|l*YFNJtvrlcmr`Ku`B&6LH55zSz7jsJg^q@PrSh-0pzCfva`B)cH<j-gHjNyiO%>vBrP+@vemR5FzDU=d2uvjbD~>|EAc5e3HE`oqHKa5Pfgsv|{>OA&J+B(Fbu@WKKb4?f>^Y4NOkjqWjzh)U@AO83H0GNcV?;?ZRB@%)(j7}*Jz7Sy_Y32Hb$}07UW5g!a=?C!K^^x5+;S)!W_)*p)kdZ$IR7P;-XnoWUb#b^-#5%^mEx(#zQrIk!9!0y;Yi>&>h)Zk%XvER&nXUo_7K4pi$yudnG+=Zp%=OPV=kv&ra`t8h(K9RKE}V<OZFQ-fb1MoqPh7lb5O{i#5SG9q^3YzcQO*c49_LHEBJ`sKp5Wjo6Eiv+d#7wg3x=k9+wZaklW5*NY(pE*r^-J*tOh2x2poMUVAU>3s{JeV|Vb@UlsE3t{{B^x~Q@6Efo*dhp);n$*#ZkFlaTKTisj8s>KF_nD{*0-$UTp!f2G8OQ_owA6VKc%}%x5j&^F-sq;1iIKf*3R{qiCLTG4B;%yCHoxx#Ha~#A#2WxaVS`Tm4zLA*|OGu!dBhL0&h`&v(soJi$aIjPy_t{@Z-Kcxy(44yrJ$VlkEZx9-$tcy%&SIi8ebHupH*B*}<ksp-;Ih5|GV)(8Y<f3|xA%?-TyOP2epy+b)W-!Rxc(G2H$|hMX&i29F@{7>exAUK1f1vZjuH-c!D>bT+vF=p`~`%#C+9`6^lvo2lPbhB*W+P2<BYGReNe9P1SozegVJ+;^mxg6woq_6Y%AYMbEe-QwZ1asT=60}AFK~j*2@2Q_7(}v5JG`z{`l;fHd!e8iPd_g3<KMm>ujYyv7EseiEp$gzO%!)^kW-YW%Xt(a?rqVtOU8+*XWtM)}UiE7m`j#g3OWg(6i3m+;E0DHK{bfUh8qPYMBh&S98ZyUM&qcvm3aD5?EIGl^#i(0B!q9dZ;o2%?eEkuRey{D!YXjvu%+(E(J9y{Jf$?r+MG5*Pz71P#C;9Kx_s$vV3+dR*Wlh%ctz)L`P+~*WvB(Gwvkj`aeW7Cx2*ozJ-qbWjNP^uW@0!8l2`2KzGM8Xe;%TQL6OkEVfSPa^#PakXaM-#W`J$f75GXWDo`4b_e6fKWB;Su3UQhs(oF~r~)xp_JI3$ezS!hmmzEOaZq?K2tAL|@tyt*u6kiK=?&kB;nzcf>6^scN`xU#SRYHb?}u+kn?ar}BzrFAGXe?%T>pY$GIHh;NbZpWW7~%qetR4ziCN*EGqzN)c$D4aD+RB2s>6&<8>}-n#`D}bRXH~w)^ERwjS0Tw#!CVbskYFOaTd<h3{cS-F62UMI0h|9#76ctdwl-_ICIDj<*f3UlU?UQ{Q60-3C@A3&5a<Qc9{${pTWS50i<q~2uz>eOZvY%f&PbWa3N_muudt|syvezbGo=~YbTY`at8;0T~c_!5pAAs#fFPIya%hyVSMr!6RsOb%<oQuLowOZ$fwi1RcbQ2y}N*0{`Fs)csvw{6%%{YkJR~45K-iW=wTN*IK1XMJh>G@&#y?J$M0=m)<&7L`ED=Bb8B0CSTP0eth0h6D+=f*O9#+b+62OmvP8j65q`J-rZ*2fCi_joKwaoBRl4X8!ir7QQJ{=2dRd5BbLYd-@2^NkPYu1fDI8fhySg`ztKj;^yR@lHkM7Z|Cn_lnQ4mN5(}ZGjE?0~_s>l!AXMK3EW(JP+ET9L@c;n_Xo$NcE?_{w}G!)+!W1<g46N6(bA>c7#yzD~2IG%-ARt;s5GF*=W1QHqOIueP;$3$^t<9%A&oJZCkEhITd<YBn`K2bK^42ShLfb-9KsMz-nZXddY3pDq`q~vPkPtHWk@Q+kutdlUqDiG~*k(QZWBqO)JQ;2E7HLqgv_=8z6MJ@{Fx!!=+d>81cpS>6$yO-t`U&g1Wu27>)S?+{xNS*6P!bP<rs~pk~mR+sr_e_>O_TV>Jw&^}wE0@lc$y{U-HwTc{Yo>7%{*ss*69bh05sfMB#4T&m;Du}>@SK&|paW@ihUO}2<Y){!j`n0;(rFx0=jTq9QDTyM9bANls8nPc*1mazud52c?B4>NKTGk_GCk&{ejn}7%!QSb<=EfB$J5J5r>h%tXxoGLY}>FfJu<i(tTMt$yqptmi2cX<9oh7Q(<u3&v5rp6jRI$FIiCCWdiWb+PvX?`Q7~15vO!LCv{#q%^Ou3n{n;S&V1!2d<iZDM4bDJ52>z%#fl~cq?D@2v*xdWfHp#!DyC+w}#@}n`j^HjL6qZTfoiQZZzWYEmR`p*tL<vdmMBn<ybgNelR3siGhu!T^HCK>R8)`zyOFneup&l+=ZVTo833#JK5}f(2vYiI!A?)ciXxXR1yH%e;C$bjN`F+8pil;!^E=v%tc5ifRO98C|0r<Md0XJM#18TUI-IBh<>@zC`vt}ycJ})GWQ_UIq6S9n=&ngU9mq>5?e!&#Xtz^m<Md6%#fVp{+pn4*UYA?QuPd8nM1{ZB|_+Ab%4*f-I>24gi>Ow2}4nEZ_;`NmDVQ9V!UUL!VxlD7w2E`|^VbMA0(&?nfUzg(7SSv7+2%=e$%ZcNy{h)Iuh$e6v7+kmjtvavJvMp2LhUpnrVM_vQGW?fbd6bU__B{p3_Zz6NUK;k8nS$2gD|EwrZ}fcd5flGqZ&OSS9q_(Pm#)4}%vY_%{B|EaB{)GYgh)g3o@R!b8-O7b5|I1i4Bh_Y8uR79-SDI>oxqvmIv+1(G<+n1R%`;41vb$j=OVi9?|hU_yNn8LFTrK&C7c$$2~;a(K+-N2=ZlHMT-!j}H^&}Qg`%Nkc|4}yZpPky{9IUI1D0`<pipy=te@r!OG}kNQf>=~v`*t@%5<?U3x*;7+*iE4XcqZ&C=#kU20SAGO65!IYV((Y%Ss(s@F5lkKbX_Ib3!nvH<FkpZ09}cT*7ue7v@>4Ob7XQX|Pu`5MR%z1H-19xR5^>4X=;F>mp@L5WRydvZs)H%@d5uyY(p5m5EnwCE#q2<@jFl6;59>2473OnI9Xi@WCT9R50uZK{Wy1SW`Kge=w$pYOLV)e<zts(xFTkCrr#Ea<NfA1b^yH5cw%O$fp+s+aeAyD0QFt?`H{BeR-D*+OEPQOLOV$^BK7Mvo(D@ScG*7y7+qCMcS(!0%-*<xYbdVcc)erNqQP1zpIwqxcm{mDBmPU9G$_ptO-<>B;qMMJvh9x4v6q2I&A5{uICPrz*IjNd@IPdCwzis9dek!S>vJAPhsolcKUg($G<F|HCw+u7ib27!atMXq+uv?KsyrzqbGBX&LU*?i&u0?Nf_Jjs0%Opr$WEf8GK`+3Nzz7sQ4s){IFy-rvS0^rOgy7BX$iFW}EWHb-GctE*SjVUy;J?d+Dp}e{Rh^z|TU)wDZYatctBNo4fY_#A&`ozsHA&mbW9?|EJAKqzUnge|TZW;tZ%exre=BXN_LbIq<_Ii`mxGMs^O(r&^+>beUKrW(D5IH}%O>VP`uj>*!&gKCOoNWiK%3lL#JFTS3<436Y=vinuH~5G|sv)}6evljeTg2s^~?!8eDcoZ6O)7<pC+e-yA}k87g2%HKJVsh0qiDVpeY`#h+34bolaB6Q}zjdPlR0s3w$;WF7kkm(Wx<4y0$yL@4|;8=iq5&GB&xh&c8lq59D(NiJSICpG}X?5IBBRjS*i(KvL{bLpoK5se~FK$8C9?+z_4BAk(tQzfk6GX9V12j$HsBoVHvG$$9yWSlMACE029_)Us8d}F~eL0!@S<43j1!BBYpNp8B1Ha6URBuEjO+{qvw}9p?mAZqIx{&7*f_FA<Bge$#LHtPyF8yLdUfz=g>*JeHfcrw?Ya{W|)?%<P6oUo2a<C~a4n$91fx55)U?pAPrFS67+<l#1TAd0T1@n>LxdeB<5J1rrPNc;Eagn7OEp-hA%`$s5e=~>kt2jV%?%xED#572)wFl)Dv8cN|2s_rX^q_1r@hFx<fwEdweJ}>=Q>Su!%dODz4j=HhY2c#W&fs^R(m{C%JX~`hsd*>$TXF~=hGbK_#8^nbQHWFjn~I+&*N_wIFA<}LtF&sPE=c(q03&pV=>OUXB456c6?H41YyUzF3h2dkKDOlRUMu7t|E&3Z`!DX(2}GN@yO`LlUufwo4M96*;~$A;dT+sOWM<Cg^rEv+^rbj7XbLm!{I_6rXFO>!?LhwziI7{3VAgS-@nR2A&lpdt8`S`fR~%sR1{t_=&Kd8;Z9p$SO25ndQ0t^h`eDolQr62rpX-e}Tcc?pVqrn%xgMr5b!xS`H@m14GnW^$uN8Mbc}qN1F9KaH4WjSUQNz}gxQ4xD*&E_u{6L35@>+Cxo(P*hw2)_iGT7FCH#vRUA0tK{VCGH}yymz9)|(_U?ycdh%cmkdtRjvv`=2w7IYD(-_S)j2@nN#U?Fih;HKxs5wK&haJxpHKeDG}YXY?n$h@0*uwCqlx?a8%JQ*eRs&rQOFv2e^epNGHIHj+;(8*y$cAAXrui%!?2V6KHWJk_0v%e<q}FkKX`_D7hz9||QSh6G|3>p*@)FgEZ9fx)dxXyEB^?<c156!*!%ijEXATpvQ8YB$oG-b+X)eNAR8J^@dh<be&zCW?~M|8hE?jf)LqY@J0(Rqt2Q>v_pMyf+P+jzvRL?L3&J_7w*0myl!o=5b}8tzo5C7%cd)6IQP9g$)@x7(B?J<=-bH+TVw&otlTO+3Qg34u{71Q|NYmLFVpE5A;%xhHLwJYn_sBLQsD-dip!F8X_COuuu}N&8a7gr-k5hl{hl%?!P;0m(`iPor-64RX86DHQs};dGz^8JJ58>q>UVlzxJ4Ldq18<OOr?tp4@|%&B}4@g;*Gy5sJaz5^?4CgRs2(6l=U^37lD1K`czZnE$OCCD*OI=}=oVwK=wu*4+9_{%(B^M+I%6?`}2CPTU0pzr}g;Cr^U!4sl?TdWo%1al^#GB=#cSfN&K#MyO^H#L8bKLvjJQfCw=eE)F2ww}?FZ;g3bfl(79!6V-Zo6bH-)V6*r%yy~4ypIZ%qzl{kwX==#~IDe)SiQkxt|9WuXg$0V+y&}uLZlcPkm2qwK71%ZH18Hbm3m<d}P$XTKcl-Bch;u$oNK`S-s`aA<tpRvp*IRt~WD(CmP=;IcH~=RfUc!^kdrKF46yQ6#T*Q%IRCZVl%Ljt-x{5mN{-(h-RmgFPU5jDr;y+ld-%6%cKLg3VGTi3FF_a_y^s=-&ly`n5*(U#?@li#%T~dlM$E~p;%^l>|c;U3kt>*7O)G;|tAz<4v8{Cw>L*Al4B-{K4wQKCB_PWh<_ro|GU$qMS-(4gV6>mX6)faiAtLU!=L6Y^Z9Q-F;MB_RYAlmoL4b_0W_3Z!`?Q6td?Kt`MA(rMn7vNbQwI|9Bd9dq=3C}mIk8FOZ$(>>hd5#wZc~4K~g2UgfsCA+VA1G<iaqVh6TJ{-ddVVCmTkOFdZnEL!IpF$lJBQ69@$utk=6TpHFvZKPu7)dW-YBD&7CfiL65A<X{eDP&$uS>wwqot9dN7JF0k)``bvfpNMf?U_N5U?8aNB(Pb5Acmof!nfe0sdKr8#6qVr|`{_YDx790L8ntZ?hDGuWvkf_lf5=;@77WP!;Dtkypd)?qhr$=g$~M(qO*t=Wz)ucMgBHfB_+?i~4iNgT}kFHw`sK019%3MQRZBm?HkymjlR;ThkjaFrfowhoqJ`P#jtr-+|~tS+UkYd+JVZE29*unY~mAHv_>x%6s%Jzf7ekZkk451Mi;xZRCn&y8_(xK#?K4$c72_=jj%xQkc~eWltbGid9Z<8bP`9piqm3}&s%LC`orE>w78bxshu+8a~%J!vLXJ#K}OS3}6o?jx^s&aso{?7?jvcbI{|8|3mS4Y~^V;LWg~^zc9aU6K?9``e%6SGh14&-h?|!o-T^8AZ{P9{juo5&_s<7fNp%PDSOEQC70~3mqFWz|#@4=q8;!a`L_-oVzp?ZkB&Uz1TbEBT-9HJ5Cx>BpEbL-HFpK6=BuEH7NPq2r914Bf7>cxqIseJ*TZgdySJ|!?QGaa`ui{Y(Q<TaZd`!gpSY(QDbm18)hy^%%}0Evl;QqAviZTmD~%x2iu+r&{JM1NY{Cy3R^@aqy3RlEhSq9wYc97JDHoI1|)oG9v#?H4qL2LK}T#pJXD^^6LR_h{u5Ogn^MUPUa2MFyTzzJ69f(Q6?CWDU81M*nB|L>An$$BNztKwMAjjLo|l?K<r>yfCjob8R}3Y2hhEW2-%j+|bA|RYYr)kyANKNPL-g!W>{hXaJ4?2p+<FNt*DNGU2Nr^@gA$d^&O$&VaMYZSwZ|nPT`QP)+Z3`}9#Q6J?iW}o9!A=WY*9O42=WF($(BfSvav6SZqJU!2lI-d(o+Ccckjl@ZKWhZQ=X^a^ogz20Jd88FR4>c!J<8K=sRJ7J1yMV)pc1IGn~piDw4xbHU=2KB$4bdETP$Hb>u+RD9HX73MpTI(viyxVgK4bvd%6Sk}3iG`m9OkoD`xsRTyTb^I`X`GCH8=PrknjAd0h|L(@VL%y;-mq=#-}kc=Lk{G*d>mi)*FbV#9(_yYKKCmI4A@8MgGKj#0fy@<_UK9JV=gnG4HCV}hSaJI7%9^7Yz#TFI#-{}W*%a*;Uw8D};_31Tl-BC~aMfXtC1|9Nd{uWG*ibv18e@MfVXnIgrn#ZTkkL52kP=BWkuim|lQp1PXwM~mo{5XhW!v^qm-V3r>VG4NGx<W5wL8hPqW;6}4S@D^~Z9^@tTvpEBe<{j+hCJGF-T>}ATubHGY{LIG6hl_*QpS310DhVgiBl|A!ooSLh-H2p#98IjCe;~SPF7;w+h4)3w%G+=Y&gNhu2Y4o!z&R&UgFIkdvKu&hksU_B+<7IL!40=z{)66_i#S)(i7nPs2h3J7(z?N3uyHDZ<y`=p6*@08{@-Pp=ekF2GwXoUj_@tp`LJa9!m%Fi>YEtKdnA_6sP9Lldb8CN%NB1bvMKR6>MMymOsp9kKg(TQ>8?}@aRLrf2);RiD_X(d@@`5w*#Ivst}pog1iKuLehIW3Uw?S%)H&&>5?CkaFcHx`ROeL&7TfJ=@VI8U>O1X!!^0<gRNL8n`|a&8c2`)nG0p<?-{>O?Xdc>Fv%~!KtIN;A$sn@oJ{sd<X@u>Mla98{HNzIZ}U`A^`)2SG&w`aMRNq-P8jRCOl@a7;jvb0ERT!<-!*aIyDX7q^bWJpt7bv=p80TA-UWX;JHhxRM@W>GCmS3^Au!<&#wh$EilIm>Y#NBkj4W!Dy$Vze!+?_#<{i*d#Vu#Y%!^YJXi2aJ8Jnws3SI?dX~`6_&Uh*L9UB4$gM`#p<ly(}5Lzlc6#|>}z(wsDsX2WH9_?<&kpAm+M%vHGxlQ4abNDd!OO!zWV{<rv(;R-gCxFL$NhYtXg4wrp16cC?b4s_bsGFgO&97$gs*gzj<9-zs!7A{Oiv(5AO8mPok)F*lqRrmM7``VN168hr;Tvc3l1Jj?htn;3Hs~1e9<Ky#n;oFpvX^*$O2L*D51<1QX!p9=C>)fGbA+nMv1VO(W7`j1*=HetVLH~V2?E(CubI_v){#4CiJRx`qn=vAoYBLtOx}$q;;Q+Etj$v*LK!cRkfTi1k66@8GX|dw8D5-%6s!nz$D6h*vFw!)j42DkJL5kvbo3YqERUi|dki5~Ee5k<tHD94hs4ndVmazb)svJkeDz!CX^Eu<-J7A{t~Xto!~3^GJ<PE8K1hEML#M9Y&EBohrC&<g>ROhZ#2JUqLGoiGwBsGBb(pi13t0FFReP^P)Y+>j9$62OuSBqth@<L`SiBMRgG7Wn;d)C=D!OX{oSi5^tER<ZzXz#3e+JyzR7v&-ITCNt3fTWd0NPgjkm$SDskq}aQm)IxPeXs0xUUT~=}sQGx&IF;ELJ6>#SC-rQV0q37RCnJLOf3zlLrDxMESQHD2zk^dJ;yzHj1`}oQ1gT7$O?vgwtbRlNzfLB4fUoV(?`;r*S5+*8M`%tjb`Iy)$Thu*NKPb+FiSllUE~V(!iVLOA|uIDW$cE{&(c?KktGE7}0J**DVeBvp9NIAfeyAQ?%&4011#WDkxp>BmLt9{d@hBS*e44hy3(C}b4vW$kd1+6bAOcp9ed7-t)<)DcnRKIU2EQJDP(X()S?EtfqHTNg<{#UfEEG-Vs{-nN39`%MDxm&ihel|9`5xg0&UBS8JtHS;T;0_>?ZuTfLX9<>8wQ7t43QjZ^`T_X};y~qP4qFr&-7k;wfl^!l0l)z1CC)s23`MDEUmoc6zrO@x%eSG}<DSC|TfrOTu7*Jt}2HR^eZNh;ThB(2H?hrkFRtPVDsesn*U~pJr19b~O!mC#qsPKLd^qiKb=x0Wb|5AaFxI$=|wHo#b?gXpFwO~7SHLZ+&hmTF%@P&FJy?jan9~n%Ao|32ZR)80kxZB1os@BJ7I~``qKOb+iJB%@zSJ@LfYp`0!9^q&lRd{a<tJMX_(os?JeMlPL`V5iUJM)0s$&csCBh0UL`-7UDA~uGvhrW?CM#9$!pN-taZS}SE!M=JR3#}0~3$S-@Fm5<B!FYP5Q$Z6I`ut~Xox=U+q;ys=39eRvsm5Js5a5iauRH19d|9g0xBzT&RhU~%;_$umKl;~57Vgc;fN1-LG^|L5>z_WI&XC~4YxDT9AZs3ceteV2cPo(hr;;$<AsH*LT&8xHnjvb%Zv5cnOujm0(1TW^bjFIc;O|T6(yiOU<E|97;B0}H*F{73|7Q3f@pIo*t8rF*0XEE-jTJwK@Xc2rlv;fO3l?dy?ce#ihf9XBNl}R?t^Gotrd_7>p79Wwr%zyt8PpBmrjJuZxV_9LQmNYlZr6{Jt`isV;?PzUY%NBkt?nR{qG0a5Ssl*loWWN+tgxyuj_L@clMQx{Nsx0MuBu&6uPGH_Mi{`|>C>P*&WMXzJp)#}xQk3&9?lsJ#hbHlftztZ-fr@TJukv&O%jEP$|@4nV2$p9eq_VsWIA4&PbN(ffiLIwWB%AmT*7G5g484sfAb%%&$VUv8W9&A_Q4+q`an8h5hzZm@dA}AsS;}hC&XH*_SPIUnIsE8_C+jYZE~n<+iujIUx_tMws`EIIqDpo#e6!D!>TO{#FRc``t711mGIt0_VuQjNm<v^37$10c|s7X{OqwMx{ys@6$LuWTF5uQ2k7u;7VqvfSKv7h5;6aFJRuT^b384uP<?{@xta`x8R_`cW;W+**hv!nKC?FsexVaDmS!z70AAToj9&bS-rTE!*Qd&0@3UH5uuUB_%hJejged5U`e1jJKJeddqQ(~;XgA+e%uo!+j`yOl@>v}DPvkV%gsq0}i+-Yt)i+{4U=BGOc(~(B6mYA=xz@HRSYuUz7sI-s+U64bJ4ceMeVj;ZyF1W>4$!a33UK^G7a3AK&TMnlB0)w*Kt7hU(_H@0y2wi8S>)jESDhfeeINK`C{sDZlThIw4sT_X!RoIejE?xT%QTdr+glMn@8dya)&&%@TMpBvJVbijjnNc(3sH{!c&Y9+d;a2P%$5BDtF6~V$n5!$cDxD?{@eh&ykvO~-ps@&`4#k4{{Y!AT1rZtzmm9iWoBPTBbjZQ$FiSx!4c*y5gs3Axlcl%b%zgkoe+lWzt7WU?<}~>hQ%~W>Npv(3x<agpJASfCKS*5K-~0%LA3Hbc^O@c2Y%c{X3`-TwC=+Ro2$&WZ*ySf=TI6gDuxoSlm5Ba0bClpidphvn9iG>fraJ`G$QUIS-HxW%3hO%XRp2!#V5iryLT7NT3Jc`pU*()5JTSd{AZM%uY_w>Cxd3faj@mrMgQ5!)Ql#fSY<qY<sJ@#zWMO2`y>-uO~?!gVn6K9ftb9d$VAqm!p?U3ZFB)|LH`sCxLSh4QQOdWW*qd@e;_$WmxAicSvXb~LDQ>pVW*o1EYMW}opK?rO<x_Q&v#Ng&1ujyM+ADqi}8qGH|x$m0{(NIG}%xIbUa$Hxa$fin<T@!{7dBSfDk&FPf*Rp9?*?~tmeE~px+jOX+kY%cTX9rTo>TMEzZO^whL3et<grl1QTF{3b<S#SC{(1p)g~tii@LDV&>y?eF@Hq7fgMGlOTBSS^PN3nA%kR^MT2SVY>D$`uA-JwC*~LhX)dCjPLv+cJ3uiovjeWFWo`a+Jrf)79VOsT(GLSio~8WCIvrIsIBxSXy#4gik3Vlqt^=P(sf%&H!qQ>mmk8RTbcM>V2D=#I6<!b7=%%+APhAyCRuxO@ma7ww?ADOf_NWD>|Z2L>U1%iKMNnm=MZl`DO8inhRgmlVA~~8jB+`FB9qsGnNcjr)s5rJoImj1bP;dfmkB!FUV$%W%;c&k-$VTsVYrozr|l2kqGpL6%Gm~xGG#H+C+dg4_4R;n&TINKu8VxW_5<tQneZB3{=pSB_vxhzJlL$r&wCzp)qIvkHu?tpFmKKb!_5!kz?Xi6dqBjwcxO2dyfaY9cQ%g~iFk$Pp||KJ?1?NUXTQD0uhVC88n2&Won8sPkBkHT{AZBZ@)Uc|mSV>OeUP<&0Y@xm@EmxRY=5*hy*{Z2Jf4lvZ@$yHj~<=0DsBVk)>VLOZrp_m#W3bsy(oTls{-XkyJ>yC40<J9qd&Yi(X(#iT$3Y5+VkGfO#NVtcmB*;UI@hNr)xlnHv>*)TH||uCwQ{?5S{innws3@LyKM0@voaKcYd@1j~<^x0(v&W@{|aaW490oeqlV}E6$Vnt3Wazzo5T;x4}hMXEGqG&PdN1z(C`6xGMDw*GAo<?dG9ib7_<aMOT6NEK%O@Iv@I?<QPgn5`^3Xk7+ZTiJ#p)VYb#*oSL_n=#)<7Eq8bT1?AW1q9JdT>-mc125D63AHUt#PKI2CXY}=(^16YY5zMV+%h}0FI=JT450vjxhb8L|keO}#Ffe<74o#Vat|f)k^u<fOczP!6mOF#9e{5&B+zX(q&Mby`qf@x6eA-;_&)bw6H^xjkb=+mbu;Qmhz&o>=iul|@t2Mvjv-Wq;%3q77b!udHKrDV`1Tehm9_jrRLZ9TCq8CpIBP+VG=XpCkUT22+PpweF>IX^BETpWz8+=zVhm?0)pzW~=0{?FOwSEDOQm=+<InsDfp$!g=tMCj0W`K{Y4li9T1Y~1-S<A!Ek%_Y5R81mrouL!QXIB7E*RSU6i&UT^-4d>^6oKcr*W*2d1Z-(_#=<EgT<)uUBvH3O^5#=yE$q0+^iVh>o(yzY2^KuihE8P`!uBVCQPp8GkQhhu&>g>eOK{F=9OmyA;T3P^;g?xY(X1~KF77WOvu@X;rpZ(^h}Gtr7k<I|E6d<@I0Nf<IC0LSD%_TyCZJ6Vc|AH$p?l|9dUxwBh-o*62M>eMF|Gn<_TQs9BmC$({~8(}Zia@8B(&cVNi-L&g0p*1!I7E)R8$t{j5rSSmJZ<56Ps|Z*8{S@<u1AuzQx4%;yk;<gRnIA3hc6(&czB5o@&TG+PScw7WCVoZSELeIHtt@c2VQvl{SGt4x;qJNJ#6I<XKGL4ewpMXq<-#T=kpEW$viNe%=I{-n$F@r^UFXy+x>~D}Y~Qlh|ryRZME*aL6f{ys*~9r>j)R=3iHsLD3K_p1BpvX8op7+mF+|WzE!ie+eF+Is;@<0>M!H2@SZ~fV@~y*dZ`VtKKKlkevd&(c9%jN!b!Iw`;+Ab}H=MGQdD_IC`k8C!Nx=_$7ZDhMSvHd#x>Oefji%z5kuq<%@!%axO|<RKh3O1B}-90&>CQ2fejQ6&jk408Cy-?FOgdysk{Rvi$;8mGHwJ?MV7NDj&o$4-iY9KlDj;LHT4|Dp)5$r?g1%ESEe+{fT7ktGsAFEpG*^y2=lupA4Cau4H(>tpJh~Ls;2gh9H-B*F4Fshb|~iCIt%Pbl+z?Iw)rc=X*ox$oaX1i-`j*eK$NHW{g7{_rP-JF>3sTkLzwTWzYRkCOfnjU`=E{EE8BpzW(EqCy$CC()1pYAMU~Vf2vV7q7@p9`(gT@>0o~)7jm_(Ft$_A5eJmV!l4d`KW~M_c760p%P!`~&Qq|UYa14CzDMMqmBX%~3UXz?G|xOG7)7KU(d5`Vo`sA!9Saj8A7@6woL%iyLg*H{>`29Y!6Qs+QX>`8NrFB{J6OKog)G}74lz(i)7EYwPBaBPc6q?lo}28?G(KLKjUYVnNy54ff9ao56|*~&XMx^M53FxJRJZtW5qqZV1}N>n2Fu^B$KA$u_&MSvbbONK{<Cs~OOH3vgM-(o*XVY*xA7M{IZP7=_GrTP4<#UVE{NT^$&_-nn*a7)9VCCgpyLJ_<l(g2c#UaB>y9wEcA(w-Vn-srekld~4|GA!Q-<Tsk;Xag&9G!r5W$E%I{5`3Z@-5>E**`8FHtpU_jL^@EDMJ15dz$aD`j}+YbR^7RTvJpwUdR$r%B+f0A{SY2Iqb}$_7@o5W8&?)c@K!{3f1{Yrd!g`_v06*hng{y@{O~X@sg$o|q!bkxi73WODv^_oxhcTG|N@Un|f;sS`NU{}pp!S|iTg{DMfDYtuh-cEQml494y20R8qL@U56aM};<HO~ZYnn<@$BhvQJWeIDHHSr5f466qh?PwXq}7VO>emCjdh!|=+t^eUMSTcb>I@2_C=OPqtt=J=ufqZBsisSNqBbQPl;D8XI)e1<F?Uj%0*jdAT|XH5GhPHMz{p!m@Zc>P8_w1EItlX1d?3WDQVDd?lYsB1`3xcejgmfr?i*&k6%*Mu(D)#0g5mO!~1<#={w8|;nE!yS`t;nwq5uyPzGyhYbBZ{`$EUrL)fdDxeD)j!6Hh&*z-eJV|kxQMT(>>{K5pTIFG8Mc);(HGNPcxnHhd6&!79Xq>_Tc*O}#P_^KnfgIov0y(a4lm)nwA4BGm>E3tOb6Ipx|z#RT*Iv|zK=EYthh~krgH6bc|3=u_fcJiaObZp@wj_~^kvs^dUa105xG4R%Nvf-S6<4Hac~u*efk8L`#oS|?ySW0t_D)IRi7Mf(L%Qy7eHoyBl9!$0T?Z=W#*S@;!>M@veMTG#_!)CUNZ;CiED1KLrD)pt_;A?YFjvWtq1kyDDfh+10lAjl~^@0+~09w&PVGrPJQr=6<!n!t81U(`nhuG6a5Y*G*Vz&w;;DP=mdG79$_xN%^e4PqA=m?L#QWHIGdIL?3G5Maa<8JQmuI+REX;ykAuj}Sr{NP1BI>MWAWizxMckkY+i3iKTr6=$PXv{DSjCb6-Pp*az8ma=LFi1zM-E_r(=kr1{ZN`1ck=$!!n~fv~IP<lJ{G1MNkE-=!+(A8<cSGXaa1n`an|_CYr|<{l|R~sild}eqd*B77oAEfK$l|X#07Hj8Ex>@O7I(Yk?Q^i&|sZy9n@_p9wV+0mQ8D1QWIVHatDR5|N2c$|i{N%$67t1C0!rd-@^qo$m+YPPL5ovP{_U^8vc(Z$`1CT0D4sCCZ+k1T)lDun%IRFji<jBsMWPd5J3M&IyE;3lbzxOo7QTx=okFk5KCkzahOjl}Y^Zo%Q)8Lp#E=sMMO-U|<{x`?dz)%AFf&VWk0zz0`#H@BaS>afs|LctT_ajNz!4AU;?X#k|e>O=o(@!@PlRJR!y5`u*i-GHW?Kwe1q!Z?PN{-Uoxsha`A(I|?v5lWM%*ONBqy!^MwM#C_IElpYzP1zAzxr1+XnX$SzRE?@M?kHJ&5GQ7Iavq4?km*ESk!-&bZNyQ<3<fHQJWgP(&RdR!uuFKKoxIP@V$|XtRPT)(7xMR%{yzRbgd8X?Rk*B`i+}V$Ylv!zp|9PaM`}jNfY@Uv<rD}0#Pd}Ut>?h~{I&g~C&3Nv@ZqAFRz|H<X7e&%6c|suvu;#ihH*owSnfz0RxAWj}9CMpVKL1=tZO8S|Z{JLuvh)=BA>B#0&Pb-KGQA<47GcDrJ@8Q}lpbhXPTIeWz>9l(k^e&?j#}Ko8xmjfv#~kc(nh%bbTWpPSrVNYw;3hR7|8G|U`!hSU|@$TFC~5#J5wTqXz_B<{Sk{z0tR@|Etc_^dmT2pNRs8<xw!mbG2~ih!NKpzcrV(9)K=Ewp_=6&sPUY=wkDONt_;UQ<veV+8YDhl-Y6oh0yzbVq;K*aYzU|ThoZeWH*6lXwZzhbl=)cAMbKhBH9C2-5?=c{g9$yVi;pK{(OI_yTCabA>EF7D?ln2y(B|d9YMj9<JLDmce>;3yR?M21)zMi+aUi&01UE(c;a|ngkpKM-In*UfY@!QsqWJ{MrY<Em3#G7lQx}f&o0IFyCg|fKaq}ZfC9$jj0i0e_&-R99(hGT8*%zy4<M`s~V6>cvzrBy*lNYD)O|Beou)c~JOE`tndpAJpyRG>A^*X$8WeL1^w2e+=@RJ_zi!?1@n9Wk^VU`VN;;ZTMa6xt-Gdrgm_&WmFj?=Q7WlJ8+WqmN0?+7M;Y9Rc+1>iRliSk8V)YZcb@}tslVYhmn-N!m|_}X%e>`R80o+xm+_YgPF7J-{Hs+fbqJaYF}Cu=n|npj+|L-nmqMB<kU1S-Cz+ENeU>#!7#zY8EoEO*d@t#{y4#187SAQH1A^T^DlVwk)-ggGyp&uAWZ!tsB%bQy0(yR)hEl>Q0mjx*pkUrWUaK2I1M<>zhe;Nxy-JSHj~%OK9*4L=cms?i?8u6#NRTlu1Km|kX#-@S(&jniOXbS2JOyB20&%?HB|2f=Kz8iw$Y`J&!{W2yW&{nsl<Y@5f)vD%!Q#chcB-N8sj)!{(509SK42wU6BQ8)N9J;W!>)4RSI*-Zy<s%A2f6<*+1sKWczZGeJhJ0UIk67rTtL8p~5HWV*{&Xoz&?@KPSpK@S0?F{v*8O2H(3M&$AAf;(1{bqj(69vks(sVvdzGO<(4}XP~10mF;a23AzTS>jO#V{~>BaPdU51+r!#L)ivwAu3(9lmr1P0m<g%HjkJ(Ng5@yCl&f^)ZqW;6$f=d;!#GA6TCU5~JsfgZo}mi$-@ajPi!SJ-YB@X9Rt?Din`eN8p^&2G}&$5gUbdIG<IC;QUa9?7FcYtl#X!rnA}{t*pio0Sngm=v?xxiNkI=2b60VA(3+np+Kw~y-OFfuV=aA<AVJ-%|iksq89Qzjc$;(oK%$UbET7h-NC0dvf%Ys!o2XEJU%ir#H@1*uqvpBjwQ}uc7%u1?;#5Ctv8SQuDVYK9Y;y>3OhKuIt~nF(nxWS1`#j}1y!a9U4$8YtKUNBMmk^t9m4-|6XAy9Zv4+s4fg2e!m<-4AhX||Tul29;^gzuWBO~Rv&b5CRC{p9APLSbVDZkaFjh>l9J;&rz;vHlqWWf(TAtFQ*NmreA-(O`u4jV+)swjSU2+&sJK4X#n$6M70uHUYh#oeTc&vC0O82#5m})4*Jiko;Iz{38Mc!0Zp$AKu)hIHQf*Sf}j3;*t%2z93Q++FNH?p8$<^aA>eoJ4CB;pOBH`Gfyndz0a!qu8KV7r7LPw20JqDTWg$!!4{<ri#cB|~d=){}H2OE{-H52i-MLco$!WWlPbWY5eG<bs|Xk(7AHto)@5vPJ9gW|lnneBnHN9(9lE{ijJ3>UY81iyOc<Kd8=nd>a@(yhaw-T_GyJW<t@!Nif~L8jq<R$55vr+FL_F+~zSU`QF52?Yn~tr^fJ=L@3A^)sxq%+i}f|9_$pDMa_JZ$eV47;Iz32NuCdBJN$*JSNVV?Yl9mdwQ#6?8tnSFe+Q~R(wtfoF!L-#DPMc`*pY0!q)`lF=^5CTb_hGxg_8Cw8sO?UMkB&(aYc3s9h)~uoXS*ah3Qi06kUsI2h!-mT0Jxr>ta7&zd}0#S&XibgbbB*SaQH0?Y{dH7hMhm9L!<iyWdRUJ5{`skVK5dMaV+(g3MDdgzwYc$l?RnsldQrOuZBhH<Md&JBssWZlvgpO&IC%u1<AME>YByB;hKb@L>54ChFjHbaD!(nrm+3r;Bpj;<{(dnQ#BPc8(_dF%Y<j#cecbT%GndY=k-`1&)1bLT(>QN5PbKoCsDTCAT=xeBMq&vfI#f&QY4irv^t$cJX8*?YL!*Ry<Mle>wdxjgCsX(!(NRNOqmX=~=H)TmKv^uW)BY^Yd`Y(P5%~ECd=>e?|6v4n3x^3g181<rZ#F$KVEjUeD{NDC4jhUmtWQ7Hca&{q0qF_?Sn8uLZ)%b7|n5;tm6o&tTEEC|oU;h!LLWF)VK~JhJ*s+i%3-@AqNws=t&NRz@Mc-i*ICl$vQkG&PF7iC*_DNlmu{##A8kcdv!=13jRxIz~^N%Exz!t+4)x9(((6NnK#GJ&{o20qr}FQu<zSE<J>#XIP=UA0;8jqv)TMMiS!RiF^O~@~K=SaBelm4T&kBQu~p8a=Q%8<0^4@>MU?hD1jF}p0N2r7M?p@Od?%AQp=O>BvtA)`thQu*WH(x{Lg{(k8*6<g-1kw>MmkYZ%!ZHngtRbgS72dBUaUI!e>2lT-WRE_}AeQJ!lXD(>M9UM8YiI<@Tk};qZ(s+LVQ584dJlbS&{*d;s`5R)Fzdic@ynK;;W-K)j*?B=;5*6~}8>UZD?0y-TPCkDm+kO2?kE^^hw%2b%QcQMc$l?dvt*$bl0mqMAZ0O*t@TB8jK=3(Rr2OHy7e!_EB`ME0c$CmcQuNmAWV3E_BNZG`>m^ObCKEhD?9WkT`gL*Vo2Gx&sg;(ixJc3xNvDfWzFq~?rLozv}fW_bXP+FU{BS?=totwCU^+G2iYZVWTguYkAbiE#;MQ;G1eG_oy384fb3s38zdf4X#|{*ES$A17$E+>e!aFGhWnUAX^t33GE-5Eb=!hclGBNL&8}dNVbYFjYn%+0;*?3uSn><3B*jqAZa8H3VZu+wr5J4MgXOL-H$4`sYpvo8lS|oWNo9EFHqLnZL-}={X=%RZ#cn<O&S<kwP=`*D^z4yD{EoKYDD~jgKVl5JEoScvLH-R$c}BeWe)gJ&!m4)@;^X<|fsfV}y*n4VNpEiT70x;KeT`VBL2b29^fUVR0qi=s(xb-KoqaWz9t2b6e2wQ6V|svIZl6I{{C+5l7xuqRV|heC&6Tyld}==Q~&6<>!QCXOw_q`U9#pyp>hz&%+q~a`ye5wd{9GA?O|whGz#vAR&1fT;POx9}MQ>8JiMTNt(sXvrp)?Dn4#2(?bm^gh<TsN7TnP2uqu%km{WO&}zJZ@#W_Or=rQ&{~?{cJKjmwhK(Wbxe<Q1m<|uaSr{{T4r^jmF>F;4tu9I>xA&dE31M|Spm&dszm_Ds((hy1%y?|PVS(P~6IqFGM^WoVA1M$3Z2V<UD#AwKWVRJFZW6|4MT+?LCWW`DN=)3FP+YlcF7WLu#Jx&W@cs!QsJhz7ER-l961Ky5<McVK^>@K&wHd@BfS;$MFrC}C@iJOSwlQ-VX<k+35%~Mf1Xrv6W~8+WVP%g3O8aalLREEm<jQ4ywTea2oE4CgCC6p%o`IPk;;3?xU0r#&CT68JVA8uusMv2q>oS+q>TPTB<A3|j8;tBxVkbu<)BA|>;SuOLQ9({9=Rx^ob)NlJYuw7Mz|$|+(cio%oa8Hpt4A6^b5SF4*K)z^tji47T?#SRRLFqpLkxbWPK>uCgW=$DRLh&h({U)mfHekK8GeRs6nX-ug3d!{u`UdL$b;uy#&m|`Rcbdmh7JFr3Xh{IaG}^jEYa1+yAjujTWlX*S5qKK1=rxk-2i5d{0fZycpWT#1$eV<uh3P&a=eYf_9(6vi+5_Lq4TOV;$;xbtd;e^nJYzjQSZcg=&nwr8z}_2x1sdkEHdZ&Y1~~i4988M;5LzQJeh3>=^HLXpYda=D1RM2E{JhW^-u6iK|Jp5oDGe>--(MuJRG?6lR0`e0R$@JP~0kyES5?Jv%yyC+b@7wdx0GL`h~pGv1E<wE2&@oJ6a)s4A1<jr$e8`d1v%*5XY!7rgZCT_<H*pqoy8>V)g&gXAjHqV5cb&bkW1w`#0&vm$Fo1ng#Fi7HekNIUVd?mx5v6-(XPMGg>-H4F?9#(c`ub5FyTjUKl^zZaE6F*G<j3Z^S{exEt9qcQWdlb&@8+&)r%dPpZ>Sknwnaa{S&s!k=n}reU$LCi4wEN-br-#M={}S=yksD3rcF9fM=u_8?T?O+NTjVs@v46dmoQZf%0_X16WUs3O{M(UD2IA`PiKP2qiLDQ-8Z#7#{HLCDn=+Qkpzh4-3fYXb`~X~AB4RPqf?@>3?x!u&vl&yv3X-c!@d&6LZof%;TCSd5Yo7^Z+$jz?ifn-GAQ0GNK80zc=MvIFAf^hrT7Y<7Rc9`M-&OO^*hY5Yx8e$hpe8gnpzO$|)jHi@fVHV5vt9RbwM!_7Cs@WbL3`p|Ye&^PCa^NyODYcAnL>gH0SQ<DQ;*A-z|fGhH?oy_UP1QW}SAXw&mg1ovL$hs(5!$yY%u<G+=^wz3ljcpvrl~BYd@|Pg%pcV*bj$`h)IEH$>$E?kVplW*pj&IH<lL7I<y8{3jnN;YiDQv$}!CFWJg6m*Dng(6OFLk|itl%n$l@#IEQ~%-D>OX8?%10LdvEdV2E7+6~z^>LZf%^{UV7KzVTHR}6+@!4?=yLQcJo}^qQSN=rkBJWYXQ2#fdr-xew7BA$`F!YJ97x$gSJbmCWs<jD0!?E%&UK$Kj_W=p9!-J&3q1nD{XHI{pS?fPlf89!O+A)$ls)4IkUR8j%)R{^hdYgxWW9Ooe!Fc8HapWvtv#_=?7Sd6WxeO%i9LbPzr4942EOW38oc>A2fh1S>pU#Zcsx9#nY~WfhrOL#e?12{ZoItvT0Ei<QoSHsXFQoX=e^q{ti2`fPQ5B7lshcdggo6&5WefC13XEs1iY%nExn^1sl5$DvOR(GvOL6D?L4$qvAq~fTRdb>n7sK+NWFpvy1ZNm0=>tbmpv@_I=rDDjy?I313lTfhdiv(LOu8Rp}dQ9bUleKp}hdIAw6R|wz*2z1-#~_553+5ZoO`ss66ibWj)e7x4bK1TD=y8JiX2_S3FH)l)XUx;Jt$AyE~0>0=;JN!8=}m$2~^TjXdTmc0JPcG&{eyiM{f9D?4dtOg(v;A-$EV)x2-9nLOh;?z|2Fn>>36QN034A-%j~^gI@x;k>Imvb~j~pgY7)PP<hMd^?JsW4uRuoV~ZOpFB%KjJyV-vAa*IguR{S!8|z6485@+B)#51$ULGXP(8(cslAuBvc2V2Bt3&zyS%s1yS@DTbG;&RQ9UMOpFJA^3_P+wSiK_!jJ;?f(mWF<jl2u1k-h3NwY@}rTRl?D@ja@E06qW!m%Qv1)IA00r#<Sq7d_to@;sdM4!!BVAH6WT<UR9wygi65*1aC(H@t|uAwBRoZ9VBNm_2W16ur8veZ4Wp5k2Hz$32gc$Gr1#Hoe1!w7p6aOFS75=Dbi#0|YGq000O8001EXX+}^k|NsC0|NsC03;+}WY;R+0b6;XHE^csn0RRvH-~a#s00000s{jB100000o9q|r8xYCJP{vTLo|0OeT%>NLpl*|9qOPN$o|a!!Qk0k%pI?-c3KDlq%qdO<iWg@j7Ni3C8b&5MnmP)#3giQ>huYWol&XK-<DK=xM(Dw=J(~o}c5j`##YW42qm9nOLpBRHT(Oy^bI+!;>*yZcmyd0B>0h?F>2YFD?}U`S$0p9VSs%aEX39268<|9A+oSW>+sw|%+>_8a(dLry5gXgvGpzwoO9KQg000080000X0DvBq#Q*>Q|NsC0{|o>W0Bmn#Yja<BGA?d#c>w?r06+l%00000022WK000000GsR=>KhQr$WX>mt)7xvoLr=CrJ!z;W}>d6pq`drR8o|f7oT60k_r-cOUx-w1&SAEBo?Fs`5HzhItoUbItsN4<N~g*0=4_ri}>38`p~?O$4l5w?q!KxC~wieMVlJ-1-WV3m32+rH#K{rUB=3#wrAE&w3T8Dx7+b;=Dw4G0(R@qpWSyVC&~8EROx-(_BU;xs4m`@xwc^M^u0ZHi_%uv<*TgSm*eAaXK`urzW9?5_DzV%wd;F4d#~i>PkSz%m}<9jQ_j982O{nCZRhQcc(TVXGTdR`|IM52?v;Psm-%m-P3=9`ef2M<?_JSWXLm?qo?Xx1_Pvc)n(RKY@a?;p$iFwsX|)XiP)h>@EdT%j2mk;8ApnT_+${h9|NsC0|NjgC6aZ{*V{3C?VlpmnaCrd$5CDh(00000002w?00000005ip7wQ`j$;eQ~P_3SlTAW;@Zl$1ZlV+l>qoAIaUsO_*m=~X4l#&V(cT3DEP6dh=XCxM+0{I$7I+{8PwF(pfuG7ZGwq3@?djU{O0|YGq000O8001EXd_IyK|NsC0|NsC05C9YaY;R+0b6;U&bZK^FE^csn0RRvHhyVZp00000N&o-=00000o9q|r8xYCJP{vTLo|0OeT%>NLpl*|9qOPN$o|a!!Qk0k%pI?-c3KDlq%qdO<iWg@j7Ni3C8b&&rItsN46aX#;h6Z~O1^`e?0|YGq000O8001EX5V8Co|NsC0|NsC05dahbY;R+0b6;a+ZggdGb1rUhc>w?r0KgCc000000J9DN000000G(I+Tg>VEFPcu%x#=)7)tslAX=*w*)6D&%h#Vp-a$2DSBB_w`DPoBwVOJa5I%hY^$N5ZIi4yCSge{R$q=T}2-g{l&>-z_M?q8nw{anxWe(vXay<YcyKe<scBS%j#G+1b`)IB~SeMXwQR_Lxv3~=`nx+l&{%SfA+GId^Be8Ru;5!2?RCouErGpD5{FusSMmoU&z?dc_4F8m+Oyre{ru>uJ;W;w#{whr@ONua*LMNtnyX|BZbk3tM#4M9{(AQERN;rcKV#@<@oEcStP(=ZrSXfP#D1u7o`pS~b0Fi@g)co?{azNl!B;!T+zzqBb4d)^HPIC|XkcSrjj9)^zABHLMnj0^`ny6%r%N1ZUT$`qeu;c%R&fW;gUJlNs5ZlOYcjtuFK!_Ygx58Jzx*jpBXsu&I0VtpY_AA(o4dTfgfLVnT^tocI+gNi`x?2W*~(ok%l<PWp=;cy-qg6*rl5oZ;S>9+%sD^TFwzJZvXq{dYj84h~_i%%+W?3n<UhlXHLhydT4+C%*Txc{RktY1iQB^L;O;sxq+$Nh<+m~5lQJZ~jBGj*8F>^Xm$8tP^(j4c8%YLYKRi!|`l`{LX>H5$6Lc)u_J@r-|IgcjZQe%STE3ung=K34f7;*=*!OM!kp{^)(^j@Dg-oIPG>&J}^zros6`LRg)1fMa<Gb||Izd#4D&wgCvSkz)Z9o831B9<7$4UL(c16P_4YEWv>>Qbc+vkew?=Ypw(riWSiQEyd6|GEBRnz)KGq%Hm{*H&&u4Kmx-O5!}>j+z#--<iCYDoE?IsXWqze7egv0Oj+xL{S^{i73mOvUkZW98CJu+krwZY-~C+RkR?a=Ek3^0^6~2<XY6aT!;%Ij=D{wQVCH~_zD|f)AVNfhGb9##7{3&u@+uEr6)qUf7vkVsF82HLFtVD5<Twr*#2n1h3*hh%7jakZkvUlbd9w&3dz|2r6AZ^wO8n?0L``Whbg!gHG<HHovpahKV%FDlahDmlO|yY%xINDOWr<6g{;-{524x%Le=)()t!$hF55t6(Xy0Ou>})qQ5W_<YdtB+{VzdPZHqo{if5;lEzZf%kvl&iVp`V#44nHzM=uI~)ta3uXMjL$T^+ow)IU1O=#*g<0`<WP)3!E_Im=v$}aNx&sLhyJSG-a?bi){(}mEWj%W)o>7Unyui3++|iH0WmoglDobGQS@J4;f+aZZ_`K^v5!(5f&Jmp;7;phMOD0`_MPK*72TF6Tea75+l5e`bzaN{jql;7Zn*c7#L)QHoY2Qbz<ya!N;^;{1KZeM)!3m{F?5LSb+ob-JB5?YK!x?3^6&}0`ZzI8Wi$?ru25x`sj9Y9Q}YE3*S-Q(muL+_YFB}+bOrci*hZWlck`A`0@`FUwoh9qu<a$kN1?GT}%6NIw_{!2szR(G$ojYj`?mVv9brN(hhN)KqNcMAZ4&NXbQ%>5-H+7I-qv34`Tn};h`}flPd+-_@fn;{mH@or^X0wZlM9cvJlDdA<dY-sHmWuypMO2*}TUzFr|Y!$97Y3%2S#mZl{X=eN^+?b9(o>o4)dmz;f*%!AB#=uRB0)$422T8%DRDSj6Sy)gUfV7>e=N-J!kC$JtOlCbH!4c*enQTP-XgMtPqL_I*&}>m3&y{gIEU)nZIEv&E%42aJ(9fH#4K$2%<XRAvLAtRF6KVB`2q8#t91z-F5{lG7b9m&L-SNtO_&xFKStHH=z0*vnVqycHjro46Po6oQ}9C5ZdXN87D|m=@%T)WbY{-7)|V++}!U<A9<egW+T8g^oBe?3f(o&?iS-un@kn8Y~KxqJOaf6-*BM;iL<aqWS3FtwP;<K8E+_BW!{St<1mIzvf|Ar7uoh79daViYH4$FyB^&5v>Bqw1hD?Jg{|u5Ho)d$I==l-Z=|#QWcI%S_QlcTyQ8=kC-?Sj=>&-V)V$X5@FSId*o&6;Vcy6EQ8nYXZ5I+%8=9Pj1``Qqw76T!gIyj(E||ARpDxc7~d}+fbA_Rq>Ys@+8cnBD_(e1CPZfT0K8;!exuqIR|Nwx`Mo<fRB#dF4oo+Yq4K>Ww!PQlG?N35D;PdW0ugWGjB8|u7x$zjuNY2Mu`@~Yc^Or1Os1U){`BF316?u=qV7{7dLPOq!!i$gV@}jir6j()fXbRpNEfE0_?ts0^mjGAZ*-v-+nlJYi${(@YFakggQhp8Q>@WKqUW>dT|1FsjGDN`;k0MnC_2$JoR<E%gFHJn(!qH@P@3=eR2Mjcd{nbZQj$nL!=}@Tq4{)iVm4VF-%5`Bh17f}iMH8KC02G4U0pwpnwHO?XzwVp6)Yx$xpCAaoIu9maWr>+JVmrIv23&>tHuag@W%|=S(-(+w*5fT%+)j}DU#wwy3z@uH;wYWqJNfMs&@`;)J;59uHR%`7<y;)2~GDOUqcKSb4=psmA8BKk;VD?h8gR%lcnvtf;)-&^4tu4{qttssdXLNMnzKaRL)x6{%wW&p4_RreO@>9;TJgidq11%?+zAY{%bpgdl+I|ivaDB)?hzqq#4l)^p54B^4&+el%l~@FK;+UTO;U}4(HnRm_1UC6~lC(g@dry+Yg#Wfne!F5qU|08$|)g%?m`@2?6G93&HT2K42&Ca89K~ahM!Qvw0wq7N(~~xMR-61$zxjr@JC5)d>@ph#=eJg!mDSbeO5jlWN?s^!}f8vfUfatE8}AT}Ps{U|jz$7^h6F@bPXi>NbR9F+_Og;SEXCQ2fL7#ZyL8W+p?>S*XI31W&XrAw2F8z_7^&DF^&<z>SYD!9n2Edciu-1$mS7czf3!I||&e%R<9wLX6EBu4p;s0p+i*$nGws`CH}KGSLN>)j6~*UxLT8TwxTtks_1anDZ&HHRBHXWO30_twAWu1e&kbNGTkEh*fSdD09R^NhA!7q>zVkVX}QN+Ap|aXR`oeMrU1fdC+Z>!)UTEdaiMhdL$SwZr;%EcE$Ow031B*fl#R{qN9D8dt&;NpNgnH#~G^2c5vvNO9hS+xGoW3^VU%mG|mzy*&_5s?WbkOztXLV-Z=01nhJVa$>>BVqWV}!JJ3P%t_;SUR2zJfcF_IBgAi|L3HGjDs+dT)XlII(;ZFF;JQ%Nq*4SjALfKJ&B-isH&Go@&Cm*bh5@OdN0iO1Wp;R9y@sc(wtTh9xd=VAS`AS)z9dO=bFy$|<p*Ipc7&YWm?Zt<rDo~*F>3u5X+@oawK#UD;r^avP)NsoWS?WHje078_Fn!m$<OUia_y<XTmZR`W6J6*s!ph|;m@hL$i(Z1Br~oLv-SF+30GZV?B#)56D#09|Hn<>sa2aKF)zFt5TWorfN8TMbsO23SOREzoe#jM?aF>T=tw*T3`5xtp0`c$K{D^z><jg?Su4$#piI*t!l@8Uro9R=*M)F)PhF8~3%DBCX&RW`|HnD>8rhcFSA_aa^8^My{a`BrWyiAoMa|avyqqxZ3E{3k`4Xujg!zHbWD$5VjL`GwCl5dmn^>~U<8iB9bN4-&5ROcW?+4%ERX_QL~n7+3%?<Qq+Y$c!dO5BaRLD^5|(aHy0d_Gh{Dusd)nfe)3Uq~6T(PUr9!lmr9^q{((E_ch2Wy?Z&nGApb6M*6-8M-@J*z9J98K+$^*6$rvS-POat&e)o9V3$nDPq37CUt?9%v+4`GNpv3Ee)el&OEf5m(a*HqbWOC3gf9KY3QIq)M?M)@BIgLo%5pa0<GXS=?F!fb)ddapJ?TogXDW<Eory($KySBh$UrX<9a!ER9nI3G*h2S{W1G0;|n<$ao!1A`Uw!R!UEU)g$OJ%LFTcibmplDwe8)MH6x$gZMb++S4W$p*ODQ*pux17R+y(ycP$4SzAK~WYv$5yUwiQNH|S$~Dh2eiai!=6aXND-BiIbZB{g(m-xD%j!Nb@iUrA*t$A3&zIIVEPz2Xpv7#?qO6=BD5cZ}#1;BJi=R@2>a<`+5gTJ3P@JR5ro8Qj;HAZzby@){|H>lg!+F1Sf2N+npF*+Yr)Gqm#^Q%@^9NT*>iI3)lpw~JQ(a)YjI;Gy52pJ~#qMv_i-#SRlAyg1+ldAI^4a(n#xAqb-ay)k1gqkV%gr2M1C7XuOYSZa|o&I1N48O9#a;z*GaCx^P=eu)wz96ez6mm?grgs^_5g4aw(bjI7F<(>is-gX#gz{UlR0!x3g!F69N2wC1(mT!-@u?`sAp@Mg<11yZ?$XEH|CDZqCoq|!`&ldr)0vzodfQQUmW(@Ntxwt9}rg9ZH<wCqQ(qYdEcZgfvFzKxqtj^dW`3VbQt9_s?=VIPyGgR03q3O675AzwU_xVF7R-&?qgF&bLF*d{(IbZ(GRes28_CehxCmfvbkK+O_WcP4z)J21gB2Vn!&p|-F1}-`YE<Uxz!psmnTI+^BwKa6j0T|)SL(CQnc&Svlal{S=A;wr97)9mPj?~7BrYzGn)ZUy)EgAvUJZ961XV>%%v-}vo%%-<5f2P4>66pJ3G78ZPDL}y?^KAySVa$F~Z%?B=#&hWNzAXCin-6XJ-j5EnswjWfe9B(Fm887Q)bwcst+Pv|c;`iQgyGuwWtsHxeia4QZz08pQaUEwLKj}eF#W<Z3jDB;8tSuXbNy|qpSqp$ZGR`7Ku?b<VyW>LDOK!=p%qQhWOQ^Vty?~ek|S4A7Nyc(iNokc$1u`P2%{qhd-WH_7?Rt#t@Nk+bUL^tm*)5$q#}#0R9Y28>yrg^Hxo1~@TBewJ5jGo&(Q_6ifGF9mpTj4)lfk=TUVaAU-zn_Pv75eH$}NGB4>$Sv-Bs|(CP*0p#|d-LjV0X_&*y}^Gq9a@$&!s|5}ab<k0^DP)h>@EdT%j2mk;8ApojmI%og?|NsC0|NjpF6aZ{*V{3C?a$#g?X)bPXc>w?r0Ehqp00000089V?000000GsR=>KhQr$WX>mt)7xvoLr=CrJ!z;W}>d6pq`drR8o|f7oT60k_r-cOUx-w1&SAEBo?Fs`5Hz#nmP)#3KRe?d&g`$-7Et;08mQ-0xbhA000080000X0Q(S+{bvCH01g8H01N;C00000000000Du7i0001OWprU=VRT_GZg6=}O928c11$gm00;m803iT_ETF?ywEzHs!vFvb00000000000001h0i^)|0Ap`#Z*pIEF)nU!c~DCM0xbhA000080000X0N_(5?WzC(0N?-s01N;C00000000000Du8Gw*UZRZ)|UJUt%#XZg6=}O928c11$gm00;m803iU32;+A~0{{Sk0{{RF00000000000001h0U@{m0Ap`#Z*pIEGA?d#c~DCM0xbhA000080000X0Av{3KV$#^0H6Q>01N;C00000000000Du9dy8r-VZ)|UJUt%&YZg6=}O928c11$gm00;m803iT23Pcr50001>0000G00000000000001h0Y|(50Ap`#Z*pH@V{~bDWiD=Tc~DCM0xbhA000080000X0CU(`bFwi20DwUN01*HH00000000000DuA2yZ``WZ)|UJUt?u%bY*gLE^csnP)h*<Edwn8000O8001EXiv~{sV*mgEpa1{>4*&oF0000000000fC0+x003ieY;SU3a$#g?X)bPXc~DCM0xbhA000080000X02D%PV~Vu^0D!{)01N;C00000000000Du8*?*IU7Z)0n7Uw1JsZg6=}O928c11$gm00;m803iTrMo=!R0002s0000C00000000000001h0XC%p0Bmn#Yja;>F)nU!c~DCM0xbhA000080000X0DvBq#1jDk06+l%01N;C00000000000Du7>rU3wKZ)0n7Uw1MtZg6=}O928c11$gm00;m803iT~`rIr`0001p0000C00000000000001h0dS`Q0Bmn#Yja;>GA?d#c~DCM0xbhA000080000X0DL}@97+HH0Ehqp01yBG00000000000DuAdrvU(LZ)0n7Utwc(X?A5UZg6=}O928c11$gm00;m803iSnvHTvh4gdha5C8xX00000000000001h0fwjn0Bmn#Yja;?Wo~q3a&s<jaCuNm0Rk-pEdT%j2mk;8ApojmI%iA(004*p000jF0000000000004jie76ArY;R+0b6;{{WNB$GZg6=}O9ci10000F01p6u0{{RCxB&nF00'

VISUAL_AI_MODEL=None

VISUAL_AI_LOCAL=threading.local()

VISUAL_FEATURES=520

def visual_board_tiles(image):
    w,h=image.size
    return [image.crop((round(c*w/6),round(r*h/5),round((c+1)*w/6),round((r+1)*h/5))) for r in range(5) for c in range(6)]

def visual_features(tiles):
    import cv2
    batch=np.asarray([np.asarray(tile.convert('RGB').resize((12,12),Image.Resampling.BILINEAR),dtype=np.float32)/255 for tile in tiles],dtype=np.float32)
    hue,sat,val=rgb_to_hsv_np((batch*255).astype(np.uint8))
    features=[]
    for i,rgb in enumerate(batch):
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
        edge=cv2.resize(np.abs(cv2.Laplacian(gray,cv2.CV_32F)),(8,8)).clip(0,1)
        hist=np.histogram(hue[i][sat[i]>.25],bins=24,range=(0,360))[0].astype(np.float32);hist/=max(1,hist.sum())
        features.append(np.concatenate((rgb.ravel(),edge.ravel(),hist)))
    return np.asarray(features,dtype=np.float32)

def visual_head_predict(head,features,threshold=.985):
    hidden=np.tanh((features*2-1)@head['w1']+head['b1'])
    logits=hidden@head['w2']+head['b2'];logits[:,head['active']==0]=-1e6
    logits-=logits.max(axis=1,keepdims=True);prob=np.exp(logits);prob/=prob.sum(axis=1,keepdims=True)
    predicted=prob.argmax(axis=1);confidence=prob.max(axis=1)
    distances=np.sqrt(np.mean((features-head['centers'][predicted])**2,axis=1))
    accepted=(confidence>=threshold)&(distances<=np.maximum(.08,head['radii'][predicted]*1.25))
    return predicted,confidence,accepted

def visual_model_bytes(model):
    import io
    payload={'metadata':np.frombuffer(json.dumps(model['metadata'],ensure_ascii=False).encode('utf-8'),dtype=np.uint8)}
    for name,head in model['heads'].items():
        for key,value in head.items():payload[name+'_'+key]=np.asarray(value,dtype=np.float32)
    stream=io.BytesIO();np.savez_compressed(stream,**payload);return stream.getvalue()

def visual_model_load(data):
    import io
    if len(data)>2_000_000:raise ValueError('모델 파일이 너무 큽니다.')
    with np.load(io.BytesIO(data),allow_pickle=False) as archive:
        metadata=json.loads(archive['metadata'].tobytes().decode('utf-8'))
        if metadata.get('schema')!=1 or metadata.get('features')!=VISUAL_FEATURES:raise ValueError('지원하지 않는 이미지 모델입니다.')
        heads={}
        for name in ('color','spikes','locks'):
            if name+'_w1' not in archive:continue
            k=8 if name=='color' else 2
            shapes={'w1':(520,24),'b1':(24,),'w2':(24,k),'b2':(k,),'active':(k,),'centers':(k,520),'radii':(k,)}
            head={key:np.asarray(archive[name+'_'+key],dtype=np.float32) for key in shapes}
            if any(value.shape!=shapes[key] or not np.isfinite(value).all() for key,value in head.items()):raise ValueError('모델 가중치가 올바르지 않습니다.')
            if head['active'].sum()<2:raise ValueError('학습된 종류가 부족합니다.')
            heads[name]=head
    if not heads:raise ValueError('학습된 모델이 없습니다.')
    return dict(metadata=metadata,heads=heads)

def visual_infer(image):
    model=VISUAL_AI_MODEL
    if model is None:return None
    cached=getattr(VISUAL_AI_LOCAL,'cache',None)
    if cached and cached[0] is image and cached[1] is model:return cached[2]
    started=time.perf_counter();features=visual_features(visual_board_tiles(image));result={}
    for name,head in model['heads'].items():
        predicted,confidence,accepted=visual_head_predict(head,features,.985 if name=='color' else .99)
        result[name]=[int(value) if accept else None for value,accept in zip(predicted,accepted)]
        result[name+'_confidence']=[float(v) for v in confidence]
    result.update(trained_poison=bool(model['heads'].get('color',{}).get('active',np.zeros(8))[7]),model_version=model['metadata']['id'],inference_ms=(time.perf_counter()-started)*1000)
    VISUAL_AI_LOCAL.cache=(image,model,result)
    return result

def visual_apply_colors(image,board):
    result=visual_infer(image)
    if result is None:return board
    updated=list(board);conflicts=[];applied=0
    for i,value in enumerate(result.get('color',[None]*30)):
        if value is None:continue
        predicted=COLOR_ORDER[value]
        # A poison class absent from the validation set must not erase a known hazard.
        if board[i]=='S' and not result.get('trained_poison',False):continue
        if predicted!=board[i]:conflicts.append(i)
        updated[i]=predicted;applied+=1
    result.update(used=applied>0,accepted_cells=applied,conflicts=conflicts,board=list(updated))
    return updated

def visual_fit_head(train_x,train_y,k,cancel=None,seed=76,epochs=160):
    rng=np.random.default_rng(seed);x=train_x*2-1;n=len(train_y)
    counts=np.bincount(train_y,minlength=k);active=(counts>=4).astype(np.float32)
    if active.sum()<2:raise ValueError('학습할 정답 종류가 부족합니다.')
    w1=rng.normal(0,.06,(520,24)).astype(np.float32);b1=np.zeros(24,np.float32)
    w2=rng.normal(0,.1,(24,k)).astype(np.float32);b2=np.zeros(k,np.float32)
    params=[w1,b1,w2,b2];first=[np.zeros_like(p) for p in params];second=[np.zeros_like(p) for p in params]
    weights=(n/np.maximum(1,counts*k))[train_y].clip(.25,4);target=np.eye(k,dtype=np.float32)[train_y]
    for epoch in range(1,epochs+1):
        if cancel is not None and cancel.is_set():raise RuntimeError('학습을 중지했습니다.')
        hidden=np.tanh(x@w1+b1);logits=hidden@w2+b2;logits[:,active==0]=-1e6
        logits-=logits.max(axis=1,keepdims=True);prob=np.exp(logits);prob/=prob.sum(axis=1,keepdims=True)
        grad=(prob-target)*weights[:,None]/n;back=(grad@w2.T)*(1-hidden**2)
        gradients=[x.T@back+1e-5*w1,back.sum(axis=0),hidden.T@grad+1e-5*w2,grad.sum(axis=0)]
        for i,(parameter,g) in enumerate(zip(params,gradients)):
            first[i]*=.9;first[i]+=.1*g;second[i]*=.999;second[i]+=.001*g*g
            parameter-=.008*(first[i]/(1-.9**epoch))/(np.sqrt(second[i]/(1-.999**epoch))+1e-7)
    centers=np.zeros((k,520),np.float32);radii=np.zeros(k,np.float32)
    for label in range(k):
        if counts[label]:
            rows=train_x[train_y==label];centers[label]=rows.mean(axis=0)
            radii[label]=max(.06,float(np.quantile(np.sqrt(np.mean((rows-centers[label])**2,axis=1)),.98)))
    return dict(w1=w1,b1=b1,w2=w2,b2=b2,active=active,centers=centers,radii=radii)

def visual_validate_head(head,features,labels,name,baseline=None):
    predicted,confidence,accepted=visual_head_predict(head,features,.985 if name=='color' else .99)
    accuracy=float(np.mean(predicted==labels));coverage=float(accepted.mean());errors=int(np.sum(accepted&(predicted!=labels)))
    classes=set(map(int,labels));per_class={str(label):float(np.mean(predicted[labels==label]==label)) for label in classes}
    approved=accuracy>=.95 and errors==0 and coverage>=.25 and min(per_class.values())>=.8 and len(classes)>=2
    if baseline is not None:
        old,_,_=visual_head_predict(baseline,features,.985 if name=='color' else .99)
        approved=approved and accuracy>=float(np.mean(old==labels))
    return dict(approved=bool(approved),accuracy=accuracy,coverage=coverage,accepted_errors=errors,validation_cells=len(labels),per_class=per_class)

AI_REVIEW_DATA_LOCK=threading.RLock()

def ai_review_data_locked(function):
    """Serialize local record snapshots, edits and destructive retention."""
    from functools import wraps
    @wraps(function)
    def guarded(*args,**kwargs):
        with AI_REVIEW_DATA_LOCK:return function(*args,**kwargs)
    return guarded

def _ai_case_folder_safe(root,folder):
    root=Path(root);folder=Path(folder)
    if (root.is_symlink() or folder.is_symlink() or not folder.is_dir()
            or not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{32}',folder.name)):
        return False
    if getattr(root,'is_junction',lambda:False)() or getattr(folder,'is_junction',lambda:False)():return False
    try:
        return folder.parent.resolve()==root.resolve() and all(not p.is_symlink() and not getattr(p,'is_junction',lambda:False)() for p in folder.rglob('*'))
    except OSError:return False

def _visual_training_case(root,folder):
    """A readable, approved original; malformed/ineligible records are retained."""
    folder=Path(folder)
    if not _ai_case_folder_safe(root,folder):return None
    record=_load_json_dict(folder/'record.json');feedback=_load_json_dict(folder/'feedback.json')
    proposal=record.get('proposal')
    if not isinstance(proposal,dict) or not isinstance(proposal.get('board'),list):return None
    if record.get('id')!=folder.name or not ai_review_has_board(folder,record):return None
    if (feedback.get('human_confirmed') is not True or feedback.get('status') not in ('confirmed','corrected')
            or feedback.get('image_training_eligible') is not True):return None
    labels=feedback.get('labels') or {}
    if not isinstance(labels,dict):return None
    board=labels.get('board',[])
    if not isinstance(board,list) or len(board)!=30 or any(code not in COLOR_ORDER for code in board):return None
    try:visual_labels={name:list(labels.get(name,[None]*30)) for name in ('board','spikes','locks')}
    except TypeError:return None
    if any(len(visual_labels[name])!=30 or any(value is not None and type(value) is not bool for value in visual_labels[name]) for name in ('spikes','locks')):return None
    try:
        with Image.open(folder/'board.png') as original:image=original.convert('RGB')
        digest=hashlib.sha256(image.tobytes()).hexdigest()
        version=_ai_case_version(folder)
    except (OSError,ValueError):return None
    return dict(id=folder.name,folder=folder,record=record,feedback=feedback,image=image,
        labels=visual_labels,effective_labels=visual_labels,digest=digest,
        group=hashlib.sha256(''.join(board).encode()).hexdigest(),contributors=[],source_version=version)

def _ai_case_version(folder):
    """Small stable edit fingerprint plus the original image's file identity."""
    folder=Path(folder);board=folder/'board.png';info=board.stat()
    return (hashlib.sha256((folder/'record.json').read_bytes()).hexdigest(),
            hashlib.sha256((folder/'feedback.json').read_bytes()).hexdigest(),
            info.st_size,info.st_mtime_ns,getattr(info,'st_ino',None))

@ai_review_data_locked
def visual_training_cases(root,limit=100):
    """The exact bounded training snapshot, with original identity and sources."""
    records=[];seen={};limit=max(0,int(limit))
    for path in sorted(Path(root).glob('*/record.json'),reverse=True):
        if len(records)>=limit:break
        case=_visual_training_case(root,path.parent)
        if case is None:continue
        visual_labels=case['labels'];digest=case['digest']
        if digest in seen:
            previous=seen[digest]
            for name in visual_labels:
                for i,value in enumerate(visual_labels[name]):
                    old=previous['labels'][name][i]
                    if old is not None and value is not None and old!=value:
                        raise ValueError('같은 원본에 서로 다른 시각 정답이 있습니다. AI 관리에서 정답을 통일해 주세요.')
                    if value is not None:previous['labels'][name][i]=value
            previous['contributors'].append({key:value for key,value in case.items() if key not in ('image','contributors')})
            continue
        seen[digest]=case;records.append(case)
    return records

def visual_training_data(root):
    return [(case['group'],case['image'],case['labels']) for case in visual_training_cases(root)]

def _ai_case_retention_contents_safe(folder):
    for path in Path(folder).iterdir():
        if not path.is_file() or path.is_symlink():return False
        if path.name not in ('record.json','feedback.json','board.png','game.png') and not re.fullmatch(r'feedback_[0-9a-f]{32}\.json',path.name):return False
    return True

def _persist_training_case_labels(case):
    """Save only merged labels that were explicitly approved on identical pixels."""
    previous=case['feedback'];labels=json.loads(json.dumps(previous.get('labels') or {}))
    for name,values in case['effective_labels'].items():labels[name]=list(values)
    if labels==previous.get('labels'):return
    updated=json.loads(json.dumps(previous));updated['labels']=labels;updated['status']='corrected'
    updated['retention_merge']=dict(method='human_confirmed_identical_pixels',
        sources=[dict(id=row['id'],confirmed_at=row['feedback'].get('confirmed_at'),
            status=row['feedback'].get('status'),labels=row['feedback'].get('labels'))
            for row in case['contributors']],at=time.time())
    folder=case['folder']
    _atomic_json_write(folder/('feedback_'+uuid.uuid4().hex+'.json'),updated)
    _atomic_json_write(folder/'feedback.json',updated)
    case['feedback']=updated

@ai_review_data_locked
def ai_prune_training_cases(root,limit=100,cancel=None,protected_ids=()):
    """After a validated model is saved, retain the same next-training dataset."""
    root=Path(root)
    result=dict(kept=0,deleted=0,preserved_open=0,preserved_other=0,cancelled=False)
    if cancel is not None and cancel.is_set():return dict(result,cancelled=True)
    selected=visual_training_cases(root,limit);keep={row['id'] for row in selected};result['kept']=len(selected)
    if not selected:return result
    candidates=[]
    for path in sorted(root.glob('*/record.json'),reverse=True):
        if cancel is not None and cancel.is_set():return dict(result,cancelled=True)
        if path.parent.name in keep:continue
        case=_visual_training_case(root,path.parent)
        if case is None:continue
        if not _ai_case_retention_contents_safe(case['folder']):result['preserved_other']+=1;continue
        candidates.append({key:value for key,value in case.items() if key not in ('image','contributors')})
    # Preserve shared-update progress before old source IDs cease to exist.
    ai_record_share_changes(root.parent)
    for case in selected:
        if cancel is not None and cancel.is_set():return dict(result,cancelled=True)
        if _ai_case_version(case['folder'])!=case['source_version']:
            raise ValueError('정리 중 정답이 바뀌어 원본 정리를 보류했습니다.')
        _persist_training_case_labels(case)
    keep_versions={row['id']:_ai_case_version(row['folder']) for row in selected}
    for case in candidates:
        if cancel is not None and cancel.is_set():return dict(result,cancelled=True)
        protected=set(protected_ids() if callable(protected_ids) else protected_ids)
        if case['id'] in protected:result['preserved_open']+=1;continue
        # Never delete against an externally changed or missing retained set.
        if any(_ai_case_version(root/cid)!=version for cid,version in keep_versions.items()):
            raise ValueError('보관할 정답이 바뀌어 오래된 원본 정리를 보류했습니다.')
        folder=case['folder']
        if not _ai_case_folder_safe(root,folder) or not _ai_case_retention_contents_safe(folder):
            result['preserved_other']+=1;continue
        if _ai_case_version(folder)!=case['source_version']:
            result['preserved_other']+=1;continue
        shutil.rmtree(folder);result['deleted']+=1
    return result

def visual_train_confirmed(root,current,cancel=None):
    from PIL import ImageEnhance,ImageFilter
    records=visual_training_data(root)
    groups=sorted({row[0] for row in records})
    if len(groups)<5:raise ValueError(f'확인된 서로 다른 판 {len(groups)}/5개 · 원본 이미지가 있는 서로 다른 드롭 배치 정답이 최소 5개 필요합니다.')
    # Freeze complete image groups; augmentations of a held-out board never enter training.
    heldout={group for i,group in enumerate(groups) if i%5==0}
    validation=[row for row in records if row[0] in heldout];training=[row for row in records if row[0] not in heldout]
    rng=np.random.default_rng(76);train={name:[[],[]] for name in ('color','spikes','locks')};valid={name:[[],[]] for name in train}
    for group,output,augment in ((training,train,6),(validation,valid,1)):
        for digest,image,labels in group:
            if cancel is not None and cancel.is_set():raise RuntimeError('학습을 중지했습니다.')
            for iteration in range(augment):
                view=image if iteration==0 else ImageEnhance.Brightness(image).enhance(float(rng.uniform(.85,1.15)))
                features=visual_features(visual_board_tiles(view))
                for name in output:
                    values=[COLOR_ORDER.index(c) for c in labels['board']] if name=='color' else labels.get(name,[None]*30)
                    if len(values)!=30:continue
                    for f,value in zip(features,values):
                        if value is not None:output[name][0].append(f);output[name][1].append(int(value))
    heads=dict(current['heads']);reports={};approved=[]
    for name in train:
        x=np.asarray(train[name][0],np.float32);y=np.asarray(train[name][1],np.int64);vx=np.asarray(valid[name][0],np.float32);vy=np.asarray(valid[name][1],np.int64)
        k=8 if name=='color' else 2
        counts=np.bincount(y,minlength=k) if len(y) else np.zeros(k)
        if len(vy)<12 or np.sum(counts>=4)<2 or len(set(map(int,vy)))<2:reports[name]={'approved':False,'reason':'독립 검증 정답/양성·음성 부족'};continue
        head=visual_fit_head(x,y,k,cancel)
        report=visual_validate_head(head,vx,vy,name,current['heads'].get(name));reports[name]=report
        if report['approved']:heads[name]=head;approved.append(name)
    if not approved:raise ValueError('검증 기준을 통과하지 못했습니다. 현재 모델을 유지합니다. '+json.dumps(reports,ensure_ascii=False))
    metadata=dict(schema=1,features=520,id='local-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6],source='human_confirmed_local',training_boards=len(groups)-len(heldout),validation_boards=len(heldout),reports=reports,updated_heads=approved)
    return dict(metadata=metadata,heads=heads)

def classify_board(image: Image.Image) -> tuple[list[str], list[list[float]]]:
    arr = np.asarray(image.convert("RGB"))
    h, w, _ = arr.shape
    cw, ch = w / COLS, h / ROWS
    result: list[str] = []
    hues: list[list[float]] = []
    for r in range(ROWS):
        row_codes, row_hues = [], []
        for c in range(COLS):
            x0 = int(c * cw + cw * 0.20)
            x1 = int((c + 1) * cw - cw * 0.20)
            y0 = int(r * ch + ch * 0.20)
            y1 = int((r + 1) * ch - ch * 0.20)
            crop = arr[y0:y1, x0:x1]
            code, hue = classify_cell(crop)
            # A yellow padlock can win the center hue histogram (the uploaded
            # locked Wood at row 2/column 1 was read as Light). Require two
            # independent badge-free areas to agree before replacing yellow.
            if code == 'Y' and min(crop.shape[:2]) >= 12:
                right_code,right_hue=classify_cell(crop[:,int(crop.shape[1]*.23):])
                bottom_code,bottom_hue=classify_cell(crop[int(crop.shape[0]*.23):,:])
                if right_code==bottom_code and right_code in ('R','B','G','P','H'):
                    code,hue=right_code,bottom_hue
            # v72: re-evaluate suspicious blue/spike-like cores using a broader
            # cell crop. Dust-shaped jammer graphics are dark in the centre and
            # can resemble a blue orb with crossed blades at certain frames.
            if code == "B":
                wide = arr[int(r*ch+ch*0.10):int((r+1)*ch-ch*0.10),
                           int(c*cw+cw*0.10):int((c+1)*cw-cw*0.10)]
                if wide.size:
                    wide_code, _ = classify_cell(wide)
                    if wide_code == "J":
                        code = "J"
            row_hues.append(hue)
            row_codes.append(code)
        result.extend(row_codes)
        hues.append(row_hues)
    result=visual_apply_colors(image,result)
    return result, hues

def cloud_cell_mask(image: Image.Image) -> list[bool]:
    """Mark opaque white cloud cells; the red duration digit is never a drop.

    Use broad pale coverage in several quadrants rather than white highlights
    in a normal orb or the small white face of a poison drop. This only locates
    unreadable cells; it does not infer their hidden colours.
    """
    arr=np.asarray(image.convert('RGB'))
    height,width=arr.shape[:2]
    if width<COLS*8 or height<ROWS*8:return [False]*(ROWS*COLS)
    result=[]
    for index in range(ROWS*COLS):
        row,col=divmod(index,COLS)
        tile=arr[round((row+.12)*height/ROWS):round((row+.88)*height/ROWS),
                 round((col+.12)*width/COLS):round((col+.88)*width/COLS)]
        _,sat,value=rgb_to_hsv_np(tile)
        pale=(sat<.22)&(value>.76)
        hh,ww=pale.shape
        quadrants=[float(pale[y0:y1,x0:x1].mean()) for y0,y1 in ((0,hh//2),(hh//2,hh))
                   for x0,x1 in ((0,ww//2),(ww//2,ww))]
        # Cloud borders can be translucent before the full white animation
        # settles. Require a broad white core as well as multiple quadrants;
        # a normal orb's narrow highlight must never become a hidden cell.
        core=pale[hh//4:hh-hh//4,ww//4:ww-ww//4]
        opaque=float(pale.mean())>=.64 and sum(score>=.48 for score in quadrants)>=3
        covered_core=float(core.mean())>=.80 and float(pale.mean())>=.38 and sum(score>=.28 for score in quadrants)>=3
        result.append(opaque or covered_core)
    return result

def dark_cell_mask(image: Image.Image) -> list[bool]:
    """Opaque black discs with a neutral circular rim, not purple/jammer art."""
    arr=np.asarray(image.convert('RGB'));height,width=arr.shape[:2]
    if width<48 or height<40:return [False]*30
    found=[]
    for index in range(30):
        row,col=divmod(index,6)
        tile=arr[round(row*height/5):round((row+1)*height/5),round(col*width/6):round((col+1)*width/6)]
        hh,ww=tile.shape[:2];yy,xx=np.mgrid[:hh,:ww]
        radius=((xx+.5-ww*.5)/(ww*.5))**2+((yy+.5-hh*.5)/(hh*.5))**2
        _,sat,value=rgb_to_hsv_np(tile)
        core=radius<.45**2;ring=(radius>.68**2)&(radius<.94**2)
        black=(value<.18)&(sat<.50)
        neutral=(sat<.30)&(value>.23)
        found.append(bool(core.any() and ring.any() and black[core].mean()>.72
                          and black[radius<.65**2].mean()>.65 and neutral[ring].mean()>.10
                          and float(value[ring].mean())>float(value[core].mean())+.045))
    return found

def obscured_cell_mask(image):
    return [cloud or dark for cloud,dark in zip(cloud_cell_mask(image),dark_cell_mask(image))]

def cloud_visible_board_valid(image,board,mask=None):
    """Validate a partly obscured live grid using only the visible orb cells.

    An all-white panel cannot pass this gate. Hidden cells may contain None;
    callers must reconstruct them before using any puzzle solver.
    """
    if len(board)!=ROWS*COLS:return False
    mask=obscured_cell_mask(image) if mask is None else list(mask)
    if len(mask)!=ROWS*COLS:return False
    width,height=image.size
    if min(width,height)<80 or not .95<=width/height<=1.5:return False
    visible=[index for index,hidden in enumerate(mask) if not hidden]
    if len(visible)<12 or any(board[index] not in COLOR_ORDER for index in visible):return False
    arr=np.asarray(image.convert('RGB'))
    hue,sat,value=rgb_to_hsv_np(arr)
    prototypes=np.asarray([COLOR_INFO[color][2] for color in COLOR_HUE_ORDER])
    offsets=((- .29,0),(.29,0),(0,-.29),(0,.29),(-.205,-.205),(.205,-.205),(-.205,.205),(.205,.205))
    texture=[];coverage=[];consistency=[];normal=0
    for index in visible:
        row,col=divmod(index,COLS)
        tile=arr[round(row*height/ROWS):round((row+1)*height/ROWS),
                 round(col*width/COLS):round((col+1)*width/COLS)]
        texture.append(float(tile.astype(np.float32).std(axis=(0,1)).mean()/255)>=.045)
        if board[index] not in COLOR_HUE_ORDER:continue
        normal+=1;votes=[]
        for dx,dy in offsets:
            x=min(width-1,round((col+.5+dx)*width/COLS));y=min(height-1,round((row+.5+dy)*height/ROWS))
            if sat[y,x]<.32 or value[y,x]<.24:continue
            distance=np.abs(prototypes-hue[y,x]);chosen=int(np.argmin(np.minimum(distance,360-distance)))
            votes.append(COLOR_HUE_ORDER[chosen])
        coverage.append(len(votes)/len(offsets))
        consistency.append(sum(code==board[index] for code in votes)/len(votes) if len(votes)>=3 else 0.0)
    return (normal>=8 and len({board[index] for index in visible if board[index] in COLOR_HUE_ORDER})>=3
            and float(np.mean(texture))>=.75 and float(np.mean(coverage))>=.45
            and float(np.mean(consistency))>=.80)

def _board_image_is_plausible(image: Image.Image, board=None) -> bool:
    """Quick reject for stale saved coordinates or a non-board MuMu screen."""
    try:
        w, h = image.size
        ratio = w / max(1, h)
        if not (0.88 <= ratio <= 1.52) or w < 240 or h < 190:
            return False
        if board is None:
            board, _ = classify_board(image)
        unique = set(board)
        normal_unique = unique.intersection(set(COLOR_HUE_ORDER))
        if len(unique) < 3 or len(normal_unique) < 2:
            return False
        # Sample central pixels in cells; a genuine board has saturated orb centers.
        arr = np.asarray(image.convert("RGB"))
        samples = []
        for r in range(ROWS):
            for c in range(COLS):
                cx = int((c + 0.5) * w / COLS)
                cy = int((r + 0.5) * h / ROWS)
                radius_x = max(2, int(w / COLS * 0.12))
                radius_y = max(2, int(h / ROWS * 0.12))
                tile = arr[max(0, cy-radius_y):min(h, cy+radius_y), max(0, cx-radius_x):min(w, cx+radius_x)]
                if tile.size:
                    samples.append(tile.reshape(-1, 3).mean(axis=0))
        if len(samples) < 20:
            return False
        sample_arr = np.asarray(samples, dtype=np.float32) / 255.0
        saturation_proxy = (sample_arr.max(axis=1) - sample_arr.min(axis=1)) / np.maximum(sample_arr.max(axis=1), 0.01)
        colorful = float(np.mean(saturation_proxy > 0.22))
        return colorful >= 0.32
    except Exception:
        return False

def live_board_grid_diagnostics(image, board):
    """Stricter display gate: 30 textured cells with consistent orb colors.

    A few colorful team portraits must not make a party/menu screen look like
    a board. Do not change the existing manual capture classifier here.
    """
    details={'passed':False,'plausible':_board_image_is_plausible(image,board)}
    if not details['plausible']:
        return dict(details,reason='board_size_colors_or_saturation')
    small = np.asarray(image.convert('RGB').resize((120, 100), Image.Resampling.BILINEAR))
    cells = small.reshape(ROWS, 20, COLS, 20, 3).transpose(0, 2, 1, 3, 4)
    textured = cells.astype(np.float32).std(axis=(2, 3)).mean(axis=2) / 255.0
    details['textured_fraction']=float(np.mean(textured >= .045))
    details['required_textured_fraction']=.75
    if details['textured_fraction'] < .75:
        return dict(details,reason='too_few_textured_cells')
    hue, saturation, value = rgb_to_hsv_np(small)
    prototypes = np.asarray([COLOR_INFO[color][2] for color in COLOR_HUE_ORDER])
    offsets = ((-.29, 0), (.29, 0), (0, -.29), (0, .29),
               (-.205, -.205), (.205, -.205), (-.205, .205), (.205, .205))
    consistency = []
    visible_consistency = []
    visible_samples = []
    for index, color in enumerate(board):
        if color in ('J', 'S'):
            continue  # Dark hazard art is not a normal colored orb ring.
        row, col = divmod(index, COLS)
        votes = [0] * len(prototypes)
        for dx, dy in offsets:
            x, y = round((col+.5+dx)*20), round((row+.5+dy)*20)
            if saturation[y, x] < .32 or value[y, x] < .24:
                continue
            distance = np.abs(prototypes-hue[y, x])
            chosen = int(np.argmin(np.minimum(distance, 360-distance)))
            votes[chosen] += 1
        valid = sum(votes)
        consistency.append(max(votes) / len(offsets))
        visible_samples.append(valid / len(offsets))
        visible_consistency.append(max(votes)/valid if valid >= 3 else 0.0)
    details.update(normal_cell_count=len(consistency),required_normal_cells=8,
                   color_consistency=float(np.mean(consistency)) if consistency else 0.0,
                   required_color_consistency=.65)
    # Pale highlights and the yellow lock hide some samples. Missing samples
    # are not conflicting colors: normalize by usable pixels, with coverage
    # and stronger agreement requirements so sparse menu art cannot pass.
    details.update(visible_color_consistency=float(np.mean(visible_consistency)) if visible_consistency else 0.0,
                   ring_sample_coverage=float(np.mean(visible_samples)) if visible_samples else 0.0)
    passed=len(consistency)>=8 and (details['color_consistency']>=.65 or
        (details['visible_color_consistency']>=.85 and details['ring_sample_coverage']>=.45))
    return dict(details,passed=passed,reason='accepted' if passed else 'orb_ring_color_consistency')

def _live_board_grid_is_visible(image, board):
    return live_board_grid_diagnostics(image,board)['passed']

AI_REVIEW_ACTIONS=('경로 계산','스킬 사용 후 재계산','6콤보 생존 이동','콤보로 턴 진행','자동 실행 보류','확인 필요')

def ai_board_image_valid(image):
    if not isinstance(image,Image.Image):return False
    try:
        if any(obscured_cell_mask(image)):return False
        board,_=classify_board(image)
        return _live_board_grid_is_visible(image,board)
    except (ValueError,OSError,TypeError):return False

@lru_cache(maxsize=512)
def _ai_board_file_valid(path,modified,size):
    try:
        with Image.open(path) as image:return ai_board_image_valid(image.convert('RGB'))
    except (OSError,ValueError):return False

def ai_review_has_board(folder,record):
    if len(record.get('proposal',{}).get('board',[]))!=30 or record.get('board_image')!='board.png':return False
    path=Path(folder)/'board.png'
    try:info=path.stat();return _ai_board_file_valid(str(path),info.st_mtime_ns,info.st_size)
    except OSError:return False

def ai_review_has_display(folder,record):
    if ai_review_has_board(folder,record):return True
    if record.get('board_image')!='board.png' or not record.get('obscured_cells'):return False
    board=record.get('proposal',{}).get('board',[])
    if len(board)!=30:return False
    try:
        with Image.open(Path(folder)/'board.png') as opened:image=opened.convert('RGB')
        mask=obscured_cell_mask(image)
        return any(mask) and cloud_visible_board_valid(image,board,mask)
    except (OSError,ValueError,TypeError):return False

def ai_share_case_fingerprint(case):
    """Stable learning content identity; reconfirm timestamps do not add data."""
    labels=case.get('effective_labels') or case.get('labels') or case.get('feedback',{}).get('labels') or {}
    if not isinstance(labels,dict):raise ValueError('시각 정답 형식이 올바르지 않습니다.')
    visual={key:list(labels.get(key,[None]*30)) for key in ('board','spikes','locks')}
    image=case.get('image')
    if image is None:
        with Image.open(Path(case['folder'])/'board.png') as source:image=source.convert('RGB')
    else:image=image.convert('RGB')
    digest=hashlib.sha256(f'{image.width}x{image.height}\n'.encode('ascii'))
    digest.update(image.tobytes())
    digest.update(json.dumps(visual,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))
    return digest.hexdigest()

def ai_record_share_changes(root,cases=None,force_current=False):
    """Retain small change counters before pruning sources, including thresholds >100."""
    root=Path(root)
    with AI_REVIEW_DATA_LOCK:
        state=_load_json_dict(root/'share_changes.json')
        known=dict(state['known']) if isinstance(state.get('known'),dict) else {}
        pending=dict(state['pending']) if isinstance(state.get('pending'),dict) else {}
        if force_current:known={}
        if cases is None:
            cases=[];case_root=root/'cases'
            for path in sorted(case_root.glob('*/record.json'),reverse=True):
                folder=path.parent
                if (not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{32}',folder.name)
                        or folder.is_symlink() or any((folder/name).is_symlink() for name in ('record.json','feedback.json','board.png'))):continue
                record=_load_json_dict(path);feedback=_load_json_dict(folder/'feedback.json')
                if record.get('id')!=folder.name:continue
                if feedback.get('status')=='unknown':
                    pending.pop(folder.name,None);known.pop(folder.name,None);continue
                labels=feedback.get('labels') or {}
                if not isinstance(labels,dict):continue
                board=labels.get('board',[])
                if (feedback.get('human_confirmed') is not True or feedback.get('status') not in ('confirmed','corrected')
                        or feedback.get('image_training_eligible') is not True or not isinstance(board,list) or len(board)!=30
                        or any(code not in COLOR_ORDER for code in board)):continue
                try:
                    if not ai_review_has_board(folder,record):continue
                    cases.append(dict(id=folder.name,folder=folder,feedback=feedback,labels=labels))
                except (OSError,ValueError,TypeError,AttributeError):continue
        for case in cases:
            try:
                cid=case['id']
                if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{32}',cid):continue
                fingerprint=ai_share_case_fingerprint(case)
            except (OSError,ValueError,TypeError,KeyError,AttributeError):continue
            if known.get(cid)!=fingerprint:pending[cid]=fingerprint
            known[cid]=fingerprint
        # Removed, acknowledged sources need no permanent ledger entry.
        known={cid:value for cid,value in known.items() if cid in pending or (root/'cases'/cid).is_dir()}
        state=dict(schema=1,known=known,pending=pending)
        _atomic_json_write(root/'share_changes.json',state)
        return state

def ai_ack_share_changes(root,captured):
    """Acknowledge this snapshot only; newer edits remain due for sharing."""
    root=Path(root)
    with AI_REVIEW_DATA_LOCK:
        state=_load_json_dict(root/'share_changes.json')
        pending=dict(state['pending']) if isinstance(state.get('pending'),dict) else {}
        known=dict(state['known']) if isinstance(state.get('known'),dict) else {}
        for cid,digest in captured.items():
            if pending.get(cid)==digest:pending.pop(cid,None)
        known={cid:value for cid,value in known.items() if cid in pending or (root/'cases'/cid).is_dir()}
        _atomic_json_write(root/'share_changes.json',dict(schema=1,known=known,pending=pending))

def ai_storage_guide_text(data_root):
    """Describe automatic retention; opening this guide never removes files."""
    return (
        '학습 성공 후 최근 정답 100개를 남깁니다.\n'
        '사람이 판단 맞음·수정 저장으로 확정한 유효 원본을 기록 생성 순서로 고릅니다. '
        '같은 원본은 중복을 제외하며 나뉘어 확인한 잠금·가시 정답도 합쳐 보존합니다. '
        '학습·검증과 모델 저장이 성공한 뒤, 그보다 오래된 유효 확정 기록 폴더를 자동 정리합니다.\n\n'
        '미확인·모르겠음·학습에 유효하지 않은 기록은 정리 대상이 아닙니다. '
        '학습 실패·중단·모델 저장 실패 때도 원본을 지우지 않습니다. '
        '관리창에 열어 둔 기록은 보호하고, 창을 닫은 뒤 다시 정리합니다. '
        '현재 모델과 이전 모델도 유지합니다.\n\n'
        '200건은 목록 표시 상한입니다.\n'
        '필터에 맞는 최근 200건을 표시하므로 미확인 기록을 포함한 전체 기록 수는 100개보다 많을 수 있습니다. '
        '100개는 유효한 확정 학습 자료에 적용하는 보관 기준입니다.\n\n'
        'GitHub에는 최근 학습 자료 전체 묶음을 압축해 공유합니다.\n'
        '업데이트 때 설정된 새 정답 수에 도달하면 최근 유효 정답 최대 100개와 원본 퍼즐판, '
        '기록·합쳐진 정답, 현재·이전 모델을 ZIP으로 만듭니다. 용량이 크면 여러 ZIP으로 나눕니다. '
        '새 전체 묶음의 업로드를 모두 확인한 뒤 오래된 앱 학습 업로드와 완료된 전송 ZIP을 정리합니다. '
        '업로드 실패·중단 시 기존 완료 자료와 재전송 ZIP은 남깁니다.\n\n'
        '새 정답 수는 작은 전송 기록에 별도로 남기므로, 원본을 100개로 정리해도 '
        '200개 같은 업로드 기준에 도달할 수 있습니다. 같은 정답을 다시 확인한 것만으로 새 자료 수가 늘지는 않습니다.\n\n'
        '공유 대상과 보관할 파일\n'
        '전체 게임 화면·개인 설정·적 아이콘 예시·토큰은 학습 ZIP에 넣지 않습니다. '
        '적 아이콘 확인 예시는 settings.json에 남습니다. '
        'ai_review/models의 현재·이전 모델, github_sync.json과 share_changes.json의 전송 상태, '
        'settings.json과 database는 계속 유지합니다. '
        '추가 파일이 섞였거나 정리 중 변경된 기록은 임의로 삭제하지 않습니다.\n\n'
        '더 오래 보관하고 싶은 자료가 있다면 학습 전에 사용자 데이터 폴더를 별도 위치에 백업하세요. '
        '검토가 끝난 debug 오류 ZIP은 학습 원본과 별도이므로 직접 정리할 수 있습니다. '
        '미완료 전송 ZIP은 자동 재시도에 필요하므로 직접 지우지 마세요.\n\n'
        '사용자 데이터 위치: '+str(data_root)
    )

def ai_review_list(root,mode='전체',limit=200):
    paths=sorted(Path(root).glob('*/record.json'),reverse=True);rows=[]
    for path in paths:
        record=_load_json_dict(path)
        if not record.get('id') or 'proposal' not in record or not ai_review_has_display(path.parent,record):continue
        feedback=_load_json_dict(path.parent/'feedback.json');status=feedback.get('status','pending')
        if mode=='미확인' and status!='pending':continue
        if mode=='확인 완료' and status not in ('confirmed','corrected'):continue
        rows.append((record['id'],status,record.get('created_at',0)))
        if len(rows)>=limit:break
    return rows,len(paths)

@ai_review_data_locked
def ai_review_write(root,proposal,board_image=None,game_image=None):
    """Local, immutable observations. Nothing is a training label by default."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    cid=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex
    folder=root/cid;folder.mkdir()
    record=json.loads(json.dumps(proposal));record.update(schema_version=1,id=cid,version=APP_VERSION,status='pending',created_at=time.time(),model_applied=bool(record.get('visual_model',{}).get('used')))
    for name,image in (('board',board_image),('game',game_image)):
        if image is not None:
            image.save(folder/(name+'.png'),format='PNG');record[name+'_image']=name+'.png'
    _atomic_json_write(folder/'record.json',record)
    return record

@ai_review_data_locked
def ai_review_feedback(root,cid,status,labels,note='',outcome='미확인'):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{32}',str(cid)):raise ValueError('검토 기록 번호가 올바르지 않습니다.')
    folder=Path(root)/cid;record=_load_json_dict(folder/'record.json')
    if record.get('id')!=cid:raise ValueError('검토 기록을 찾지 못했습니다.')
    if status not in ('confirmed','corrected','unknown'):raise ValueError('확인 상태가 올바르지 않습니다.')
    labels=json.loads(json.dumps(labels))
    board=labels.get('board',[])
    if board and (len(board)!=30 or any(code not in COLOR_ORDER for code in board)):raise ValueError('드롭 30칸을 확인해 주세요.')
    for field in ('spikes','locks'):
        values=labels.get(field,[])
        if board and (len(values)!=30 or any(value is not None and type(value) is not bool for value in values)):raise ValueError('가시·잠금 30칸을 확인해 주세요.')
    if 'clouds' in labels:
        values=labels['clouds']
        if not isinstance(values,list) or len(values)!=30 or any(value is not None and type(value) is not bool for value in values):raise ValueError('구름 30칸을 확인해 주세요.')
    if labels.get('goal_index') not in {i for i,_,_ in ROUTE_GOAL_OPTIONS}:raise ValueError('경로 조건을 선택해 주세요.')
    if labels.get('action') not in AI_REVIEW_ACTIONS:raise ValueError('행동을 선택해 주세요.')
    if status=='confirmed' and labels!=record.get('proposal'):status='corrected'
    feedback=dict(schema_version=1,status=status,labels=labels if status!='unknown' else None,note=str(note)[:4000],outcome=str(outcome)[:80],confirmed_at=time.time(),human_confirmed=status!='unknown',
                  image_training_eligible=bool(status!='unknown' and board and not any(labels.get('clouds',[])) and ai_review_has_board(folder,record)))
    # Preserve every explicit correction; feedback.json is just the current selection.
    _atomic_json_write(folder/('feedback_'+uuid.uuid4().hex+'.json'),feedback)
    _atomic_json_write(folder/'feedback.json',feedback)
    return feedback

AI_GITHUB_SYNC_LOCK=threading.Lock()

def ai_github_repo(value):
    value=str(value).strip().removeprefix('https://github.com/').rstrip('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',value):
        raise ValueError('GitHub 저장소는 소유자/저장소 형식으로 입력하세요.')
    return value

def ai_github_credential(repo,token=None):
    """Windows Credential Manager only; never serialize a token into settings."""
    import ctypes.wintypes as wt
    if sys.platform!='win32':raise RuntimeError('GitHub 인증 저장은 Windows 인증 정보 관리자를 사용합니다.')
    class Credential(ctypes.Structure):
        _fields_=[('Flags',wt.DWORD),('Type',wt.DWORD),('TargetName',wt.LPWSTR),('Comment',wt.LPWSTR),
            ('LastWritten',wt.FILETIME),('CredentialBlobSize',wt.DWORD),('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),
            ('Persist',wt.DWORD),('AttributeCount',wt.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',wt.LPWSTR),('UserName',wt.LPWSTR)]
    api=ctypes.WinDLL('Advapi32',use_last_error=True);target='Pad0Overlay/GitHub/'+ai_github_repo(repo).lower()
    api.CredReadW.argtypes=[wt.LPCWSTR,wt.DWORD,wt.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))];api.CredReadW.restype=wt.BOOL
    api.CredWriteW.argtypes=[ctypes.POINTER(Credential),wt.DWORD];api.CredWriteW.restype=wt.BOOL
    api.CredFree.argtypes=[ctypes.c_void_p];api.CredFree.restype=None
    if token is None:
        pointer=ctypes.POINTER(Credential)()
        if not api.CredReadW(target,1,0,ctypes.byref(pointer)):return ''
        try:return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
        finally:api.CredFree(pointer)
    raw=str(token).strip().encode('utf-8')
    if not 10<=len(raw)<=2500:raise ValueError('GitHub 토큰 형식을 확인하세요.')
    blob=ctypes.create_string_buffer(raw);value=Credential();value.Type=1;value.TargetName=target;value.UserName='PAD0';value.Persist=2
    value.CredentialBlobSize=len(raw);value.CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte))
    if not api.CredWriteW(ctypes.byref(value),0):raise RuntimeError('Windows 인증 정보 저장에 실패했습니다.')
    return True

def ai_github_token(repo):
    try:token=ai_github_credential(repo)
    except (OSError,RuntimeError):token=''
    if token:return token
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:return token.strip()
    executable=shutil.which('gh')
    if executable:
        try:
            result=subprocess.run([executable,'auth','token','--hostname','github.com'],capture_output=True,text=True,timeout=5,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode==0:return result.stdout.strip()
        except (OSError,subprocess.TimeoutExpired):pass
    return ''

def ai_confirmed_export_cases(root):
    """The same newest 100 distinct approved sources used by local training."""
    with AI_REVIEW_DATA_LOCK:
        cases=[];root=Path(root)
        for selected in visual_training_cases(root/'cases',limit=100):
            folder=selected['folder']
            files=[folder/name for name in ('record.json','feedback.json','board.png')]
            if any(path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()) for path in files):
                raise ValueError('학습 원본 경로를 확인하세요.')
            record=dict(selected['record']);record.pop('game_image',None)
            feedback=dict(selected['feedback']);labels=dict(feedback.get('labels') or {})
            labels.update(selected['effective_labels']);feedback['labels']=labels
            payloads={
                'record.json':json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode(),
                'feedback.json':json.dumps(feedback,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode(),
                'board.png':files[2].read_bytes(),
            }
            source_digest=hashlib.sha256()
            for path in files:source_digest.update(path.name.encode());source_digest.update(path.read_bytes())
            cases.append(dict(id=selected['id'],digest=ai_share_case_fingerprint(selected),
                files=files,payloads=payloads,source_digest=source_digest.hexdigest()))
        return cases

def ai_export_training(root,cases,version,max_bytes=24*1024*1024):
    """A complete recent training snapshot, split without separating one case."""
    import zipfile
    with AI_REVIEW_DATA_LOCK:
        root=Path(root);batch=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:10]
        folder=root/'exports'/batch;folder.mkdir(parents=True,exist_ok=False)
        groups=[dict(cases={case['id']:case['digest']},files=[('cases/'+case['id']+'/'+name,data) for name,data in case['payloads'].items()]) for case in cases]
        models=[root/'models'/name for name in ('visual_model.npz','visual_model_previous.npz')]
        for path in models:
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()):
                groups.append(dict(cases={},files=[('models/'+path.name,path.read_bytes())]))
        chunks=[];chunk=[];size=0
        for group in groups:
            estimate=sum(len(data)+len(name.encode())*2+128 for name,data in group['files'])+1024
            if estimate+4096>max_bytes:raise ValueError('한 자료가 ZIP 분할 상한보다 큽니다. 해당 원본 크기를 확인하세요.')
            if chunk and size+estimate+4096>max_bytes:chunks.append(chunk);chunk=[];size=0
            chunk.append(group);size+=estimate
        if chunk:chunks.append(chunk)
        if not chunks:raise ValueError('공유할 학습 자료가 없습니다.')
        result=[]
        for index,chunk in enumerate(chunks,1):
            path=folder/f'학습_{index:02}.zip';pending=path.with_suffix('.pending');case_map={k:v for group in chunk for k,v in group['cases'].items()}
            manifest=dict(schema=2,version=version,batch=batch,part=index,parts=len(chunks),cases=case_map,
                confirmed_only=True,complete_snapshot=True,retention_limit=100,total_cases=len(cases))
            with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                for group in chunk:
                    for name,data in group['files']:archive.writestr(name,data)
            if pending.stat().st_size>max_bytes:pending.unlink();raise ValueError('학습 ZIP 분할 크기 검증에 실패했습니다.')
            os.replace(pending,path)
            result.append(dict(path=str(path.relative_to(root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),cases=case_map))
        for case in cases:
            digest=hashlib.sha256()
            for source in case['files']:digest.update(source.name.encode());digest.update(source.read_bytes())
            if digest.hexdigest()!=case['source_digest']:raise ValueError('압축 중 정답이 바뀌었습니다. 최신 정답을 다음 실행에서 다시 준비합니다.')
        receipt=dict(schema=1,batch=batch,files=result,complete_snapshot=True)
        _atomic_json_write(folder/'snapshot.json',receipt)
        return dict(tag='training-'+version.replace('.','-')+'-'+batch,files=result,all_files=list(result),batch=batch,
            version=version,snapshot_cases={case['id']:case['digest'] for case in cases},complete_snapshot=True)

class GitHubLearningClient:
    """Private repository release assets; no training ZIPs in Git history."""
    def __init__(self,repo,token):self.repo=ai_github_repo(repo);self.token=token
    def request(self,method,path,payload=None,binary=False):
        import urllib.request,urllib.error,urllib.parse
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,request,fp,code,msg,headers,newurl):
                if urllib.parse.urlsplit(newurl).scheme!='https':raise urllib.error.URLError('HTTPS redirect required')
                redirected=super().redirect_request(request,fp,code,msg,headers,newurl)
                if redirected is not None and urllib.parse.urlsplit(request.full_url).hostname!=urllib.parse.urlsplit(newurl).hostname:
                    redirected.remove_header('Authorization')
                return redirected
        url=('https://uploads.github.com' if binary and method=='POST' else 'https://api.github.com')+path
        data=payload if binary and method=='POST' else json.dumps(payload).encode() if payload is not None else None
        headers={'Authorization':'Bearer '+self.token,'Accept':'application/octet-stream' if binary and method=='GET' else 'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'PAD0-learning/'+APP_VERSION}
        if data is not None:headers['Content-Type']='application/zip' if binary else 'application/json'
        request=urllib.request.Request(url,data=data,headers=headers,method=method)
        try:
            with urllib.request.build_opener(Redirect()).open(request,timeout=60) as response:
                value=response.read();return value if binary else json.loads(value or b'{}')
        except urllib.error.HTTPError as exc:
            if exc.code==404:return None
            raise RuntimeError('GitHub 요청 실패 (HTTP '+str(exc.code)+'). 저장소/토큰의 Contents 쓰기 권한을 확인하세요.') from None
        except (urllib.error.URLError,TimeoutError,OSError):raise RuntimeError('GitHub 연결에 실패했습니다. 미완료 자료는 다음 실행에서 이어 전송합니다.') from None
    def verify_private(self):
        data=self.request('GET','/repos/'+self.repo)
        if not data:raise RuntimeError('학습 저장소가 없습니다. 비공개 저장소를 README와 함께 만들고 연결하세요.')
        if data.get('private') is not True:raise RuntimeError('학습 자료는 연결한 비공개 저장소에만 전송합니다.')
        if data.get('permissions',{}).get('push') is False:raise RuntimeError('학습 저장소의 Contents 쓰기 권한이 필요합니다.')
    def release(self,tag):
        from urllib.parse import quote
        path='/repos/'+self.repo+'/releases';data=self.request('GET',path+'/tags/'+quote(tag,safe=''))
        if data is None:data=self.request('POST',path,dict(tag_name=tag,name='PAD0 학습 자료 '+tag,draft=False,prerelease=False,make_latest='false'))
        if not data or not isinstance(data.get('id'),int):raise RuntimeError('학습 자료 릴리스를 준비하지 못했습니다.')
        return data['id']
    def upload(self,release_id,name,data):
        from urllib.parse import quote
        prefix='/repos/'+self.repo+'/releases/'+str(int(release_id))+'/assets';digest='sha256:'+hashlib.sha256(data).hexdigest()
        for page in range(1,101):
            assets=self.request('GET',prefix+'?per_page=100&page='+str(page)) or []
            for asset in assets:
                if asset.get('name')==name:
                    if asset.get('digest')==digest:return
                    existing=self.request('GET','/repos/'+self.repo+'/releases/assets/'+str(int(asset['id'])),binary=True)
                    if isinstance(existing,bytes) and hashlib.sha256(existing).digest()==hashlib.sha256(data).digest():return
                    raise RuntimeError('같은 이름의 다른 업로드 파일이 있습니다. 기존 파일을 덮어쓰지 않았습니다.')
            if len(assets)<100:break
        else:raise RuntimeError('학습 릴리스의 파일 목록이 너무 큽니다.')
        uploaded=self.request('POST',prefix+'?name='+quote(name,safe=''),data,binary=True)
        # Upload endpoints return JSON even though their request body is binary.
        if isinstance(uploaded,bytes):
            try:uploaded=json.loads(uploaded)
            except ValueError:uploaded=None
        if not uploaded or uploaded.get('size')!=len(data) or uploaded.get('state')!='uploaded':raise RuntimeError('GitHub 파일 업로드 완료 응답을 확인하지 못했습니다.')

    def _snapshot_assets(self,release_id):
        result=[]
        for page in range(1,101):
            rows=self.request('GET','/repos/'+self.repo+'/releases/'+str(int(release_id))+'/assets?per_page=100&page='+str(page)) or []
            if not isinstance(rows,list):raise RuntimeError('학습 파일 목록을 확인하지 못했습니다.')
            result.extend(rows)
            if len(rows)<100:return result
        raise RuntimeError('학습 파일 목록이 너무 큽니다.')

    def cleanup_snapshots(self,keep_tag,files,cancel=None):
        """Verify a full replacement before removing completed, app-owned releases."""
        import io,zipfile
        from datetime import datetime
        from urllib.parse import quote
        cancel=cancel or threading.Event();prefix='/repos/'+self.repo+'/releases'
        release=self.request('GET',prefix+'/tags/'+quote(keep_tag,safe=''))
        if not release or release.get('name')!='PAD0 학습 자료 '+keep_tag:raise RuntimeError('새 학습 스냅샷을 확인하지 못했습니다.')
        assets=self._snapshot_assets(release['id']);by_name={row.get('name'):row for row in assets}
        expected={Path(row['path']).name:row['sha256'] for row in files}
        if not expected or len(expected)!=len(files):raise RuntimeError('학습 스냅샷 파일 구성을 확인하지 못했습니다.')
        for name,digest in expected.items():
            if cancel.is_set():raise RuntimeError('자료 정리 중지 · 다음 실행에서 이어 확인합니다.')
            asset=by_name.get(name)
            if not asset or asset.get('state')!='uploaded':raise RuntimeError('학습 스냅샷의 모든 ZIP 전송을 확인하지 못했습니다.')
            if asset.get('digest')!='sha256:'+digest:
                data=self.request('GET','/repos/'+self.repo+'/releases/assets/'+str(int(asset['id'])),binary=True)
                if not isinstance(data,bytes) or hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('학습 스냅샷 ZIP 검증에 실패했습니다.')
        marker='PAD0_MANAGED_TRAINING_SNAPSHOT\n'
        metadata=dict(schema=1,complete=True,tag=keep_tag,files=expected)
        body=marker+json.dumps(metadata,sort_keys=True)
        confirmed=self.request('PATCH',prefix+'/'+str(int(release['id'])),dict(body=body))
        if not confirmed or confirmed.get('id')!=release['id'] or confirmed.get('body')!=body:
            raise RuntimeError('새 학습 스냅샷의 완료 표시를 확인하지 못했습니다.')
        def created(value):
            try:
                result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
                return result if result.tzinfo is not None else None
            except ValueError:return None
        # GitHub created_at may be the target commit date shared by many tags;
        # published_at is the actual snapshot publication order.
        kept_at=created(release.get('published_at'))
        old=[]
        for page in range(1,101):
            rows=self.request('GET',prefix+'?per_page=100&page='+str(page)) or []
            if not isinstance(rows,list):raise RuntimeError('기존 학습 자료 목록을 확인하지 못했습니다.')
            old.extend(rows)
            if len(rows)<100:break
        else:raise RuntimeError('기존 학습 자료 목록이 너무 큽니다.')
        removed=0
        for row in old:
            if cancel.is_set():raise RuntimeError('자료 정리 중지 · 다음 실행에서 이어 확인합니다.')
            tag=row.get('tag_name','')
            if (tag==keep_tag or row.get('name')!='PAD0 학습 자료 '+tag or row.get('draft')
                    or not re.fullmatch(r'training-v\d+(?:-\d+)*-\d{8}-\d{6}-[0-9a-f]{10}',tag)):continue
            old_at=created(row.get('published_at'))
            if kept_at is None or old_at is None or old_at>=kept_at:continue
            parts=self._snapshot_assets(row['id'])
            if not parts or any(not re.fullmatch(r'학습_\d{2,}\.zip',part.get('name','')) or part.get('state')!='uploaded' for part in parts):continue
            complete=False;body=row.get('body') or ''
            if body.startswith(marker):
                try:
                    info=json.loads(body[len(marker):])
                    complete=info.get('complete') is True and info.get('tag')==tag and set(info.get('files',{}))=={part['name'] for part in parts}
                except (ValueError,TypeError):pass
            else:
                # Legacy releases have no marker. Their first ZIP manifest
                # proves ownership and how many uploaded parts form the set.
                first=min(parts,key=lambda part:part['name'])
                try:
                    data=self.request('GET','/repos/'+self.repo+'/releases/assets/'+str(int(first['id'])),binary=True)
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        info=json.loads(archive.read('manifest.json'))
                    count=int(info.get('parts',0));batch=info.get('batch','')
                    complete=(info.get('confirmed_only') is True and 0<count<=1000 and tag.endswith('-'+batch)
                        and tag=='training-'+str(info.get('version','')).replace('.','-')+'-'+batch
                        and {part['name'] for part in parts}=={f'학습_{i:02}.zip' for i in range(1,count+1)})
                except (ValueError,TypeError,KeyError,OSError,zipfile.BadZipFile):pass
            if not complete:continue
            self.request('DELETE',prefix+'/'+str(int(row['id'])));removed+=1
        return removed

def ai_validate_snapshot_metadata(root,item,cleanup=False):
    """State files never authorize paths outside one generated export folder."""
    root=Path(root);batch=item.get('batch','');tag=item.get('tag','')
    if (not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{10}',batch)
            or not re.fullmatch(r'training-v\d+(?:-\d+)*-'+re.escape(batch),tag)):
        raise ValueError('학습 스냅샷 번호를 확인하세요.')
    if cleanup and item.get('complete_snapshot') is not True:raise ValueError('전체 학습 스냅샷 완료 상태를 확인하세요.')
    exports=root/'exports';folder=exports/batch
    for path in (root,exports,folder):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('학습 ZIP 폴더 경로를 확인하세요.')
    for field in ('files','all_files'):
        if field not in item:continue
        rows=item[field]
        if not isinstance(rows,list) or cleanup and field=='files' and not rows:raise ValueError('학습 ZIP 목록을 확인하세요.')
        for row in rows:
            value=row.get('path','');path=Path(value)
            if (path.is_absolute() or len(path.parts)!=3 or path.parts[:2]!=('exports',batch)
                    or not re.fullmatch(r'학습_\d{2,}\.zip',path.name)
                    or not re.fullmatch(r'[0-9a-f]{64}',str(row.get('sha256','')))):
                raise ValueError('학습 ZIP 경로 또는 해시를 확인하세요.')
            target=root/path
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):raise ValueError('학습 ZIP 경로를 확인하세요.')
    return batch

def ai_cleanup_completed_exports(root,keep_batch,previous_uploaded=None,previous_tag=None):
    """Remove only provably completed local bundles, never retry payloads."""
    import zipfile
    root=Path(root);exports=root/'exports';removed=0;previous_uploaded=previous_uploaded or {}
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{10}',keep_batch):raise ValueError('유지할 학습 ZIP 번호를 확인하세요.')
    if (root.is_symlink() or getattr(root,'is_junction',lambda:False)() or not exports.is_dir()
            or exports.is_symlink() or getattr(exports,'is_junction',lambda:False)()):return 0
    for folder in exports.iterdir():
        if (folder.is_symlink() or getattr(folder,'is_junction',lambda:False)() or not folder.is_dir() or folder.name[:15]>=keep_batch[:15]
                or not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{10}',folder.name)):continue
        contents=list(folder.iterdir())
        if any(not path.is_file() or path.is_symlink() or (path.name not in ('snapshot.json','uploaded.json')
                and not re.fullmatch(r'학습_\d{2,}\.zip',path.name)) for path in contents):continue
        receipt=_load_json_dict(folder/'uploaded.json')
        complete=(receipt.get('schema')==1 and receipt.get('complete') is True and receipt.get('batch')==folder.name
            and re.fullmatch(r'training-v\d+(?:-\d+)*-'+re.escape(folder.name),str(receipt.get('tag',''))) is not None)
        if not complete:
            files=sorted(folder.glob('학습_*.zip'))
            if not files or any(file.is_symlink() for file in files):continue
            try:
                manifests=[]
                for file in files:
                    with zipfile.ZipFile(file) as archive:manifests.append(json.loads(archive.read('manifest.json')))
                count=len(files)
                owned=all(info.get('confirmed_only') is True and info.get('batch')==folder.name and info.get('parts')==count for info in manifests)
                all_acknowledged=all(info.get('cases') and all(previous_uploaded.get(cid)==digest for cid,digest in info['cases'].items()) for info in manifests)
                complete=owned and (bool(previous_tag and previous_tag.endswith('-'+folder.name)) or all_acknowledged)
            except (ValueError,TypeError,KeyError,OSError,zipfile.BadZipFile):continue
        if complete:
            # Never follow a nested link supplied by another program.
            if any(path.is_symlink() for path in folder.rglob('*')):continue
            shutil.rmtree(folder);removed+=1
    return removed

def ai_github_sync_on_update(root,repo,threshold=50,enabled=True,version=APP_VERSION,token_provider=None,client_factory=GitHubLearningClient,cancel=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);state_path=root/'github_sync.json'
    cancel=cancel or threading.Event()
    with AI_GITHUB_SYNC_LOCK:
        state=_load_json_dict(state_path);changed=state.get('seen_version')!=version;state['seen_version']=version
        def save():_atomic_json_write(state_path,state)
        if not enabled:save();return dict(status='disabled',message='업데이트 시 학습 자료 공유 꺼짐')
        repo=ai_github_repo(repo)
        # A token/repository change never acknowledges data sent elsewhere.
        repo_changed=state.get('repo')!=repo
        if repo_changed:
            pending_update=state.get('pending_update')
            state=dict(repo=repo,seen_version=version,uploaded={})
            state['pending_update']=version
        pending=state.get('pending')
        cleanup=state.get('cleanup_pending')
        if changed and ((pending and pending.get('version')!=version) or (cleanup and cleanup.get('version')!=version)):
            # Resuming yesterday's transfer must not consume this update's
            # opportunity to share newly reviewed material afterwards.
            state['resume_update']=version
        try:
            if pending:ai_validate_snapshot_metadata(root,pending)
            if cleanup:ai_validate_snapshot_metadata(root,cleanup,cleanup=True)
        except (ValueError,TypeError,AttributeError) as exc:
            state['last_error']=str(exc);save();return dict(status='error',message='학습 자료 상태 확인 필요 · '+str(exc))
        if not changed and not pending and not cleanup and state.get('pending_update')!=version:
            save();return dict(status='same_version',message='이번 버전의 자료 확인 완료 · 다음 업데이트 때 다시 확인합니다.')
        dirty={};new_count=0
        if not pending and not cleanup:
            journal=ai_record_share_changes(root,force_current=repo_changed);uploaded=state.get('uploaded',{})
            already={cid:digest for cid,digest in journal['pending'].items() if uploaded.get(cid)==digest}
            if already:ai_ack_share_changes(root,already)
            cases=ai_confirmed_export_cases(root)
            dirty={cid:digest for cid,digest in journal['pending'].items() if cid not in already}
            fresh={case['id']:case['digest'] for case in cases if uploaded.get(case['id'])!=case['digest']}
            new_count=len(set(dirty)|set(fresh))
            if not cases or new_count<max(1,int(threshold)) and not state.get('force_snapshot'):
                state.pop('pending_update',None);save()
                return dict(status='below_threshold',count=new_count,message=f'새 정답 {new_count}/{max(1,int(threshold))}건 · 다음 업데이트 때 확인합니다.')
            state['pending_update']=version;save()
        token=(token_provider or ai_github_token)(repo)
        if not token:state['last_error']='GitHub 연결 설정 필요';save();return dict(status='needs_auth',message='AI 관리의 공유 설정에서 GitHub를 한 번 연결하세요.')
        client=client_factory(repo,token)
        def finish_cleanup(item):
            """A failed cleanup retries without replacing or uploading the snapshot."""
            try:
                if cancel.is_set():raise RuntimeError('자료 정리를 중지했습니다.')
                remote=getattr(client,'cleanup_snapshots',None)
                if callable(remote):remote(item['tag'],item['files'],cancel=cancel)
                ai_validate_snapshot_metadata(root,item,cleanup=True)
                folder=root/'exports'/item['batch']
                if folder.is_dir() and not folder.is_symlink():
                    _atomic_json_write(folder/'uploaded.json',dict(schema=1,complete=True,batch=item['batch'],tag=item['tag']))
                ai_cleanup_completed_exports(root,item['batch'],item.get('previous_uploaded'),item.get('previous_tag'))
                ai_ack_share_changes(root,item.get('share_changes',{}))
                state.pop('cleanup_pending',None);state.pop('last_error',None)
                if state.get('resume_update')==version:
                    state['pending_update']=version;state.pop('resume_update',None)
                save()
                return dict(status='uploaded',count=item.get('new_count',0),snapshot_count=len(item.get('snapshot_cases',{})),
                    message=f"학습 자료 공유·이전 자료 정리 완료 · 최근 정답 {len(item.get('snapshot_cases',{}))}개 스냅샷")
            except Exception as exc:
                state['last_error']=str(exc);save()
                return dict(status='uploaded',count=item.get('new_count',0),cleanup_pending=True,
                    message='학습 자료 업로드 완료 · 이전 자료 정리 보류: '+str(exc))
        try:
            client.verify_private()
            if cleanup:return finish_cleanup(cleanup)
            if cancel.is_set():return dict(status='cancelled',message='자료 공유 중지 · 다음 실행에서 이어 전송합니다.')
            if not pending:
                with AI_REVIEW_DATA_LOCK:
                    # Refresh under the same lock as local retention. Frozen ZIP
                    # bytes remain valid even if later training removes a case.
                    cases=ai_confirmed_export_cases(root)
                    if not cases:raise ValueError('공유할 학습 정답이 없습니다.')
                    captured=ai_record_share_changes(root)
                    pending=ai_export_training(root,cases,version)
                    pending.update(share_changes=dict(captured['pending']),new_count=new_count,
                        previous_uploaded=dict(state.get('uploaded',{})),previous_tag=state.get('last_success',{}).get('tag'))
                    state['pending']=pending;save()
            if cancel.is_set():return dict(status='cancelled',message='자료 공유 중지 · 다음 실행에서 이어 전송합니다.')
            release_id=client.release(pending['tag']);count=0
            while pending['files']:
                if cancel.is_set():return dict(status='cancelled',message='자료 공유 중지 · 다음 실행에서 이어 전송합니다.')
                item=pending['files'][0];path=(root/item['path']).resolve()
                if not path.is_relative_to(root.resolve()):raise ValueError('업로드 원본 경로가 잘못됐습니다.')
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('업로드 원본이 변경됐습니다. 해당 ZIP을 보존하고 공유 설정을 확인하세요.')
                client.upload(release_id,path.name,data)
                state.setdefault('uploaded',{}).update(item['cases']);count+=len(item['cases']);pending['files'].pop(0);save()
            state.pop('pending',None);state.pop('pending_update',None);state.pop('last_error',None)
            state['last_success']=dict(version=version,tag=pending['tag'],at=time.strftime('%Y-%m-%d %H:%M:%S'))
            if pending.get('complete_snapshot') is True and pending.get('all_files'):
                state['uploaded']=dict(pending.get('snapshot_cases',{}));state.pop('force_snapshot',None)
                cleanup=dict(tag=pending['tag'],batch=pending['batch'],version=pending.get('version',version),files=pending['all_files'],complete_snapshot=True,
                    share_changes=pending.get('share_changes',{}),snapshot_cases=pending.get('snapshot_cases',{}),
                    previous_uploaded=pending.get('previous_uploaded',{}),previous_tag=pending.get('previous_tag'),new_count=pending.get('new_count',count))
                state['cleanup_pending']=cleanup;save()
                return finish_cleanup(cleanup)
            # A pre-upgrade pending bundle may contain only a delta. Finish it
            # intact, then create a full replacement before deleting any backup.
            state['force_snapshot']=True;state['pending_update']=version;state.pop('resume_update',None);save()
            return dict(status='uploaded',count=count,message='기존 미완료 자료 전송 완료 · 다음 실행에서 최근 100개 전체 스냅샷을 준비합니다.')
        except Exception as exc:
            # Never include request headers, credentials, or response bodies.
            state['last_error']=str(exc);save();return dict(status='error',message='학습 자료 공유 보류 · '+str(exc))
