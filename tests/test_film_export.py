"""Real per-film export proves independent stories, audio clock and recovery."""
import copy
import json
import subprocess

import pytest

from lib.film import plan_export, prepare_export, run_export, export_status


@pytest.fixture
def film_project(tmp_path):
    (tmp_path/'scenes').mkdir();(tmp_path/'audio').mkdir()
    for color in ['red','blue']:
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=256x144:r=30:d=1',
                        '-c:v','libx264',str(tmp_path/'scenes'/f'{color}.mp4')],capture_output=True,check=True,timeout=20)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2',
                    str(tmp_path/'song.wav')],capture_output=True,check=True,timeout=20)
    board={'scenes':[{'id':1,'shot_id':'A01','video':'A','selected_asset':'scenes/red.mp4','edit_duration_secs':.5},
                     {'id':2,'shot_id':'A02','video':'A','selected_asset':'scenes/blue.mp4','edit_duration_secs':.5},
                     {'id':3,'shot_id':'B01','video':'B','selected_asset':'scenes/blue.mp4','edit_duration_secs':1}],
           'soundtracks':{'A':[{'path':'song.wav','source_in':0,'source_out':1}],
                          'B':[{'path':'song.wav','source_in':1,'source_out':2}]}}
    return tmp_path,board


def test_each_film_keeps_its_own_cuts_and_music_without_duplication(film_project):
    project,board=film_project
    outputs=[]
    for film,shots in [('A',['A01','A02']),('B',['B01'])]:
        plan=plan_export(project,board,film,width=256,height=144,preset='ultrafast')
        assert [c['shot_id'] for c in plan['cuts']]==shots
        prepare_export(project,plan)
        result=run_export(project,plan['job_id'])
        assert result['status']=='completed',result
        assert result['completed_shots']==shots
        assert float(result['probe']['format']['duration'])==pytest.approx(1,abs=.06)
        assert {s['codec_type'] for s in result['probe']['streams']}=={'video','audio'}
        out=project/result['output'];stamp=out.stat().st_mtime_ns
        assert run_export(project,plan['job_id'])['output_sha256']==result['output_sha256']
        assert out.stat().st_mtime_ns==stamp
        assert export_status(project,plan['job_id'])['active_lock'] is False
        outputs.append(result['output'])
    assert outputs[0]!=outputs[1]


def test_musical_holes_and_unselected_takes_are_not_padded(film_project):
    project,board=film_project
    board['scenes'][0]['edit_duration_secs']=.2
    with pytest.raises(ValueError,match='align the edit'):
        plan_export(project,board,'A')
    board['scenes'][0]['selected_asset']=None
    with pytest.raises(ValueError,match='needs a selected take'):
        plan_export(project,board,'A')


def test_interrupted_render_resumes_same_input_and_source_changes_are_refused(film_project):
    project,board=film_project
    plan=plan_export(project,board,'A',width=256,height=144,preset='ultrafast')
    record=prepare_export(project,plan)
    record['status']='running';record['pid']=99999999
    from lib.film import atomic,job_path
    atomic(job_path(project,plan['job_id']),record)
    assert export_status(project,plan['job_id'])['status']=='interrupted'
    result=run_export(project,plan['job_id'])
    assert result['status']=='completed'
    # A fresh export plan has its own identity; changing a source invalidates its pinned inputs.
    other=copy.deepcopy(board);other['scenes'][0]['source_trim_in']=.1
    plan2=plan_export(project,other,'A',width=256,height=144,preset='ultrafast')
    prepare_export(project,plan2)
    with (project/'scenes/red.mp4').open('ab') as stream:stream.write(b'changed')
    assert run_export(project,plan2['job_id'])['status']=='failed'


def test_frame_rounding_does_not_accumulate_across_many_short_cuts(film_project):
    project,board=film_project
    prototype=board['scenes'][0]
    board['scenes']=[{**prototype,'id':i+1,'edit_duration_secs':.101} for i in range(10)]
    board['soundtracks']['A'][0]['source_out']=1.01
    plan=plan_export(project,board,'A')
    assert sum(c['frames'] for c in plan['cuts'])==round(1.01*30)
    assert [c['frame_in'] for c in plan['cuts']]==list(range(0,30,3))


def test_missing_final_export_can_resume_from_completed_cuts(film_project):
    project,board=film_project
    plan=plan_export(project,board,'A',width=256,height=144,preset='ultrafast')
    prepare_export(project,plan);result=run_export(project,plan['job_id'])
    assert result['status']=='completed'
    cut=project/'renders'/'film-A'/plan['job_id']/'scene-01.mp4'
    stamp=cut.stat().st_mtime_ns
    (project/result['output']).unlink()
    assert export_status(project,plan['job_id'])['status']=='interrupted'
    resumed=run_export(project,plan['job_id'])
    assert resumed['status']=='completed' and cut.stat().st_mtime_ns==stamp
