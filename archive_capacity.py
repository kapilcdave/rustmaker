"""Archive closed audit chunks over existing SSH. Delete only hash-verified copies.

The VM stops at its spool cap if this local helper is unavailable. Never deletes
partial files, logs, or anything outside the explicit experiment root.
"""
import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path


def ssh(command):
    return subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=20','aws',command],timeout=90)


def main():
    p=argparse.ArgumentParser();p.add_argument('--remote',required=True);p.add_argument('--local',required=True);p.add_argument('--once',action='store_true')
    a=p.parse_args();remote=Path(a.remote)
    if remote.parent!=Path('/home/admin/trading/kalshi-mm15') or not remote.name.startswith('capacity-'):
        raise ValueError('Refusing an unscoped remote path')
    local=Path(a.local).resolve();local.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            code='import pathlib,json; r=pathlib.Path('+repr(str(remote))+'); print(json.dumps([str(p.relative_to(r)) for p in r.glob("run_*/audit/*.gz")]))'
            names=json.loads(ssh('python3 -c '+shlex.quote(code)))
            for rel in names:
                rp=Path(rel)
                if rp.is_absolute() or '..' in rp.parts or len(rp.parts)!=3 or rp.parts[1]!='audit':raise ValueError(rel)
                src=remote/rp;dest=local/rp;dest.parent.mkdir(parents=True,exist_ok=True)
                expected=ssh('sha256sum '+shlex.quote(str(src))).decode().split()[0]
                if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:
                    temp=dest.with_suffix('.download')
                    subprocess.run(['scp','-q','-o','BatchMode=yes',f'aws:{src}',str(temp)],check=True,timeout=180)
                    if hashlib.sha256(temp.read_bytes()).hexdigest()!=expected:raise ValueError('Archive checksum mismatch')
                    temp.replace(dest)
                ssh('rm -- '+shlex.quote(str(src)))
                with (local/'archive.jsonl').open('a') as f:f.write(json.dumps({'time':time.time(),'file':rel,'sha256':expected})+'\n')
            state=json.loads(ssh('cat '+shlex.quote(str(remote/'supervisor.json'))))
            (local/'supervisor.json').write_text(json.dumps(state,indent=2)+'\n')
            if state['status'] in ('complete','stopped_error','stopped_disk_guard','stopped_early'):
                subprocess.run([sys.executable,str(Path(__file__).with_name('score_capacity.py')),str(local)],check=True)
                print('Supervisor ended:',state['status'],flush=True);return
            print(time.strftime('%Y-%m-%d %H:%M:%S'),state['status'],'archived',len(names),'chunks',flush=True)
        except Exception as e:
            print(time.strftime('%Y-%m-%d %H:%M:%S'),'archive error',str(e),flush=True)
            if a.once:raise
        if a.once:return
        time.sleep(60)


if __name__=='__main__':main()
