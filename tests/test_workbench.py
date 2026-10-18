"""Exercise project persistence, reference history and musical cut validation."""
import json
from pathlib import Path

import pytest

from app.workbench import import_cuarteto
from tests.conftest import PNG_1PX


@pytest.fixture
def studio(client, projects_root, tmp_path):
    project = projects_root / 'cuarteto'
    project.mkdir()
    source = project / 'sources' / 'kit'
    (source / 'production').mkdir(parents=True)
    kit = {
        'characters': {'NORA': {'name': 'Nora', 'appearance': 'Same face', 'outfit': 'Ivory'}},
        'locations': {'HOUSE': {'name': 'Same house', 'es': 'Turquoise door'}},
        'shots': [dict(id=f'{film}0{n}', video=film, block=film+'1', characters=['NORA'],
                       location='HOUSE', action='A purposeful action', still_prompt_en='Same person')
                  for film, n in [('A', 1), ('A', 2), ('B', 1)]],
        'edit_A': [], 'edit_B': [], 'age_ledger': []
    }
    (source / 'production/03_Proyecto.json').write_text(json.dumps(kit))
    (source / 'DIRECCION_DE_MONTAJE.txt').write_text('Short purposeful cuts')
    import_cuarteto(project, source, 'reviewed-source')
    return client, project


def snapshot(client):
    r = client.get('/api/project/cuarteto/studio')
    assert r.status_code == 200
    return r.json()


def shot_body(data):
    s = data['storyboard']['scenes'][0]
    return dict(revision=data['revision'], text=s['text'], framing='Close-up',
                edit_duration_secs=1.25, reference_versions=s['reference_versions'])


def test_import_preserves_source_and_refuses_replacement(studio):
    client, project = studio
    data = snapshot(client)
    assert len(data['storyboard']['scenes']) == 3
    assert data['storyboard']['scenes'][0]['edit_duration_secs'] is None
    before = (project / 'storyboard.json').read_bytes()
    with pytest.raises(ValueError):
        import_cuarteto(project, project / 'sources/kit', 'other-source')
    assert (project / 'storyboard.json').read_bytes() == before


def test_edits_survive_reload_and_stale_edit_cannot_overwrite(studio):
    client, project = studio
    original = snapshot(client)
    body = shot_body(original)
    body['text'] = 'An edited glance'
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 200
    reloaded = snapshot(client)
    assert reloaded['storyboard']['scenes'][0]['text'] == 'An edited glance'
    assert reloaded['storyboard']['scenes'][0]['edit_duration_secs'] == 1.25
    body['text'] = 'Stale browser overwrite'
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 409
    assert snapshot(client)['storyboard']['scenes'][0]['text'] == 'An edited glance'
    history = list((project / '.history').glob('*.json'))
    assert json.loads(history[0].read_text()) == original['storyboard']


def test_new_reference_version_keeps_old_shot_pins(studio):
    client, project = studio
    data = snapshot(client)
    body = {'revision': data['revision'], 'appearance': 'Same face with gray temples', 'notes': 'Older variant'}
    r = client.post('/api/project/cuarteto/studio/references/characters/NORA/versions', json=body)
    assert r.status_code == 200 and r.json()['version_id'] == 'v002'
    current = snapshot(client)
    versions = current['storyboard']['continuity']['characters']['NORA']['versions']
    assert len(versions) == 2 and versions[0]['appearance'] == 'Same face'
    assert current['storyboard']['scenes'][0]['reference_versions']['NORA'] == 'v001'
    edit = shot_body(current)
    edit['reference_versions']['NORA'] = 'v002'
    assert client.put('/api/project/cuarteto/studio/shot/1', json=edit).status_code == 200
    assert snapshot(client)['storyboard']['scenes'][0]['reference_versions']['NORA'] == 'v002'


