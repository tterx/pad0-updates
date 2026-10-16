"""Exercise real Korean OCR on synthetic requirement badges and all five stocks.

Run after installing requirements.txt. Models download to a temporary cache.
This does not read game screenshots or alter any user inventory/database.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
import pad0_overlay as p
from rapidocr import RapidOCR,EngineType,LangDet,ModelType,OCRVersion


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path)
    parser.add_argument('--models-dir',type=Path,default=Path(tempfile.gettempdir())/'pad0-ocr-test-models')
    args=parser.parse_args()
    choices=[args.font,Path('C:/Windows/Fonts/segoeui.ttf'),
             Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_path=next((path for path in choices if path and path.is_file()),None)
    if font_path is None:
        parser.error('Set --font to a TrueType font supporting numerals and ×.')
    font=ImageFont.truetype(str(font_path),26)
    app=object.__new__(p.App)
    app._ocr_init_lock=threading.Lock()
    app._ocr_infer_lock=threading.Lock()
    app._ocr_engine=RapidOCR(params={
        'Det.engine_type':EngineType.ONNXRUNTIME,'Det.lang_type':LangDet.CH,
        'Det.model_type':ModelType.MOBILE,'Det.ocr_version':OCRVersion.PPOCRV5,
        'Rec.engine_type':EngineType.ONNXRUNTIME,'Rec.lang_type':'korean',
        'Rec.model_type':ModelType.MOBILE,'Rec.ocr_version':OCRVersion.PPOCRV5,
        'EngineConfig.onnxruntime.intra_op_num_threads':1,
        'EngineConfig.onnxruntime.inter_op_num_threads':1,
        'Global.model_root_dir':str(args.models_dir)})
    for text,color in [('6','#ff424a'),('3','#eeeeee'),('10','#eeeeee'),
                       ('12','#eeeeee'),('8','#eeeeee'),('0','#ff424a')]:
        badge=Image.new('RGB',(97,35),'#505050')
        ImageDraw.Draw(badge).text((48,17),text,font=font,anchor='mm',fill=color)
        value,method=p.read_requirement_badge(badge,app._run_ocr_tokens,app._recognize_number_crop)
        assert value==int(text),(text,value)
        print('REAL OCR PASS:',text,method,flush=True)
    image=Image.new('RGB',(735,290),'#eeeeee')
    draw=ImageDraw.Draw(image)
    for index,(need,owned) in enumerate(zip([6,3,3,3,3],[4,4,8,10,12])):
        cx=110+124*index; cy=74
        color='#ff424a' if index==0 else '#eeeeee'
        draw.rounded_rectangle((cx-48,57,cx+48,91),radius=11,fill='#505050')
        draw.rectangle((cx-52,96,cx+52,230),fill=['#ddbb33','#229944','#2288aa','#228833','#cc5522'][index])
        draw.text((cx,cy),str(need),font=font,anchor='mm',fill=color)
        draw.text((cx+10,cy+137),'×'+str(owned),font=font,anchor='mm',fill=color,
                  stroke_width=1,stroke_fill='#202020')
    app.data_paths={'database_dir':args.models_dir}
    rows=app._auto_material_targets(image,[])
    assert [row['required'] for row in rows]==[6,3,3,3,3]
    assert [row['current'] for row in rows]==[4,4,8,10,12]
    print('REAL OCR PANEL PASS: needs 6,3,3,3,3; owned 4,4,8,10,12',flush=True)


if __name__=='__main__':
    main()
