"""Run official BF16 Klein pipeline for one batch, then release GPU memory."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(__file__).resolve().parent
sys.path.insert(0, '/data04/swenz/ocr-on-demand/scripts')
from run_job import reserve_gpu

job = Path(sys.argv[1])
request = json.loads((job / 'request.json').read_text())
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
              'gpu': gpu.name, 'load_seconds': loaded-start,
              'total_seconds': time.monotonic()-start, 'images': results,
              'peak_allocated_mib': torch.cuda.max_memory_allocated()/1024**2}
    (job / 'result.tmp').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    os.replace(job / 'result.tmp', job / 'result.json')