def test_reference_needs_real_image_and_stays_inside_project(studio, tmp_path):
    client, project = studio
    data = snapshot(client)
    body = {'revision': data['revision'], 'status': 'accepted'}
    assert client.post('/api/project/cuarteto/studio/references/characters/NORA/versions', json=body).status_code == 400
    outside = tmp_path / 'outside.png'; outside.write_bytes(PNG_1PX)
    (project / 'escape.png').symlink_to(outside)
    body['image_paths'] = ['escape.png']
    assert client.post('/api/project/cuarteto/studio/references/characters/NORA/versions', json=body).status_code == 400
    import fitz
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 16, 16), 0)
    pix.clear_with(220)
    valid_png = pix.tobytes('png')
    assert client.post('/api/project/cuarteto/studio/reference-upload', files={'file': ('fake.png', b'not an image', 'image/png')}).status_code == 400
    r = client.post('/api/project/cuarteto/studio/reference-upload', files={'file': ('face.png', valid_png, 'image/png')})
    assert r.status_code == 200
    body['image_paths'] = [r.json()['path']]
    assert client.post('/api/project/cuarteto/studio/references/characters/NORA/versions', json=body).status_code == 200


def test_shot_cannot_lose_a_required_character_or_outlive_its_dialogue(studio):
    client, _ = studio
    data = snapshot(client); body = shot_body(data)
    body['reference_versions'] = {'HOUSE': 'v001'}
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 400
    body = shot_body(data)
    body['dialogue'] = [{'speaker': 'Nora', 'text': 'Optional line', 'time_in': 0, 'time_out': 2}]
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 400


def test_reorder_is_complete_and_keeps_film_boundaries(studio):
    client, _ = studio
    data = snapshot(client)
    for ids in [[2, 2, 3], [3, 1, 2], [2, 1]]:
        assert client.put('/api/project/cuarteto/studio/order', json={'revision': data['revision'], 'scene_ids': ids}).status_code == 400
    assert client.put('/api/project/cuarteto/studio/order', json={'revision': data['revision'], 'scene_ids': [2, 1, 3]}).status_code == 200
    assert [s['id'] for s in snapshot(client)['storyboard']['scenes']] == [2, 1, 3]


def test_soundtrack_rejects_unmeasured_or_out_of_range_segments(studio, monkeypatch):
    client, project = studio
    (project / 'song.mp4').write_bytes(b'fixture')
    monkeypatch.setattr('app.workbench.probe', lambda _: {'duration_secs': 20, 'streams': [{'codec_type': 'audio'}]})
    data = snapshot(client)
    endpoint = '/api/project/cuarteto/studio/soundtrack/A'
    assert client.put(endpoint, json={'revision': data['revision'], 'segments': [{'path': 'song.mp4', 'source_in': 2, 'source_out': 21}]}).status_code == 400
    assert client.put(endpoint, json={'revision': data['revision'], 'segments': [{'path': 'song.mp4', 'source_in': 8, 'source_out': 3}]}).status_code == 422
    body = {'revision': data['revision'], 'segments': [{'path': 'song.mp4', 'source_in': 2.5, 'source_out': 8, 'notes': 'Unique verse'}]}
    assert client.put(endpoint, json=body).status_code == 200
    assert snapshot(client)['storyboard']['soundtracks']['A'][0]['source_in'] == 2.5


def test_cannot_select_a_clip_beyond_its_actual_duration(studio, monkeypatch):
    client, project = studio
    (project / 'short.mp4').write_bytes(b'fixture')
    monkeypatch.setattr('app.workbench.probe', lambda _: {'duration_secs': 2, 'streams': [{'codec_type': 'video'}]})
    body = shot_body(snapshot(client)); body.update(selected_asset='short.mp4', source_trim_in=1)
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 400
    body['edit_duration_secs'] = .75
    assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 200
    entry = snapshot(client)['storyboard']['scenes'][0]['asset_timeline'][0]
    assert entry['source_in'] == 1 and entry['duration_secs'] == .75


