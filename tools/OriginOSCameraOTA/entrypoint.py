"""GUI default, JSON-driven unattended build for repeatable local verification."""
import argparse,json,traceback,sys
from pathlib import Path
from media_patch import ROOT,Elf
from sources import Inputs

def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path);p.add_argument('--result',type=Path)
    p.add_argument('--smoke',action='store_true');a=p.parse_args()
    if a.smoke:
        report={'status':'ok','baseline_symbols':len(Elf((ROOT/'assets/baseline/cameraserver').read_bytes()).symbols),
                'components':(ROOT/'assets/components.zip').is_file(),
                'compiler':(ROOT/'toolchain/bin/clang.exe').is_file()}
        if a.result:a.result.write_text(json.dumps(report),encoding='utf8')
        return
    if a.project:
        from workflow_origin import build
        data=json.loads(a.project.read_text('utf8'));events=[]
        try:
            archive,report=build(Inputs(**data['inputs']),Path(data['output']),
                Path(data.get('toolchain') or ROOT/'toolchain/bin'),events.append,author=data.get('author',''))
            result={'status':'built','archive':str(archive),'warnings':report['warnings'],'events':events,
                    'module_author':report['module_author']}
        except Exception as exc:
            result={'status':'failed','error':str(exc),'trace':traceback.format_exc(),'events':events}
        if a.result:a.result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        if result['status']=='failed':raise SystemExit(2)
        return
    from gui import App
    import tkinter as tk
    root=tk.Tk();App(root);root.mainloop()

if __name__=='__main__':main()
