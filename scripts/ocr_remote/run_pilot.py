"""Durable local SSH driver: run one batch, sync results, verify release, persist exit."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from bili_recipe_notes.storage import atomic_write_json

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--control-path',required=True);p.add_argument('--timeout',type=int,default=64800);args=p.parse_args()
out=args.output.resolve();state=json.loads((out/'state.json').read_text());remote=state['remote'];host='swenz@10.21.176.48'
ssh=['ssh','-o','BatchMode=yes','-o','ControlPath='+args.control_path,host]
scp=['scp','-o','BatchMode=yes','-o','ControlPath='+args.control_path]
import shlex

def command(values):return ssh+[shlex.join(values)]
def sync():
 r=subprocess.run(scp+[host+':'+remote+'/summary.json',str(out/'summary.json')],capture_output=True,text=True)
 if r.returncode==0:
  subprocess.run([sys.executable,'scripts/ocr_remote/collect_pilot.py',str(out)],check=True)
 return r.returncode

subprocess.run(command(['mkdir','-p',remote]),check=True)
subprocess.run(scp+[str(p) for p in sorted(out.glob('*.jpg'))]+[str(out/'input.json'),host+':'+remote+'/'],check=True)
state['status']='running';atomic_write_json(out/'state.json',state)
atomic_write_json(out/'execution.json',{'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'timeout_seconds':args.timeout})
with (out/'run.log').open('w') as log:
 proc=subprocess.Popen(command(['/data04/swenz/ocr-on-demand/ocr','dish','--batch',remote+'/input.json','--rank-batch','--output',remote+'/summary.json','--timeout',str(args.timeout)]),stdout=log,stderr=subprocess.STDOUT)
 while True:
  try:code=proc.wait(timeout=60);break
  except subprocess.TimeoutExpired:sync()
sync()
r=subprocess.run(command(['/data04/swenz/ocr-on-demand/ocr','status']),capture_output=True,text=True)
(out/'gpu-after.txt').write_text(r.stdout+r.stderr)
s=json.loads((out/'state.json').read_text())
success=code==0 and s.get('status')=='complete' and all(i['status']=='selected' for i in s['items']) and s.get('originals_unchanged')
if not success:
 s['status']='stopped_gpu_busy' if code==75 else 'incomplete';atomic_write_json(out/'state.json',s)
atomic_write_json(out/'execution.json',{'status':'complete' if success else 'stopped_gpu_busy' if code==75 else 'incomplete','exit_code':code,'finished_at':datetime.now(timezone.utc).isoformat(),'gpu_status_exit_code':r.returncode,'counts':s.get('counts',{})})
(out/'REPORT.md').write_text('# 批量封面测试\n\n'+json.dumps({'status':'complete' if success else s['status'],'exit_code':code,'total':len(s['items']),'counts':s.get('counts',{}),'elapsed_seconds':s.get('elapsed_seconds'),'originals_unchanged':s.get('originals_unchanged')},ensure_ascii=False,indent=2)+'\n\n[浏览候选](review.html) · [显卡结束状态](gpu-after.txt)\n\n仅比较已有步骤截图；完全匹配是模型判定，降级图仍为候选。未自动替换封面。\n')
sys.exit(0 if success else 1)
