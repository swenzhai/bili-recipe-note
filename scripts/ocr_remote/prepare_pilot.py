"""Prepare the next requested visible dishes, read-only source data, independent output."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from bili_recipe_notes.storage import atomic_write_json
from bili_recipe_notes.mobile_sync import recipe_visibility_states
from bili_recipe_notes.frame_selection_eligibility import frame_selection_eligibility
from bili_recipe_notes.cover_policy import has_manually_approved_cover

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--previous',type=Path,required=True);p.add_argument('--limit',type=int,default=100);a=p.parse_args()
if a.limit <= 0:p.error('--limit must be positive')
root=Path('data/recipes').resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
previous=json.loads(a.previous.read_text());exclude={i['folder'] for i in previous['items']};after=max(exclude)
remote='/data04/swenz/ocr-on-demand/jobs/'+out.name
items=[];records=[];skipped=[]
for folder,state in sorted(recipe_visibility_states(root).items()):
 if folder<=after or folder in exclude:continue
 f=Path(folder)
 if not state.get('content_visible') or not state.get('published',True) or frame_selection_eligibility(f,visibility=state):continue
 recipe=json.loads((f/'recipe.json').read_text())
 if has_manually_approved_cover(recipe):continue
 images=[]
 for step in reversed(recipe.get('steps',[])):
  if not step.get('screenshot_path'):continue
  image=(f/step['screenshot_path']).resolve()
  if not image.is_relative_to(f) or not image.is_file() or str(image) in [v['path'] for v in images]:continue
  try:
   with Image.open(image) as im: im.verify()
  except Exception:continue
  images.append(dict(path=str(image),sha256=hashlib.sha256(image.read_bytes()).hexdigest()))
 if not images:
  skipped.append(dict(folder=folder,reason='no_readable_screenshot'));continue
 if len(images)>12:raise ValueError('More than 12 screenshots requires larger comparison layout')
 n=len(items);cols=2 if len(images)>1 else 1;rows=math.ceil(len(images)/cols)
 w=1024//cols;h=1024//rows
 canvas=Image.new('RGB',(1024,1024),(32,32,32));draw=ImageDraw.Draw(canvas)
 for j,im in enumerate(images):
  with Image.open(im['path']) as source: thumb=ImageOps.contain(ImageOps.exif_transpose(source).convert('RGB'),(w,h-32))
  x=(j%cols)*w;y=(j//cols)*h
  canvas.paste(thumb,(x+(w-thumb.width)//2,y+32+(h-32-thumb.height)//2));draw.rectangle((x,y,x+100,y+31),fill='white');draw.text((x+8,y+5),f'ID {j+1}',fill='black',font_size=22)
 name=f'{n:03}.jpg';canvas.save(out/name,quality=95)
 items.append(dict(index=n,title=recipe['title'],folder=folder,status='pending',images=images,recipe_sha256=hashlib.sha256((f/'recipe.json').read_bytes()).hexdigest()))
 records.append(dict(image=remote+'/'+name,output=remote+f'/{n:03}.json',target=recipe['title'],candidate_count=len(images),group=str(n)))
 if len(items)==a.limit:break
if len(items)!=a.limit:raise RuntimeError(f'Only {len(items)} eligible recipes')
atomic_write_json(out/'state.json',dict(status='prepared',remote=remote,items=items,skipped=skipped,scope='existing_step_screenshots'),backup=False)
atomic_write_json(out/'input.json',records,backup=False)
print(out,len(items),'dishes',sum(len(i['images']) for i in items),'screenshots',len(skipped),'missing screenshot skips')
