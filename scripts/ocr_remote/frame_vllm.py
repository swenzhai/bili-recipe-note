"""vLLM generation adapter; sampling and judgment remain in the original pipeline."""
import json
import time
from pathlib import Path


def serve(directory, model_path):
    from transformers import AutoProcessor
    from vllm import LLM, SamplingParams
    from frame_session import serve as serve_session

    root = Path(directory)
    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
    config = json.loads((Path(model_path) / 'config.json').read_text())
    quantization = 'bitsandbytes' if config.get('quantization_config', {}).get('quant_method') == 'bitsandbytes' else None
    model = LLM(model=str(model_path), quantization=quantization, dtype='bfloat16',
                tensor_parallel_size=1, gpu_memory_utilization=0.90 if quantization is None else 0.85,
                max_model_len=8192, max_num_seqs=1, limit_mm_per_prompt={'image': 1},
                enable_prefix_caching=True)
    sampling = SamplingParams(temperature=0, max_tokens=512)

    def generate(prompt, image=None):
        content = ([{'type': 'image', 'image': image}] if image is not None else [])
        content.append({'type': 'text', 'text': prompt})
        rendered = processor.apply_chat_template([{'role': 'user', 'content': content}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        request = {'prompt': rendered}
        if image is not None:
            request['multi_modal_data'] = {'image': image}
        started = time.monotonic()
        result = model.generate([request], sampling, use_tqdm=False)[0]
        elapsed = time.monotonic() - started
        with (root/'generation-metrics.jsonl').open('a') as stream:
            stream.write(json.dumps({'elapsed_seconds': elapsed,
                'input_tokens': len(result.prompt_token_ids),
                'output_tokens': len(result.outputs[0].token_ids),
                'has_image': image is not None})+'\n')
        return result.outputs[0].text

    serve_session(directory, model, processor, None, Path(model_path).name, generate=generate)
