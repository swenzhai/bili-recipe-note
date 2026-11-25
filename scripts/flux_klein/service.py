"""Authenticated on-demand API; the official pipeline executes in an isolated worker."""
import base64
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image

ROOT = Path(__file__).resolve().parent
TOKEN = (ROOT / 'api-token').read_text().strip()
LOCK = threading.Lock()
app = FastAPI(title='FLUX.2 Klein 4B cover generation')

def auth(authorization: str = Header(default='')):
    if not secrets.compare_digest(authorization, 'Bearer '+TOKEN):
        raise HTTPException(401, 'Invalid bearer token')

class Generate(BaseModel):
    prompts: list[str] = Field(min_length=1, max_length=4)
    reference_images: list[str] = Field(default_factory=list, max_length=3)
    width: int = Field(default=1024, ge=256, le=1536, multiple_of=16)
    height: int = Field(default=1024, ge=256, le=1536, multiple_of=16)
    seed: int = Field(default=42, ge=0, le=2147483640)

@app.get('/health')
def health():
    return {'status': 'ok', 'model': 'FLUX.2-klein-4B', 'busy': LOCK.locked()}

@app.post('/generate', dependencies=[Depends(auth)])
def generate(body: Generate):
    if any(not p.strip() or len(p)>12000 for p in body.prompts):
        raise HTTPException(422, 'Prompt must contain 1–12000 characters')
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Generator is busy')
    try:
        job_id = uuid.uuid4().hex
        job = ROOT / 'outputs' / job_id
        job.mkdir(parents=True)
        refs=[]
        for index, value in enumerate(body.reference_images):
            if len(value)>28*1024*1024:
                raise HTTPException(413, 'Reference exceeds 20 MiB')
            try:
                image = Image.open(io.BytesIO(base64.b64decode(value, validate=True)))
                image.load()
                image = image.convert('RGB')
            except Exception as exc:
                raise HTTPException(422, 'Invalid base64 image') from exc
            name=f'reference-{index}.png'; image.save(job/name); refs.append(name)
        data=body.model_dump(exclude={'reference_images'});data['reference_files']=refs
        (job/'request.json').write_text(json.dumps(data,ensure_ascii=False))
        with (job/'worker.log').open('w') as log:
            result=subprocess.run([sys.executable,str(ROOT/'worker.py'),str(job)],
                                  stdout=log,stderr=subprocess.STDOUT,timeout=1800)
        if result.returncode:
            message=(job/'worker.log').read_text(errors='replace')[-2000:]
            raise HTTPException(503 if 'No idle GPU' in message else 500,
                                {'job_id':job_id,'error':message})
        return {'job_id':job_id,**json.loads((job/'result.json').read_text())}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504,'Generation exceeded 30 minutes; worker terminated') from exc
    finally:
        LOCK.release()

@app.get('/images/{job_id}/{index}', dependencies=[Depends(auth)])
def image(job_id: str,index: int):
    if len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id) or index not in range(4):
        raise HTTPException(404)
    path=ROOT/'outputs'/job_id/f'image-{index}.png'
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path, media_type='image/png')
