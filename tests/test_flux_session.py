import importlib.util
import json
from pathlib import Path


def test_requests_reuse_process_and_release(tmp_path):
    source = Path('scripts/flux_klein/service.py').read_text()
    (tmp_path/'api-token').write_text('test')
    (tmp_path/'service.py').write_text(source)
    (tmp_path/'worker.py').write_text('''import sys, json, os
from pathlib import Path
for line in sys.stdin:
 p=Path(line.strip())
 (p/'result.json').write_text(json.dumps({'pid':os.getpid()}))
''')
    spec=importlib.util.spec_from_file_location('klein_test',tmp_path/'service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    try:
        pids=[]
        for i in range(2):
            job=tmp_path/str(i);job.mkdir()
            module.run_job(job)
            pids.append(json.loads((job/'result.json').read_text())['pid'])
        assert pids[0]==pids[1]
        process=module.WORKER
        module.close_worker()
        assert process.poll() is not None
    finally:
        module.close_worker()
