"""Run official BF16 Klein pipeline for one batch, then release GPU memory."""
import json
import os
from pathlib import Path
import sys
import time
import select

root = Path(__file__).resolve().parent
sys.path.insert(0, '/data04/swenz/ocr-on-demand/scripts')
from run_job import reserve_gpu

session_mode = len(sys.argv) == 1
if session_mode:
    first = sys.stdin.readline().strip()
    if not first: raise SystemExit(0)
else:
    first = sys.argv[1]
locks = Path.home() / '.local/state/ocr-on-demand'
locks.mkdir(parents=True, exist_ok=True)
with reserve_gpu('dish', locks, min_free_mib=20 * 1024) as gpu:
    os.environ['CUDA_VISIBLE_DEVICES'] = gpu.uuid
    import torch
    from diffusers import Flux2KleinPipeline
    from PIL import Image, PngImagePlugin
    start = time.monotonic()
    pipe = Flux2KleinPipeline.from_pretrained(str(root / 'models/klein-4b'),
                                            torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    loaded = time.monotonic()
    load_seconds = loaded-start
    count = 0
    next_job = first
    while next_job:
        job = Path(next_job)
        request = json.loads((job / 'request.json').read_text())
        start = time.monotonic()
        try:
            results = []
            refs = [Image.open(job / name).convert('RGB') for name in request.get('reference_files', [])]
            for index, prompt in enumerate(request['prompts']):
                tick = time.monotonic()
                kwargs = {'image': refs} if refs else {}
                image = pipe(prompt=prompt, width=request['width'], height=request['height'],
                             num_inference_steps=4, guidance_scale=1.0,
                             generator=torch.Generator('cuda').manual_seed(request['seed'] + index),
                             **kwargs).images[0]
                info = PngImagePlugin.PngInfo()
                info.add_text('Software', 'AI generated; FLUX.2-klein-4B')
                name = f'image-{index}.png'
                image.save(job / name, pnginfo=info)
                results.append({'file': name, 'seconds': time.monotonic()-tick, 'seed': request['seed']+index})
            result = {'status': 'completed', 'model': 'black-forest-labs/FLUX.2-klein-4B',
                      'gpu': gpu.name, 'load_seconds': load_seconds if count == 0 else 0, 'worker_pid': os.getpid(), 'session_request': count + 1,
                      'total_seconds': time.monotonic()-start, 'images': results,
                      'peak_allocated_mib': torch.cuda.max_memory_allocated()/1024**2}
            (job / 'result.tmp').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            os.replace(job / 'result.tmp', job / 'result.json')
        except Exception as exc:
            (job / 'error.tmp').write_text(json.dumps({'error': str(exc)}))
            os.replace(job / 'error.tmp', job / 'error.json')
            raise
        count += 1
        if not session_mode:
            break
        # Exit on parent EOF or idle timeout, releasing the UUID lock and CUDA context.
        ready, _, _ = select.select([sys.stdin], [], [], 120)
        next_job = sys.stdin.readline().strip() if ready else ''
