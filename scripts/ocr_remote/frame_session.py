"""Persistent per-batch selector worker, invoked only inside the OCR GPU supervisor."""
import contextlib
import json
import os
from pathlib import Path
import time


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def _exception_chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def serve(directory, model, processor, torch, model_id, generate=None):
    import sys
    sys.path.insert(0, str(Path(__file__).parent/"frame-deps"))
    from video_frame_choose.judging import LocalJudge
    from video_frame_choose.pipeline import select

    root = Path(directory).resolve()
    class Judge(LocalJudge):
        def __init__(self):
            pass
        def _generate(self, prompt, image=None):
            if generate is not None:
                return generate(prompt, image)
            content = ([{'type': 'image', 'image': image}] if image is not None else [])
            content.append({'type': 'text', 'text': prompt})
            inputs = processor.apply_chat_template([{'role': 'user', 'content': content}],
                tokenize=True, add_generation_prompt=True, return_dict=True,
                return_tensors='pt', enable_thinking=False).to('cuda:0')
            with torch.inference_mode():
                output = model.generate(**inputs, max_new_tokens=512, do_sample=False)
            return processor.batch_decode(output[:, inputs['input_ids'].shape[1]:], skip_special_tokens=True)[0]
    judge = Judge()
    if generate is None and model_id.startswith('Qwen3.8'):
        import inspect
        from transformers.models.qwen3_5 import modeling_qwen3_5
        kernels = {}
        for name in ('causal_conv1d_fn', 'causal_conv1d_update', 'torch_chunk_gated_delta_rule', 'torch_recurrent_gated_delta_rule'):
            function = getattr(modeling_qwen3_5, name)
            implementations = []
            visited = set()
            while callable(function) and id(function) not in visited:
                visited.add(id(function))
                if inspect.isfunction(function):
                    bindings = inspect.getclosurevars(function).nonlocals
                    impl = bindings.get('implementation')
                    if callable(impl):
                        implementations.append(impl.__module__ + '.' + impl.__name__)
                function = getattr(function, '__wrapped__', None)
            kernels[name] = implementations
        write(root/'kernels.json', kernels)
    write(root/'ready.json', {'pid': os.getpid(), 'supervisor_pid': os.getppid(), 'model': model_id, 'engine': os.environ.get('OCR_FRAME_ENGINE', 'transformers'), 'gpu_uuid': os.environ.get('CUDA_VISIBLE_DEVICES')})
    last = time.monotonic()
    while not (root/'close').exists():
        requests = sorted(root.glob('requests/*/request.json'))
        for request in requests:
            job = request.parent
            if (job/'done.json').exists():
                continue
            last = time.monotonic()
            with (job/'stderr.log').open('w') as err, (job/'stdout.log').open('w') as out:
                try:
                    config = json.loads(request.read_text())
                    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                        result = select(job/'video.mp4', job/'result', judge,
                            model_id=model_id, **config)
                    write(job/'done.json', {'exit_code': 2 if result['status']=='not_found' else 0})
                except Exception as exc:
                    import traceback
                    traceback.print_exc(file=err)
                    fatal = any(type(error).__name__ == 'EngineDeadError'
                                for error in _exception_chain(exc))
                    if fatal:
                        write(root/'fatal.json', {'error': str(exc)})
                    write(job/'done.json', {'exit_code': 1, 'error': str(exc)})
                    if fatal:
                        raise
            (job/'video.mp4').unlink(missing_ok=True)
            last = time.monotonic()
        # An abandoned controller cannot leave the GPU loaded indefinitely.
        if time.monotonic()-last > 900:
            break
        time.sleep(1)
    write(root/'closed.json', {'status': 'worker_finished'})
