"""Collect ranked results without changing source recipes or applying covers."""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import shutil
from bili_recipe_notes.storage import read_json, atomic_write_json

p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args();out=args.output.resolve()
s=read_json(out/'state.json',expected_type=dict);batch=read_json(out/'summary.json',expected_type=dict)
for r in batch['items']:
 i=s['items'][int(r['group'])];result=r.get('result',{})
 if 'selected_id' not in result:
  i.update(status='failed',error=result);continue
 image=i['images'][result['selected_id']-1]
 i.update(status='selected',selection=result,selected_image=image['path'])
 cover=out/'selected'/f"{i['index']:03}{Path(image['path']).suffix}"
 cover.parent.mkdir(exist_ok=True)
 if not cover.exists():shutil.copyfile(image['path'],cover)
 i['candidate_copy']=str(cover.relative_to(out))
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
s['originals_unchanged']=all(h(Path(i['folder'])/'recipe.json')==i['recipe_sha256'] and all(h(Path(a['path']))==a['sha256'] for a in i['images']) for i in s['items'])
s.update(status=batch['status'],elapsed_seconds=batch['elapsed_seconds'])
s['counts']=dict(Counter('failed' if i['status']=='failed' else 'pending' if i['status']=='pending' else 'fallback' if i['selection']['fallback'] else 'full_match' for i in s['items']))
atomic_write_json(out/'state.json',s)
spot_path=out/'visual-spot-check.json'
spots={x['index']:x for x in read_json(spot_path,expected_type=dict).get('items',[])} if spot_path.exists() else {}
cards=[]
for i in s['items']:
 if i['status']!='selected':continue
 r=i['selection'];label='降级候选' if r['fallback'] else '模型判定完全匹配'
 note=spots.get(i['index'],{}).get('note','')
 cards.append(f'<article><h2>{i["index"]+1}. {html.escape(i["title"])}</h2><p>{label} · ID {r["selected_id"]}</p><img src="{html.escape(i["candidate_copy"])}"><p>{html.escape(r["reason"])}</p><p>{html.escape(note)}</p><details><summary>查看所有候选</summary><img src="{i["index"]:03}.jpg"></details></article>')
(out/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>'+str(len(s['items']))+'道菜封面候选</title><style>body{font:16px sans-serif;margin:24px;background:#f7f5ef}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}article{background:white;padding:16px;border-radius:12px}img{width:100%}h2{font-size:20px}</style><h1>'+str(len(s['items']))+'道菜封面候选</h1><p>仅筛选已有截图，未替换菜单封面；降级候选不代表已完成菜品。</p><main>'+''.join(cards)+'</main>')
print(json.dumps(dict(status=s['status'],counts=s['counts'],elapsed_seconds=s['elapsed_seconds'],originals_unchanged=s['originals_unchanged']),ensure_ascii=False))
