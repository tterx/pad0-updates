"""Portable companion files for verified single-file updates; no game input."""
from pathlib import Path
import base64
import json
import importlib.util

FEATURE_MODULES = ('pad0_install','pad0_common','pad0_quantity','pad0_crystal','pad0_crystal_review','pad0_input','pad0_solver','pad0_ai','pad0_ui')
RUNTIME = ['pad0_overlay.py', 'pad0_launcher.py', 'requirements.txt', 'update_config.json', 'VERSION.txt']
LEGACY_DOCUMENTS = ['README.md', 'REVIEW194.md', 'REVIEW195.md', 'REVIEW201.md', 'REVIEW202.md',
                    'CRYSTAL_REVIEW.md', 'QUANTITY_AI.md', 'UI_MODULE.md', 'AI_MODULE.md',
                    'TESTING.md', 'SOLVER_MODULE.md', 'INPUT_MODULE.md', 'CRYSTAL_MODULE.md',
                    'UPDATE_SETUP.md', 'SOLVER_BENCHMARK.md', 'INVENTORY_PRECHECK.md',
                    'FARMING_STATS.md', 'BACKGROUND_FARMING.md', 'PARTY_SETTINGS.md',
                    'ANDROID_BATCH_INPUT.md', '패치노트.txt', '이동_버벅임_변경내역.txt']
LEGACY_TOOLS = ['tools/build_release_source.py', 'tools/check_material_ocr.py',
                'tools/android_batch/Pad0Batch.java', 'tools/build_android_batch.py']

def file_layout():
    files = {name: 'app/' + name for name in RUNTIME}
    files.update({'디버그실행.bat':'tools/디버그실행.bat', '디버그 폴더 열기.bat':'디버그 폴더 열기.bat'})
    files.update({'run.bat': 'run.bat', '시작.vbs': '시작.vbs', 'tools/windows/사용법.txt': '사용법.txt'})
    return files

def obsolete_files():
    # Exact names from the old portable ZIPs only. Never archive a whole folder.
    modules = [name + '.py' for name in FEATURE_MODULES]
    catalogs = ['MONSTER_CATALOG.json', 'MONSTER_CATALOG_KO.csv']
    names = [*RUNTIME, *modules, *LEGACY_DOCUMENTS, *catalogs,
             '디버그실행.bat', '디버그 폴더 열기.bat',
             'pad0_overlay.py.bak', 'pad0_update_state.json', 'startup.log']
    names += ['docs/' + name for name in LEGACY_DOCUMENTS]
    names += ['docs/catalogs/' + name for name in catalogs]
    names += ['app/' + name for name in modules + LEGACY_TOOLS]
    names += LEGACY_TOOLS + ['tools/디버그 폴더 열기.bat']
    return sorted(set(names) - set(file_layout().values()))

def layout_manifest():
    return dict(version=3,files=file_layout(),remove_files=obsolete_files())


def apply_embedded_layout(source_path,payload):
    """The updater has verified this bundle's SHA; companions are embedded in it."""
    base=Path(source_path).resolve().parent
    # New app-layout installs can migrate from the old one-file updater with
    # no ZIP. Older flat installs keep working; do not relocate a live process.
    if base.name!='app' or not (base.parent/'run.bat').is_file():return False
    root=base.parent
    allowed=set(file_layout().values())-{'app/pad0_overlay.py'}
    if set(payload)!=allowed:raise ValueError('Unexpected portable companion list')
    decoded={name:base64.b85decode(value) for name,value in payload.items()}
    if sum(map(len,decoded.values()))>1024*1024:raise ValueError('Portable companions too large')
    for name,data in decoded.items():
        if name.endswith('.py'):compile(data,name,'exec')
    spec=importlib.util.spec_from_file_location('_pad0_layout_launcher',base/'pad0_launcher.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    # Atomic replacement retains the previous file on a failed write; a later
    # launch resumes incomplete layout work. Never replace personal config.
    for name,data in decoded.items():
        target=root/name
        check=target
        while check!=root:
            if check.is_symlink() or getattr(check,'is_junction',lambda:False)():raise ValueError('Linked companion target')
            check=check.parent
        if name=='app/update_config.json' and target.exists():continue
        if target.is_file() and target.read_bytes()==data:continue
        target.parent.mkdir(parents=True,exist_ok=True)
        launcher.atomic_write(target,data)
    manifest=base/'layout_manifest.json'
    launcher.atomic_write(manifest,(json.dumps(layout_manifest(),ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    # Load the just-installed launcher, not the old one that started this run.
    spec=importlib.util.spec_from_file_location('_pad0_updated_layout_launcher',base/'pad0_launcher.py')
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    launcher.organize_legacy_root(base)
    print('Portable companions and cleanup applied from verified update.',flush=True)
    return True
