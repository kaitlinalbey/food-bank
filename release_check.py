"""Reproducible release gate. Distinguishes passed, skipped, untested and failed.

Runs against isolated temporary databases only. Does not reset live data, contact GitHub,
or start Docker services. Docker configuration is inspected only when available.
"""
import argparse
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from benchmark import run_all
from presentation_story import story
from services.lab import run_lab


def checks(root=None):
    root=Path(root or Path(__file__).resolve().parent)
    print('Checking automated tests...',flush=True)
    tests=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py',top_level_dir=str(root))
    sink=io.StringIO()
    run=unittest.TextTestRunner(stream=sink,verbosity=1).run(tests)
    test_result={'discovered':run.testsRun,'passed':run.testsRun-len(run.failures)-len(run.errors)-len(run.skipped),
                 'skipped':len(run.skipped),'failures':len(run.failures),'errors':len(run.errors),
                 'skipped_examples':[reason for _,reason in run.skipped[:4]],
                 'error_summary':[str(test) for test,_ in (run.failures+run.errors)[:6]]}
    print('Checking isolated failure and recovery scenarios...',flush=True)
    try:lab=run_lab('all')
    except Exception as exc:lab={'passed':0,'total':8,'error':str(exc)[:200]}
    print('Checking scripted presentation...',flush=True)
    try:demo=story()
    except Exception as exc:demo={'passed':0,'total':7,'error':str(exc)[:200]}
    print('Measuring 5/10/20-bank local SQLite concurrency...',flush=True)
    try:perf=run_all()
    except Exception as exc:perf={'results':[],'error':str(exc)[:200]}
    print('Checking Docker Compose syntax availability...',flush=True)
    docker={'status':'not_verified','reason':'Docker CLI not installed in this environment'}
    if shutil.which('docker'):
        try:
            result=subprocess.run(['docker','compose','-f',str(root/'compose.yaml'),'config','--quiet'],
                                  capture_output=True,text=True,timeout=20,cwd=root)
            docker={'status':'config_valid' if result.returncode==0 else 'config_failed',
                    'notice':'Compose syntax only; containers are NOT launched or verified.',
                    'error':result.stderr.strip()[:200] if result.returncode else None}
        except (OSError,subprocess.TimeoutExpired) as exc:
            docker={'status':'not_verified','reason':str(exc)[:200]}
    failed=(test_result['failures'] or test_result['errors'] or lab['passed']!=lab['total']
            or demo['passed']!=demo['total']
            or any(not row['passed'] for row in perf['results']) or len(perf['results'])!=3
            or docker['status']=='config_failed')
    incomplete=bool(test_result['skipped']) or docker['status']!='config_valid'
    return {'generated_at_utc':datetime.now(timezone.utc).isoformat(),
            'release_status':'FAIL' if failed else 'PARTIALLY_VERIFIED' if incomplete else 'TESTS_PASSED_DOCKER_RUNTIME_UNVERIFIED',
            'tests':test_result,'test_lab':{'passed':lab['passed'],'total':lab['total']},
            'presentation':{'passed':demo['passed'],'total':demo['total']},
            'performance':perf,'docker_compose':docker,
            'important_note':'An actual Docker up/build and HTTP check must be performed on a PC with Docker. '
            'Passing these checks does not mean the service is production secure.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='reports/release_verification.json');args=p.parse_args()
    result=checks();dest=Path(args.out);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    md=dest.with_suffix('.md')
    t=result['tests'];lab=result['test_lab'];demo=result['presentation']
    lines=['# Food Bank release verification','',f"Status: **{result['release_status']}**",'',
           f"- Automated tests: {t['passed']} passed, {t['skipped']} skipped, {t['failures']} failures, {t['errors']} errors",
           f"- Isolated fault lab: {lab['passed']}/{lab['total']}",
           f"- Scripted presentation: {demo['passed']}/{demo['total']}",
           f"- Local SQLite concurrency benchmarks: {len(result['performance'].get('results',[]))} cases",
           f"- Docker Compose syntax: {result['docker_compose']['status']}",'',
           '**Limitations:** skipped integration tests are not counted as passes; Docker runtime verification is a separate step.','']
    md.write_text('\n'.join(lines),encoding='utf8')
    print('\n'.join(lines));print('Detailed JSON:',dest)
    if result['release_status']=='FAIL':return 1
    if t['skipped']:return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
