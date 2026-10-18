"""Exercise paid-call boundaries, interrupted recovery and continuity provenance."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
import requests

from lib import generation as gen
from tests.test_workbench import studio, snapshot


@pytest.fixture
def project(tmp_path):
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 16, 16), 0); pix.clear_with(140)
    (tmp_path/'ref.png').write_bytes(pix.tobytes('png'))
    return tmp_path


def board():
    return {'source_revision': 'verified-kit', 'scenes': [
        {'id': 1, 'shot_id': 'A01', 'characters': ['NORA'], 'location': 'HOUSE',
         'reference_versions': {'NORA': 'v001', 'HOUSE': 'v001'}}],
        'continuity': {'characters': {'NORA': {'name': 'Nora', 'versions': [
            {'id': 'v001', 'status': 'draft', 'appearance': 'Same person', 'image_paths': []}]}},
            'locations': {'HOUSE': {'name': 'House', 'versions': [
                {'id': 'v001', 'status': 'draft', 'image_paths': []}]}}}}


def plan(project, **extra):
    return gen.plan_generation(project, board(), target={'type': 'reference', 'kind': 'characters',
        'key': 'NORA', 'version': 'v001'}, prompt='Fictional Nora at 40, full-body, neutral daylight',
        media_type='image', model=gen.IMAGE_MODEL, **extra)


def authorized(project, prepared=None):
    p = prepared or plan(project); gen.prepare_generation(project, p)
    gen.approve(project, p['job_id'], plan_sha256=p['plan_sha256'],
                max_cost_usd=p['quote']['estimated_cost_usd'], statement='Human authorized this exact quoted test job')
    return p


class Transport:
    credential_identity = 'same-issuing-account'
    def __init__(self, project, responses=None):
        self.project=project; self.calls=[]; self.downloads=0; self.responses=list(responses or [])
    def request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        if self.responses:
            result=self.responses.pop(0)
            if isinstance(result, Exception):raise result
            return result
        data={'request_id': 'provider-job-1'} if method=='POST' else {'status':'done','data':[{'url':'https://imgen.x.ai/private-result?signature=do-not-save'}]}
        return SimpleNamespace(status_code=200, json=lambda:data)
    def download(self, url):
        self.downloads+=1
        return (self.project/'ref.png').read_bytes()


def test_preparing_and_reloading_never_authorizes_or_calls_provider(project, monkeypatch):
    monkeypatch.setattr(gen, 'XaiTransport', lambda: pytest.fail('credentials/provider consulted before consent'))
    p=plan(project); first=gen.prepare_generation(project,p); second=gen.prepare_generation(project,p)
    assert first==second and first['status']=='awaiting_approval'
    assert p['quote']['estimated_cost_usd']=='0.04'
    with pytest.raises(ValueError,match='authorization'):
        gen.run_generation(project,p['job_id'])
    assert len(list((project/'.generation-jobs').glob('*.json')))==1


def test_approval_is_bound_to_exact_cost_and_payload(project):
    p=plan(project);gen.prepare_generation(project,p)
    with pytest.raises(ValueError,match='match'):
        gen.approve(project,p['job_id'],plan_sha256='changed',max_cost_usd='0.04',statement='Human authorization')
    with pytest.raises(ValueError,match='cover'):
        gen.approve(project,p['job_id'],plan_sha256=p['plan_sha256'],max_cost_usd='0.01',statement='Human authorization')
    authorized(project,p)
    data=json.loads(gen.path_for(project,p['job_id']).read_text());data['approval']['expires_at']='2020-01-01T00:00:00+00:00';gen.atomic(gen.path_for(project,p['job_id']),data)
    with pytest.raises(ValueError,match='expired'):
        gen.run_generation(project,p['job_id'])


def test_recovery_keeps_remote_id_downloads_and_never_resubmits(project):
    p=authorized(project);t=Transport(project)
    sent=gen.run_generation(project,p['job_id'],transport=t)
    assert sent['status']=='pending' and sent['remote_request_id']=='provider-job-1'
    # New invocation/process gets the persisted remote ID, not a second POST.
    result=gen.run_generation(project,p['job_id'],transport=t)
    assert result['status']=='completed' and (project/result['output']).is_file()
    before=len(t.calls);gen.run_generation(project,p['job_id'],transport=t)
    assert len(t.calls)==before and [c[0] for c in t.calls]==['POST','GET']
    stored=gen.path_for(project,p['job_id']).read_text()
    assert 'signature=' not in stored and 'data:image/' not in stored
    assert 'Bearer' not in stored
    # Missing local result recovers the existing provider job too.
    (project/result['output']).unlink()
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='completed'
    assert [c[0] for c in t.calls]==['POST','GET','GET']


def test_timeout_after_post_never_repeats_paid_effect(project):
    p=authorized(project);t=Transport(project,[requests.Timeout()])
    result=gen.run_generation(project,p['job_id'],transport=t)
    assert result['status']=='submission_unknown'
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='submission_unknown'
    assert len(t.calls)==1
    with pytest.raises(ValueError,match='already been submitted'):
        gen.approve(project,p['job_id'],plan_sha256=p['plan_sha256'],max_cost_usd='0.04',statement='Retry this human authorization')


def test_saved_submitting_state_is_uncertain_not_live_process(project):
    p=authorized(project);path=gen.path_for(project,p['job_id']);record=json.loads(path.read_text());record['status']='submitting';gen.atomic(path,record)
    observed=gen.status(project,p['job_id'])
    assert observed['status']=='submission_unknown' and observed['active_lock'] is False
    t=Transport(project);assert gen.run_generation(project,p['job_id'],transport=t)['status']=='submission_unknown'
    assert t.calls==[]


def test_pending_timeout_is_not_terminal_and_next_poll_recovers(project):
    p=authorized(project);t=Transport(project)
    gen.run_generation(project,p['job_id'],transport=t)
    t.responses=[requests.Timeout()]
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='pending'
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='completed'
    assert [c[0] for c in t.calls]==['POST','GET','GET']


def test_shots_require_actual_pinned_accepted_references_and_preserve_hashes(project):
    b=board();args=dict(target={'type':'shot','scene_id':1},prompt='A purposeful reaction',media_type='video',model='grok-imagine-video',duration=3)
    with pytest.raises(ValueError,match='accepted'):
        gen.plan_generation(project,b,**args)
    for kind in ['characters','locations']:
        for item in b['continuity'][kind].values():
            item['versions'][0].update(status='accepted',image_paths=['ref.png'])
    p=gen.plan_generation(project,b,**args)
    assert p['quote']['estimated_cost_usd']=='0.214' and len(p['input_references'])==2
    endpoint,payload=gen.payload(project,p)
    assert endpoint=='/videos/generations'
    assert payload['reference_images'][0]['url'].startswith('data:image/png;base64,')
    assert 'storage_options' not in payload
    (project/'ref.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):
        gen.payload(project,p)


def test_age_variants_require_the_established_younger_reference(project):
    b=board();b['reference_dependencies']={'NORA':['YOUNGER_NORA']}
    with pytest.raises(ValueError,match='required accepted'):
        gen.plan_generation(project,b,target={'type':'reference','kind':'characters','key':'NORA','version':'v001'},prompt='Same woman thirteen years later',media_type='image',model=gen.IMAGE_MODEL)


def test_reference_must_stay_in_project_and_cross_account_poll_is_refused(project,tmp_path):
    p=authorized(project);t=Transport(project);gen.run_generation(project,p['job_id'],transport=t)
    other=Transport(project);other.credential_identity='another-account'
    with pytest.raises(ValueError,match='issuing'):
        gen.run_generation(project,p['job_id'],transport=other)
    assert not other.calls


def test_completed_reference_registration_is_idempotent_and_keeps_pins(studio):
    client,project=studio
    pix=fitz.Pixmap(fitz.csRGB,(0,0,16,16),0);pix.clear_with(140);(project/'ref.png').write_bytes(pix.tobytes('png'))
    data=snapshot(client)
    r=client.post('/api/project/cuarteto/studio/generations/plan',json={'revision':data['revision'],
        'target':{'type':'reference','kind':'characters','key':'NORA','version':'v001'},
        'prompt':'Full-body fictional Nora, neutral expression','media_type':'image','model':gen.IMAGE_MODEL})
    assert r.status_code==200;r=r.json();job_id=r['job_id']
    assert client.post(f'/api/project/cuarteto/studio/generations/{job_id}/run',json={}).status_code==403
    assert client.get('/api/project/cuarteto/studio/generations').json()[0]['job_id']==job_id
    p=authorized(project,r['plan']);t=Transport(project)
    gen.run_generation(project,job_id,transport=t);gen.run_generation(project,job_id,transport=t)
    for _ in range(2):
        data=snapshot(client)
        response=client.post(f'/api/project/cuarteto/studio/generations/{job_id}/register',json={'revision':data['revision']})
        assert response.status_code==200
    data=snapshot(client);versions=data['storyboard']['continuity']['characters']['NORA']['versions']
    assert len(versions)==2 and versions[-1]['status']=='draft' and versions[-1]['generation_job_id']==job_id
    assert data['storyboard']['scenes'][0]['reference_versions']['NORA']=='v001'
    assert len(list((project/'.history').glob('*.json')))>=1


def test_withdrawn_unsent_job_has_no_remaining_cost_authority(project):
    p=authorized(project);gen.withdraw(project,p['job_id']);t=Transport(project)
    with pytest.raises(ValueError,match='authorization'):
        gen.run_generation(project,p['job_id'],transport=t)
    assert not t.calls
    authorized(project,p);gen.run_generation(project,p['job_id'],transport=t)
    with pytest.raises(ValueError,match='already submitted'):
        gen.withdraw(project,p['job_id'])


def test_async_api_submission_and_recovery_share_one_provider_job(studio,monkeypatch):
    import time
    client,project=studio
    pix=fitz.Pixmap(fitz.csRGB,(0,0,16,16),0);pix.clear_with(140);(project/'ref.png').write_bytes(pix.tobytes('png'))
    t=Transport(project);monkeypatch.setattr(gen,'XaiTransport',lambda **_:t)
    p=plan(project)
    # Use the API-created fixture target, preserving its own imported identity.
    with client:
        s=snapshot(client)
        prepared=client.post('/api/project/cuarteto/studio/generations/plan',json={'revision':s['revision'],
            'target':{'type':'reference','kind':'characters','key':'NORA','version':'v001'},
            'prompt':'A meaningful portrait','media_type':'image','model':gen.IMAGE_MODEL}).json()
        job_id=prepared['job_id'];endpoint=f'/api/project/cuarteto/studio/generations/{job_id}'
        assert client.post(endpoint+'/approve',json={'plan_sha256':prepared['plan']['plan_sha256'],'max_cost_usd':'0.04','statement':'Explicit human authorization for the isolated test'}).status_code==200
        for wanted in ['pending','completed']:
            assert client.post(endpoint+'/run',json={}).status_code==200
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                result=client.get(endpoint).json()
                if result['status']==wanted:break
                time.sleep(.01)
            assert result['status']==wanted
        assert [c[0] for c in t.calls]==['POST','GET']


def test_invalid_provider_download_is_retained_and_recovery_keeps_remote_request(project):
    p=authorized(project);t=Transport(project);gen.run_generation(project,p['job_id'],transport=t)
    t.download=lambda _:b'provider-result-that-fails-image-decoding'
    r=gen.run_generation(project,p['job_id'],transport=t)
    assert r['status']=='download_failed' and len(r['provider_originals'])==1
    assert (project/r['provider_originals'][0]['path']).read_bytes()==b'provider-result-that-fails-image-decoding'
    t.download=lambda _:(project/'ref.png').read_bytes()
    r=gen.run_generation(project,p['job_id'],transport=t)
    assert r['status']=='completed' and len(r['provider_originals'])==2
    assert [c[0] for c in t.calls]==['POST','GET','GET']


def test_subscription_jobs_never_fall_back_to_api_key_or_need_cash_approval(project):
    p=plan(project,billing_mode='subscription');r=gen.prepare_generation(project,p)
    assert r['status']=='ready_subscription' and not p['quote']['additional_cash_spend_authorized']
    t=Transport(project);t.credentials={'provider':'xai','auth_type':'api_key'}
    with pytest.raises(ValueError,match='paid API'):
        gen.run_generation(project,p['job_id'],transport=t)
    assert not t.calls and not gen.status(project,p['job_id'])['generation_submitted']
    t.credentials={'provider':'xai-oauth','auth_type':'oauth'}
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='pending'
    assert gen.run_generation(project,p['job_id'],transport=t)['status']=='completed'
    assert [c[0] for c in t.calls]==['POST','GET']


def accepted_board():
    b = board()
    for kind in ['characters', 'locations']:
        for item in b['continuity'][kind].values():
            item['versions'][0].update(status='accepted', image_paths=['ref.png'])
    return b


def composed_still(project, b):
    p = gen.plan_generation(project, b, target={'type':'shot', 'scene_id':1},
        prompt='Nora in the same house', media_type='image', model=gen.IMAGE_MODEL,
        aspect_ratio='1:1')
    authorized(project, p)
    t = Transport(project)
    gen.run_generation(project, p['job_id'], transport=t)
    r = gen.run_generation(project, p['job_id'], transport=t)
    b['scenes'][0]['takes'] = [{'path':r['output'], 'generation_job_id':r['job_id'], 'type':'still'}]
    return r


def animate(project, b, frame, **extra):
    return gen.plan_generation(project, b, target={'type':'shot','scene_id':1},
        prompt='A quiet reaction, camera locked', media_type='video',
        model='grok-imagine-video-1.5', aspect_ratio='1:1', first_frame=frame, **extra)


def test_pinned_composition_sends_one_first_frame_preserving_all_lineage(project):
    b = accepted_board(); still = composed_still(project, b)
    p = animate(project, b, still['output'])
    assert len(p['input_references']) == 2
    assert p['first_frame']['generation_job_id'] == still['job_id']
    assert p['quote']['input_images'] == 1 and p['quote']['continuity_reference_images'] == 2
    assert p['quote']['estimated_cost_usd'] == '0.43'
    endpoint, body = gen.payload(project, p)
    assert endpoint == '/videos/generations' and body['image']['url'].startswith('data:image/png;base64,')
    assert 'reference_images' not in body and '<IMAGE_' not in body['prompt']
    assert p == animate(project, b, still['output'])


def test_single_accepted_location_can_be_the_exact_first_frame(project):
    b = accepted_board(); b['scenes'][0]['characters'] = []
    b['scenes'][0]['reference_versions'].pop('NORA')
    p = animate(project, b, 'ref.png')
    assert p['first_frame']['source'] == 'accepted_reference'
    assert p['first_frame']['sha256'] == gen.digest_file(project/'ref.png')
    assert 'image' in gen.payload(project, p)[1]


def test_first_frame_cannot_bypass_continuity_or_use_another_shots_still(project):
    b = accepted_board()
    with pytest.raises(ValueError, match='retained still'):
        animate(project, b, 'ref.png')
    still = composed_still(project, b)
    source = gen.path_for(project, still['job_id'])
    record = json.loads(source.read_text()); record['plan']['target']['scene_id'] = 2
    gen.atomic(source, record)
    with pytest.raises(ValueError, match='same shot'):
        animate(project, b, still['output'])


def test_first_frame_from_old_pins_is_refused(project):
    b = accepted_board(); still = composed_still(project, b)
    item = b['continuity']['characters']['NORA']
    item['versions'].append({**item['versions'][0], 'id':'v002'})
    b['scenes'][0]['reference_versions']['NORA'] = 'v002'
    with pytest.raises(ValueError, match='different continuity pins'):
        animate(project, b, still['output'])


def test_changed_first_frame_is_rejected_before_any_external_submit(project):
    b = accepted_board(); still = composed_still(project, b)
    p = animate(project, b, still['output']); authorized(project, p)
    (project/still['output']).write_bytes(b'changed')
    t = Transport(project)
    with pytest.raises(ValueError, match='first frame changed'):
        gen.run_generation(project, p['job_id'], transport=t)
    assert not t.calls and not gen.status(project, p['job_id'])['generation_submitted']


def test_first_frame_enforces_project_boundary_and_original_aspect(project):
    b = accepted_board(); b['scenes'][0]['characters'] = []
    with pytest.raises(ValueError):
        animate(project, b, '../outside.png')
    with pytest.raises(ValueError, match='aspect ratio'):
        gen.plan_generation(project, b, target={'type':'shot','scene_id':1},
            prompt='No camera crop',media_type='video',model='grok-imagine-video-1.5',
            first_frame='ref.png',aspect_ratio='16:9')
    with pytest.raises(ValueError, match='only available for shot video'):
        plan(project, first_frame='ref.png')
