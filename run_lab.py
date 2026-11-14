"""Run isolated demonstration scenarios and save the results as JSON."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from services.lab import run_lab,SCENARIOS

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario',choices=['all',*SCENARIOS],default='all')
    args=parser.parse_args()
    result=run_lab(args.scenario)
    print(json.dumps(result,indent=2))
    report_dir=Path(__file__).parent/'reports';report_dir.mkdir(exist_ok=True)
    dest=report_dir/('lab_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%SZ')+'.json')
    dest.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Saved report:',dest)
    raise SystemExit(0 if result['passed']==result['total'] else 1)
