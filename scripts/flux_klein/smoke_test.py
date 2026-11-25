"""Exercise authenticated HTTP generation and reference editing without logging tokens."""
import base64,json,time,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
headers={'Authorization':'Bearer '+(root/'api-token').read_text().strip(),'Content-Type':'application/json'}
def call(body):
 start=time.monotonic()
 req=urllib.request.Request('http://127.0.0.1:8766/generate',data=json.dumps(body).encode(),headers=headers)
 with urllib.request.urlopen(req,timeout=1800) as response:d=json.load(response)
 print(json.dumps(d,ensure_ascii=False),flush=True)
 d['http_seconds']=time.monotonic()-start
 return d
first=call({'prompts':['Professional food photography of Chinese tomato and egg stir fry, soft yellow scrambled eggs and juicy red tomato wedges on a plain white ceramic plate, natural window light, appetizing, close up, no text, no people'], 'width':1024,'height':1024,'seed':42})
ref=root/'outputs'/first['job_id']/first['images'][0]['file']
second=call({'prompts':['Keep the same tomato and egg dish and its ingredients. Change only the plate to a blue ceramic plate and the background to a light wooden table, natural food photography, no text.'],'reference_images':[base64.b64encode(ref.read_bytes()).decode()], 'width':1024,'height':1024,'seed':43})
(root/'smoke-results.json').write_text(json.dumps({'text_to_image':first,'image_edit':second},indent=2))
print('SMOKE_COMPLETE',flush=True)
