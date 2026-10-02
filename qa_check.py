import json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path
import psutil
from media import find_ffmpeg, get_ffprobe_info, NO_WINDOW
from state import AutomationState
from tasks import PairMergeTask

def sample(p, seconds):
    proc=psutil.Process(p.pid); rows=[]; start=time.monotonic()
    cpu0=sum(proc.cpu_times()[:2])
    while time.monotonic()-start<seconds and p.poll() is None:
        group=[proc]+proc.children(recursive=True)
        rows.append(sum(x.memory_info().rss for x in group if x.is_running()))
        time.sleep(.05)
    elapsed=time.monotonic()-start
    cpu=sum(proc.cpu_times()[:2])-cpu0 if p.poll() is None else 0
    return {'peak_working_set_MiB':round(max(rows,default=0)/2**20,2),'cpu_one_core_percent':round(cpu/elapsed*100,2),'seconds':round(elapsed,2)}

ff,probe=find_ffmpeg(); results={}
with tempfile.TemporaryDirectory() as td:
    d=Path(td)
    for name,args in [('sample.mp4',['-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-t','20','-an','-c:v','libx264','-preset','ultrafast']),('sample(1).mp4',['-f','lavfi','-i','sine=frequency=440','-t','20','-vn','-c:a','aac'])]:
        subprocess.run([ff,'-v','error',*args,str(d/name)],check=True,creationflags=NO_WINDOW)
    logs=[]
    class Logger:
        def log(self,msg): logs.append(msg)
    task=PairMergeTask(AutomationState(d),Logger(),ff,probe,threading.Event())
    task._run_one_scan(d); task._run_one_scan(d)
    out=d/'sample_merged.mp4'; assert out.exists(),logs
    info=get_ffprobe_info(probe,out); assert {x['codec_type'] for x in info['streams']}=={'audio','video'}
    before=out.stat().st_mtime_ns; task._run_one_scan(d); assert before==out.stat().st_mtime_ns
    assert (d/'sample.mp4').exists() and (d/'sample(1).mp4').exists()
    results['merge']='PASS: real 720p/20s video + audio, output validated, sources retained, repeat scan no overwrite'
    results['source_total_MiB']=round(sum((d/n).stat().st_size for n in ['sample.mp4','sample(1).mp4'])/2**20,2)
    p=subprocess.Popen([ff,'-v','error','-re','-i',str(d/'sample.mp4'),'-i',str(d/'sample(1).mp4'),'-map','0:v:0','-map','1:a:0','-c','copy','-y',str(d/'profile.mp4')],creationflags=NO_WINDOW)
    results['ffmpeg_merge_throttled']=sample(p,5); p.wait(timeout=40)
    # GUI uses an isolated state and directory, never production automation_state.json.
    code="import tkinter as tk; import main; from state import AutomationState; main.AutomationState=lambda:AutomationState("+repr(td)+"); r=tk.Tk(); a=main.MergerApp(r); a.monitor_dir.set("+repr(td)+"); a._start_automation(); r.after(12000,a._on_closing); r.mainloop()"
    p=subprocess.Popen([sys.executable,'-c',code],creationflags=NO_WINDOW)
    time.sleep(1); results['gui_monitor']=sample(p,8); assert p.wait(timeout=15)==0
# Launch packaged application without Python or FFmpeg in PATH. Close only our test window via WM_CLOSE.
import ctypes
from ctypes import wintypes
p=subprocess.Popen([str(Path('dist/MediaToolbox/MediaToolbox.exe').resolve())],env={**os.environ,'PATH':os.environ['SystemRoot']+'\\System32'},creationflags=NO_WINDOW)
time.sleep(2); results['portable_gui']=sample(p,5)
user=ctypes.windll.user32
callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
windows=[]
@callback
def visit(hwnd,param):
    pid=wintypes.DWORD(); user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if pid.value==p.pid and user.IsWindowVisible(hwnd): windows.append(hwnd)
    return True
user.EnumWindows(visit,0); assert windows,'Packaged GUI not found'
for hwnd in windows: user.PostMessageW(hwnd,0x10,0,0)
assert p.wait(timeout=10)==0
results['portable']='PASS: GUI starts with system-only PATH; bundled Python and FFmpeg present'
results['package_MiB']=round(sum(x.stat().st_size for x in Path('dist/MediaToolbox').rglob('*') if x.is_file())/2**20,2)
Path('qa-output/results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
