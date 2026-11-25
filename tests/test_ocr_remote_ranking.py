import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('ocr_ranking', Path(__file__).parents[1] / 'scripts/ocr_remote/ranking.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
select_best = module.select_best

def candidate(i, score, match=False):
    return dict(id=i, score=score, full_match=match, reason='画面清晰')

def test_no_match_still_selects_best():
    r = select_best(dict(candidates=[candidate(1, 10), candidate(2, 70)]), 2)
    assert r['selected_id'] == 2 and r['fallback'] is True

def test_full_match_has_priority_over_compromise():
    r = select_best(dict(candidates=[candidate(1, 99), candidate(2, 60, True)]), 2)
    assert r['selected_id'] == 2 and r['fallback'] is False

def test_tie_is_stable():
    assert select_best(dict(candidates=[candidate(2, 70), candidate(1, 70)]), 2)['selected_id'] == 1

@pytest.mark.parametrize('values', [[candidate(1, 20), candidate(1, 30)], [candidate(0, 90)], [candidate(1, float('nan'))], [candidate(1, True)], [candidate(1, 101)]])
def test_invalid_ranking_cannot_be_success(values):
    with pytest.raises(ValueError):
        select_best(dict(candidates=values), len(values))

def test_batch_loads_once_and_keeps_selecting_after_invalid_output(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace
    from contextlib import nullcontext
    from PIL import Image
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts/ocr_remote'))
    worker_spec = importlib.util.spec_from_file_location('ocr_batch_worker', Path(__file__).parents[1] / 'scripts/ocr_remote/judge_dish_once.py')
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    loads = []
    payloads = iter(['invalid', json.dumps({'candidates': [candidate(1, 40)]}), json.dumps({'candidates': [candidate(1, 90, True)]})])

    class Tensor:
        shape = (1, 1)
        def __getitem__(self, key): return self

    class Inputs(dict):
        def to(self, device): return self

    class Model:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            loads.append('model')
            return cls()
        def to(self, device): return self
        def eval(self): return self
        def generate(self, **kwargs): return Tensor()

    class Processor:
        @classmethod
        def from_pretrained(cls, *args, **kwargs): return cls()
        def apply_chat_template(self, *args, **kwargs): return Inputs(input_ids=Tensor())
        def batch_decode(self, *args, **kwargs): return [next(payloads)]

    torch = SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1, max_memory_allocated=lambda: 0),
                            bfloat16='bf16', inference_mode=nullcontext)
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoProcessor=Processor, Qwen3VLForConditionalGeneration=Model))
    image = tmp_path/'image.jpg'
    Image.new('RGB', (20, 20)).save(image)
    records = [dict(image=str(image), output=str(tmp_path/f'{i}.json'), target='菜', candidate_count=1, group=str(i)) for i in range(2)]
    manifest = tmp_path/'input.json';manifest.write_text(json.dumps(records))
    output = tmp_path/'summary.json'
    monkeypatch.setattr(sys, 'argv', ['worker', '--model', str(tmp_path), '--batch', str(manifest), '--rank-batch', '--output', str(output)])
    worker.main()
    summary = json.loads(output.read_text())
    assert loads == ['model']
    assert summary['status'] == 'complete'
    assert len(summary['items']) == 2
    assert summary['items'][0]['result']['selected_id'] == 1
    assert summary['items'][0]['result']['fallback'] is True
    assert summary['items'][1]['result']['fallback'] is False
