"""VM-only supervisor for a bounded, read-only market-data/shadow experiment."""
import argparse
import json
import math
import shutil
import signal
import subprocess
import time
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True)
    p.add_argument('--binary',required=True)
    p.add_argument('--env-file',required=True)
    p.add_argument('--series',default='KXBTC15M',choices=['KXBTC15M','KXETH15M'])
    p.add_argument('--minutes',type=int,default=10096)
    a=p.parse_args()
    root=Path(a.root).resolve();root.mkdir(parents=True,exist_ok=True)
    start=time.time();deadline=start+a.minutes*60
    state={'mode':'shadow','series':a.series,'start_unix':start,'deadline_unix':deadline,'pid':None,'status':'starting','segments':[]}
    def save():
        temp=root/'supervisor.tmp';temp.write_text(json.dumps(state,indent=2)+'\n');temp.replace(root/'supervisor.json')
    child=None
    def stop(signum,frame):
        nonlocal deadline
        deadline=time.time()
        if child and child.poll() is None: child.send_signal(signal.SIGINT)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    save()
    while time.time()<deadline:
        disk=shutil.disk_usage(root)
        spool=sum(x.stat().st_size for x in root.rglob('*') if x.is_file())
        if disk.free<512*1024**2 or spool>1024**3:
            state['status']='stopped_disk_guard';save();return
        segment=root/f'run_{int(time.time())}';segment.mkdir()
        cmd=[a.binary,'shadow','--residual-ladder','--series',a.series,'--minutes',str(max(1,math.ceil((deadline-time.time())/60))),
             '--out',str(segment),'--env-file',a.env_file]
        with (segment/'run.log').open('wb') as log:
            child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
            state.update(status='running',pid=child.pid,current_segment=str(segment));state['segments'].append(str(segment));save()
            guard=False
            while child.poll() is None:
                time.sleep(10)
                spool=sum(x.stat().st_size for x in root.rglob('*') if x.is_file())
                if shutil.disk_usage(root).free<512*1024**2 or spool>1024**3 or time.time()>=deadline:
                    guard=time.time()<deadline
                    child.send_signal(signal.SIGINT)
                    try:child.wait(timeout=30)
                    except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
                    break
            state['last_exit']=child.returncode
        if guard:state['status']='stopped_disk_guard';save();return
        if child.returncode!=0:
            tail=(segment/'run.log').read_text(errors='replace')[-4096:]
            # Resume only an identified transient spot disconnect; all other errors stop.
            if 'spot feed stopped' not in tail:
                state['status']='stopped_error';save();return
            state['status']='reconnecting_spot';save();time.sleep(5)
        elif time.time()<deadline:
            state['status']='stopped_early';save();return
    state['status']='complete';state['pid']=None;save()


if __name__=='__main__':main()
