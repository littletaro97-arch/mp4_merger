import subprocess,time,json,ctypes,tempfile
from pathlib import Path
from ctypes import wintypes
import psutil
from media import find_ffmpeg,NO_WINDOW
ff,_=find_ffmpeg()
with tempfile.TemporaryDirectory() as td:
 d=Path(td); source=d/'input.mp4'
 subprocess.run([ff,'-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-f','lavfi','-i','sine','-t','20','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(source)],check=True,creationflags=NO_WINDOW)
 rows=[]; cpu=0; elapsed=0
 for i in range(10):
  start=time.perf_counter(); p=subprocess.Popen([ff,'-v','error','-i',str(source),'-c','copy','-y',str(d/'output.mp4')],creationflags=NO_WINDOW); proc=psutil.Process(p.pid)
  while p.poll() is None:
   try: rows.append(proc.memory_info().rss)
   except psutil.Error: pass
   time.sleep(.001)
  assert p.returncode==0
  elapsed+=time.perf_counter()-start
  times=[wintypes.FILETIME() for _ in range(4)]
  assert ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(p._handle),*[ctypes.byref(t) for t in times])
  cpu+=sum((t.dwHighDateTime<<32)+t.dwLowDateTime for t in times[2:])/1e7
 result={'peak_working_set_MiB':round(max(rows)/2**20,2),'cpu_one_core_percent':round(cpu/elapsed*100,2),'total_seconds':round(elapsed,3),'runs':10,'input_MiB':round(source.stat().st_size/2**20,2)}
 path=Path('qa-output/results.json'); all=json.loads(path.read_text(encoding='utf-8')); all['ffmpeg_unthrottled_streamcopy']=result; path.write_text(json.dumps(all,ensure_ascii=False,indent=2),encoding='utf-8'); print(result)
