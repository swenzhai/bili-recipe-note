import io
import json
from pathlib import Path
import pytest
from PIL import Image
from bili_recipe_notes import cover_generation as gen, frame_selection_service as service
from test_dish_info_runner import recipe


def fake_service(monkeypatch):
    image = io.BytesIO(); Image.new('RGB',(1024,1024),'orange').save(image,format='PNG')
    calls=[]
    def request(path,payload=None):
        calls.append((path,payload))
        return (json.dumps({'status':'completed','job_id':'a'*32,'images':[{}]}).encode()
                if payload else image.getvalue())
    monkeypatch.setattr(gen,'request',request)
    return calls


def test_generated_candidate_no_video_originals_unchanged_and_stale(tmp_path,monkeypatch):
    folder=recipe(tmp_path,'dish')
    if folder is None: folder=tmp_path/'dish'
    p=folder/'recipe.json'; data=json.loads(p.read_text());data['source_time_ranges']=[{'start':10,'end':20}];p.write_text(json.dumps(data))
    before={p:p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    calls=fake_service(monkeypatch)
    result=service.generate_frame_candidate(folder,out_dir=tmp_path,options={'backend':'flux'})
    assert result['status']=='needs_review' and result['timestamp'] is None
    assert result['source_type']=='generated'
    assert service.load_frame_candidate(tmp_path,folder)['image_path']==result['image_path']
    assert calls[0][1]['width']==1024 and 'dish' in calls[0][1]['prompts'][0]
    assert all(p.read_bytes()==b for p,b in before.items())
    data['title']='changed';p.write_text(json.dumps(data))
    assert service.load_frame_candidate(tmp_path,folder) is None


def test_failed_response_never_publishes_candidate(tmp_path,monkeypatch):
    recipe(tmp_path,'dish'); folder=tmp_path/'dish'
    monkeypatch.setattr(gen,'request',lambda *a: b'{"status":"completed","job_id":"../escape","images":[{}]}')
    with pytest.raises(ValueError):service.generate_frame_candidate(folder,out_dir=tmp_path,options={'backend':'flux'})
    assert service.load_frame_candidate(tmp_path,folder) is None
    assert not list((tmp_path/'.frame-selection/candidates').glob('*.json'))


def test_missing_reference_does_not_call_remote(tmp_path,monkeypatch):
    recipe(tmp_path,'dish')
    monkeypatch.setattr(gen,'request',lambda *a: pytest.fail('must not request without reference'))
    with pytest.raises(ValueError,match='参考图模式'):
        service.generate_frame_candidate(tmp_path/'dish',out_dir=tmp_path,options={'backend':'flux','generation_reference':True})


def test_flux_batch_failure_continues_and_manual_cover_preserved(tmp_path,monkeypatch):
    from bili_recipe_notes import frame_selection_runner as runner
    from test_frame_selection_runner import prepare,run,confirmed
    for name in ['a','b','c']:recipe(tmp_path,name)
    confirmed(tmp_path/'c')
    monkeypatch.setattr(runner,'_preflight_options',lambda o:o)
    calls=fake_service(monkeypatch)
    original=gen.request
    def request(path,payload=None):
        if payload and not calls:
            calls.append((path, payload))
            raise ValueError('one item failed')
        return original(path,payload)
    monkeypatch.setattr(gen,'request',request)
    state=prepare(monkeypatch,tmp_path,backend='flux',limit=None)
    result=run(tmp_path,state)
    assert result['failed']==1 and result['needs_review']==1
    assert len(result['items'])==2
    assert json.loads((tmp_path/'c/recipe.json').read_text())['cover_image_status']=='uploaded'


def test_http_error_does_not_leak_server_response(monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setattr(gen, 'connection', lambda: ('http://example.test', 'secret-token'))
    def fail(req, **kwargs):
        assert req.get_header('Authorization') == 'Bearer secret-token'
        raise HTTPError(req.full_url, 503, 'secret-server-detail', {}, io.BytesIO(b'secret-token'))
    monkeypatch.setattr(gen, 'urlopen', fail)
    with pytest.raises(RuntimeError) as error:
        gen.request('/generate', {'prompts':['dish']})
    assert '显卡' in str(error.value) and 'secret' not in str(error.value)


def test_generated_image_has_metadata_only_and_auto_reference_fallback(tmp_path, monkeypatch):
    recipe(tmp_path, 'dish')
    calls = fake_service(monkeypatch)
    result = service.generate_frame_candidate(tmp_path/'dish', out_dir=tmp_path, options={
        'backend':'flux', 'generation_reference':True, 'generation_auto_reference':True})
    assert 'reference_images' not in calls[0][1]
    assert result['label'] == 'AI生成示意图（非实拍）'
    with Image.open(result['image_path']) as image:
        assert 'AI generated' in image.info['Source']
        assert image.getpixel((25,990)) == image.getpixel((25,900))
    assert 'no visible text' in calls[0][1]['prompts'][0]
    assert 'remove all of them' in calls[0][1]['prompts'][0]