def test_video_request_requires_body_and_is_idempotent(studio, monkeypatch):
    import uuid
    client, project = studio
    monkeypatch.setenv('VIDEO_STUDIO_BODY', 'amapola')
    endpoint = '/api/project/cuarteto/studio/requests'
    request = {'request_id': str(uuid.uuid4()), 'purpose': 'Prepare Cuarteto'}
    first = client.post(endpoint, json=request)
    assert first.status_code == 200 and first.json()['status'] == 'needs_body'
    assert first.json()['generation_submitted'] is False
    request['body'] = 'amapola'
    ready = client.post(endpoint, json=request).json()
    assert ready['status'] == 'prepared' and ready['reused'] is False
    again = client.post(endpoint, json=request).json()
    assert again['status'] == 'prepared' and again['reused'] is True
    assert again['created_at'] == ready['created_at']
    assert len(list((project / '.requests').glob('*.json'))) == 1
    request['purpose'] = 'A different generation'
    assert client.post(endpoint, json=request).status_code == 409


def test_remote_body_is_not_claimed_as_executed(studio, monkeypatch):
    import uuid
    client, _ = studio
    monkeypatch.setenv('VIDEO_STUDIO_BODY', 'amapola')
    result = client.post('/api/project/cuarteto/studio/requests', json={
        'request_id': str(uuid.uuid4()), 'body': 'medusa', 'purpose': 'Prepare Cuarteto'
    }).json()
    assert result['status'] == 'needs_routing'
    assert result['editor_path'] is None and result['matrix_delivery_verified'] is False


def test_switching_selected_takes_keeps_previous_candidates(studio):
    import fitz
    client, project = studio
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 16, 16), 0); pix.clear_with(120)
    for name in ['first.png', 'second.png']:
        (project / name).write_bytes(pix.tobytes('png'))
        data = snapshot(client); body = shot_body(data); body['selected_asset'] = name
        assert client.put('/api/project/cuarteto/studio/shot/1', json=body).status_code == 200
    scene = snapshot(client)['storyboard']['scenes'][0]
    assert {a['path'] for a in scene['takes']} == {'first.png', 'second.png'}
    assert scene['asset_timeline'][0]['path'] == 'second.png'


def test_openapi_supports_the_local_launch_readiness_check(client):
    response = client.get('/openapi.json')
    assert response.status_code == 200
    assert response.json()['info']['title'] == 'Video Studio'
    assert '/api/project/{slug}/studio/requests' in response.json()['paths']


def test_uploaded_candidate_is_retained_without_changing_selected_timeline(studio):
    import fitz
    client, project = studio
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 16, 16), 0); pix.clear_with(120)
    response = client.post('/api/project/cuarteto/storyboard/1/add-version',
                           files={'file': ('candidate.png', pix.tobytes('png'), 'image/png')},
                           data={'type': 'still'})
    assert response.status_code == 200
    scene = snapshot(client)['storyboard']['scenes'][0]
    assert scene['takes'][0]['path'] == 'scenes/candidate.png'
    assert scene['asset_timeline'] == []


def test_additional_coverage_preserves_source_and_can_stay_out_of_the_edit(studio):
    client, _ = studio
    original = snapshot(client)
    response = client.post('/api/project/cuarteto/studio/shot/1/coverage', json={'revision': original['revision']})
    assert response.status_code == 200
    data = snapshot(client)
    assert len(data['storyboard']['scenes']) == 4
    assert data['storyboard']['scenes'][0] == original['storyboard']['scenes'][0]
    coverage = data['storyboard']['scenes'][1]
    assert coverage['parent_shot_id'] == 'A01' and coverage['shot_id'] == 'A01.C1'
    assert coverage['edit_duration_secs'] is None and coverage['selected_asset'] is None
    body = shot_body(data); body['included'] = False
    assert client.put('/api/project/cuarteto/studio/shot/'+str(coverage['id']), json=body).status_code == 200
    updated = snapshot(client)['storyboard']['scenes'][1]
    assert updated['included'] is False and updated['shot_id'] == 'A01.C1'
