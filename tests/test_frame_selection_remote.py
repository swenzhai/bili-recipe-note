import json
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys

from bili_recipe_notes.frame_selection_remote import RemoteSelector


def test_remote_session_reuses_judge_and_calls_full_pipeline(tmp_path, monkeypatch):
    root=tmp_path/'session';root.mkdir()
    for index in range(2):
        job=root/'requests'/str(index);job.mkdir(parents=True)
        (job/'video.mp4').write_bytes(b'video')
        (job/'request.json').write_text(json.dumps(dict(interval=2,coarse_limit=24,fine_limit=8,
            fine_fps=5,top_k=3,allow_fallback=True,dish_context={'dish_title':'菜','segments':[]},
            time_ranges=None,review_uncertain=True,requirement='req')))
    seen=[]
    class LocalJudge: pass
    def select(video,output,judge,**config):
        seen.append((judge,config))
        if len(seen)==2:(root/'close').touch()
        return {'status':'fallback'}
    monkeypatch.setitem(sys.modules,'video_frame_choose.judging',SimpleNamespace(LocalJudge=LocalJudge))
    monkeypatch.setitem(sys.modules,'video_frame_choose.pipeline',SimpleNamespace(select=select))
    spec=importlib.util.spec_from_file_location('frame_session_test',Path(__file__).parents[1]/'scripts/ocr_remote/frame_session.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    module.serve(root,None,None,None,'model')
    assert seen[0][0] is seen[1][0]
    assert seen[0][1]['coarse_limit']==24 and seen[0][1]['fine_limit']==8
    assert seen[0][1]['allow_fallback'] is True
    assert all(json.loads(p.read_text())['exit_code']==0 for p in root.glob('requests/*/done.json'))
    assert not list(root.glob('requests/*/video.mp4'))
    assert (root/'closed.json').exists()


def test_result_rebase_preserves_remote_original_and_checks_identity(tmp_path):
    from PIL import Image
    remote=RemoteSelector.__new__(RemoteSelector)
    remote.host='user@host';remote.root='/remote'
    remote.call=lambda args:''
    original={}
    def read(path):
        if path.endswith('done.json'):return '{"exit_code":0}'
        return ''
    remote.read=read
    def copy(src,dst):
        if str(src).endswith('result.json'):
            job=str(src).split(':',1)[1].removesuffix('/result/result.json')
            result=dict(input=job+'/video.mp4',metadata=job+'/result/result.json',image=job+'/result/best_frame.png')
            original.update(result);Path(dst).write_text(json.dumps(result))
        elif str(src).endswith('best_frame.png'):Image.new('RGB',(2,2)).save(dst)
    remote.copy=copy
    run=tmp_path/'run';run.mkdir();output=run/'result'
    options=dict(requirement='r',interval=2,fine_fps=5,top_k=3,fine_limit=8,coarse_limit=24,allow_fallback=True)
    assert remote.select(tmp_path/'video.mp4',output,run,options,None,None,SimpleNamespace(line=lambda _:None))==0
    assert json.loads((output/'remote-result.json').read_text())==original
    result=json.loads((output/'result.json').read_text())
    assert result['input']==str(tmp_path/'video.mp4') and result['image']==str(output/'best_frame.png')


def test_connection_read_retries_without_resubmitting(monkeypatch):
    from bili_recipe_notes import frame_selection_remote as remote_module
    obj=RemoteSelector.__new__(RemoteSelector)
    calls=[]
    def shell(command):
        calls.append(command)
        if len(calls)<3:raise RuntimeError('connection interrupted')
        return '{"exit_code":0}'
    obj.shell=shell
    monkeypatch.setattr(remote_module.time,'sleep',lambda _:None)
    assert obj._read('/job/done.json')=='{"exit_code":0}'
    assert len(calls)==3 and len(set(calls))==1
    assert all('cat ' in c and 'ocr dish' not in c for c in calls)


def test_acceleration_sessions_are_isolated(monkeypatch):
    from bili_recipe_notes import frame_selection_remote as module
    monkeypatch.setattr(module, '_SESSIONS', {})
    calls=[]
    monkeypatch.setattr(module, 'RemoteSelector', lambda options: calls.append(dict(options)) or SimpleNamespace(read=lambda _: None))
    base={'remote_host':'user@host','remote_control_path':None}
    slow=module.session(base)
    fast=module.session({**base,'remote_acceleration':True})
    assert slow is not fast
    assert module.session(base) is slow
    assert len(calls)==2


def test_vllm_sessions_and_options_are_isolated(monkeypatch):
    from bili_recipe_notes import frame_selection_remote as module
    from bili_recipe_notes.frame_selection_service import normalize_frame_options
    import pytest
    monkeypatch.setattr(module, '_SESSIONS', {})
    monkeypatch.setattr(module, 'RemoteSelector', lambda options: SimpleNamespace(read=lambda _: None))
    base={'backend':'remote','remote_host':'user@host'}
    fast=normalize_frame_options({**base,'remote_engine':'vllm'})
    assert module.session(fast) is not module.session(base)
    assert module.session(fast) is module.session(fast)
    with pytest.raises(ValueError):
        normalize_frame_options({**fast,'remote_acceleration':True})


def test_frame_session_generation_override_keeps_original_judge(tmp_path, monkeypatch):
    root=tmp_path/'session';root.mkdir()
    job=root/'requests'/'one';job.mkdir(parents=True)
    (job/'video.mp4').write_bytes(b'video')
    (job/'request.json').write_text('{}')
    class LocalJudge:
        def judge(self):return self._generate('original prompt', 'original image')
    def select(video,output,judge,**config):
        assert judge.judge()=='response'
        (root/'close').touch()
        return {'status':'ok'}
    monkeypatch.setitem(sys.modules,'video_frame_choose.judging',SimpleNamespace(LocalJudge=LocalJudge))
    monkeypatch.setitem(sys.modules,'video_frame_choose.pipeline',SimpleNamespace(select=select))
    spec=importlib.util.spec_from_file_location('frame_session_override',Path(__file__).parents[1]/'scripts/ocr_remote/frame_session.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    calls=[]
    module.serve(root,None,None,None,'Qwen3.8-27B-NF4',generate=lambda p,i:calls.append((p,i)) or 'response')
    assert calls==[('original prompt','original image')]
    assert json.loads((job/'done.json').read_text())['exit_code']==0


def test_automatic_engine_is_default_and_validated():
    from bili_recipe_notes.frame_selection_service import normalize_frame_options
    options=normalize_frame_options({'backend':'remote','remote_host':'user@host'})
    assert options['remote_engine']=='auto'


def test_remote_auto_uses_dynamic_ocr_profile_selection(monkeypatch):
    commands=[]
    monkeypatch.setattr(RemoteSelector, 'call', lambda self, args: '')
    monkeypatch.setattr(RemoteSelector, 'shell', lambda self, command: commands.append(command) or '')
    monkeypatch.setattr(RemoteSelector, 'read', lambda self, name:
        None if name == 'exit-code' else '{"model":"8b","engine":"transformers","gpu_uuid":"GPU-small"}')
    obj=RemoteSelector({'remote_host':'user@host','remote_engine':'auto'})
    assert 'OCR_FRAME_ENGINE=auto' in commands[0]
    assert '--model-profile auto' in commands[0]
    assert obj.selected_runtime['gpu_uuid']=='GPU-small'


def test_dynamic_profiles_skip_occupied_gpu_and_fallback(tmp_path, monkeypatch):
    import pytest
    spec=importlib.util.spec_from_file_location('ocr_scheduler',Path(__file__).parents[1]/'scripts/ocr_remote/run_job.py')
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    profiles=module.frame_profiles('auto')
    a100=module.GPU('GPU-a','A100',501,39826,40960,0,True)
    a30=module.GPU('GPU-b','A30',4,24046,24576,0)
    small=module.GPU('GPU-c','3080Ti',4,12035,12288,0)
    monkeypatch.setattr(module,'query_gpus',lambda:[a100,a30,small])
    with module.reserve_model('dish',tmp_path,profiles) as (profile,gpu):
        assert gpu.uuid=='GPU-b'
        assert (profile.name,profile.engine)==('8b','vllm')
    monkeypatch.setattr(module,'query_gpus',lambda:[a100,small])
    with pytest.raises(module.ResourcesBusy):
        with module.reserve_model('dish',tmp_path,profiles):
            pytest.fail('No GPU fits')
    monkeypatch.setattr(module,'query_gpus',lambda:[module.GPU('GPU-b','A30',4,22000,24576,0)])
    with module.reserve_model('dish',tmp_path,profiles) as (profile,gpu):
        assert profile.name=='8b'
    monkeypatch.setattr(module,'query_gpus',lambda:[module.GPU('GPU-a','A100',4,39826,40960,0)])
    with module.reserve_model('dish',tmp_path,profiles) as (profile,gpu):
        assert profile.engine=='vllm'


def test_exited_session_is_replaced_before_next_video(monkeypatch):
    from bili_recipe_notes import frame_selection_remote as module
    monkeypatch.setattr(module, '_SESSIONS', {})
    created=[]
    def create(options):
        obj=SimpleNamespace(code=None)
        obj.read=lambda name: obj.code if name == 'exit-code' else None
        created.append(obj)
        return obj
    monkeypatch.setattr(module, 'RemoteSelector', create)
    options={'remote_host':'user@host'}
    first=module.session(options)
    assert module.session(options) is first
    first.code='0'
    second=module.session(options)
    assert second is not first and len(created)==2
    assert module.session(options) is second


def test_fatal_session_waits_for_cleanup_before_replacement(monkeypatch):
    from bili_recipe_notes import frame_selection_remote as module
    events=[]
    old=SimpleNamespace(read=lambda name: '{}' if name=='fatal.json' else None,
                        close=lambda: events.append('closed'))
    options={'remote_host':'user@host'}
    monkeypatch.setattr(module,'_SESSIONS',{('user@host',None,False,'auto'):old})
    new=SimpleNamespace(read=lambda name:None)
    def create(options):
        assert events==['closed']
        return new
    monkeypatch.setattr(module,'RemoteSelector',create)
    assert module.session(options) is new


def test_engine_death_exits_worker_before_next_request(tmp_path, monkeypatch):
    import pytest
    root=tmp_path
    for name in ('one','two'):
        job=root/'requests'/name
        job.mkdir(parents=True)
        (job/'request.json').write_text('{}')
    class EngineDeadError(Exception): pass
    calls=[]
    def select(*args,**kwargs):
        calls.append(1)
        raise EngineDeadError('engine stopped')
    monkeypatch.setitem(sys.modules,'video_frame_choose.judging',SimpleNamespace(LocalJudge=object))
    monkeypatch.setitem(sys.modules,'video_frame_choose.pipeline',SimpleNamespace(select=select))
    spec=importlib.util.spec_from_file_location('fatal_worker',Path(__file__).parents[1]/'scripts/ocr_remote/frame_session.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(EngineDeadError):
        module.serve(root,None,None,None,'test',generate=lambda *a:None)
    assert len(calls)==1
    assert (root/'fatal.json').exists()
    assert json.loads((root/'requests/one/done.json').read_text())['exit_code']==1
    assert not (root/'requests/two/done.json').exists()
