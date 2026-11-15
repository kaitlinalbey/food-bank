"""Opt-in Docker runtime smoke test. Uses a dedicated ephemeral Compose project.

Requires Docker Desktop/Engine. Aborts if ports are occupied or a previous named
verification project exists. Never touches the ordinary Food Bank Compose project.
"""
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.request import urlopen,Request

ROOT=Path(__file__).resolve().parent
PROJECT='foodbankclassverify'


def cmd(*args,timeout=180):
    result=subprocess.run(['docker','compose','-p',PROJECT,*args],cwd=ROOT,
                          capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Docker command failed: '+(result.stderr or result.stdout)[-600:])
    return result.stdout.strip()


def main():
    if not shutil.which('docker'):
        raise SystemExit('Docker is not installed. Skipped: runtime not verified.')
    try:
        cmd('version',timeout=15)
        if cmd('ps','-q',timeout=15):
            raise RuntimeError('Existing verification project found. Stop it manually; no changes made.')
        busy=[]
        for port in (5000,5001,5002,5003):
            with socket.socket() as s:
                s.settimeout(.5)
                if s.connect_ex(('127.0.0.1',port))==0:busy.append(port)
        if busy:raise RuntimeError(f'Ports are occupied: {busy}. Stop the normal demo first.')
        started=False
        try:
            started=True
            cmd('up','--build','-d',timeout=300)
            result={'time_utc':datetime.now(timezone.utc).isoformat(),'checks':{}}
            for port in (5000,5001,5002,5003):
                endpoint=(f'http://127.0.0.1:{port}/api/health' if port==5000
                          else f'http://127.0.0.1:{port}/node-health')
                last_error=None
                for _ in range(35):
                    try:
                        with urlopen(endpoint,timeout=3) as response:
                            data=json.loads(response.read())
                            if response.status==200 and data.get('status')=='ok':
                                result['checks'][str(port)]={'passed':True,'response':data}
                                break
                    except Exception as exc:last_error=str(exc)[:160]
                    time.sleep(1)
                if str(port) not in result['checks']:
                    result['checks'][str(port)]={'passed':False,'error':last_error or 'Not healthy'}
            url='http://127.0.0.1:5000/api/login'
            payload=json.dumps({'bank_id':1,'username':'staff','password':'demo123'}).encode()
            try:
                request=Request(url,payload,{'Content-Type':'application/json'},method='POST')
                with urlopen(request,timeout=5) as response:
                    info=json.loads(response.read())
                token=info.pop('token',None)
                if not token:raise ValueError('No token returned')
                request=Request('http://127.0.0.1:5000/api/dashboard',headers={'Authorization':'Bearer '+token})
                with urlopen(request,timeout=5) as response:
                    board=json.loads(response.read())
                result['checks']['authenticated_api']={'passed':bool(board.get('inventory'))}
            except Exception as exc:
                result['checks']['authenticated_api']={'passed':False,'error':str(exc)[:180]}
            result['passed']=all(v['passed'] for v in result['checks'].values())
            output=ROOT/'reports'/'docker_verification.json';output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
            print(json.dumps(result,indent=2))
            print('Saved',output)
            return 0 if result['passed'] else 1
        finally:
            if started:
                # Clean up only the dedicated verification project and its ephemeral volume.
                try:cmd('down','--volumes','--remove-orphans',timeout=90)
                except Exception as exc:print('WARNING: Manual Docker verification cleanup needed:',exc)
    except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print('Docker runtime NOT verified:',exc)
        return 1

if __name__=='__main__':raise SystemExit(main())
