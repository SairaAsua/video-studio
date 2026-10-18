"""Finite, recoverable xAI jobs; preparing a quote never authorizes a paid POST."""
from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import fitz
import requests

from lib.film import atomic, digest_file, media_file, inspect
from lib.gen_bridge import _credentials

PRICING_URL = 'https://docs.x.ai/developers/pricing'
PRICING_CHECKED = '2026-10-18'
PRICING_VALID_UNTIL = '2026-10-25T23:59:59+00:00'
IMAGE_MODEL = 'grok-imagine-image-2.0'
VIDEO_RATES = {'grok-imagine-video': ('0.07', '0.002', 10),
               'grok-imagine-video-1.5': ('0.14', '0.01', 15)}
STATES_AFTER_SUBMIT = {'submitting', 'submission_unknown', 'pending', 'provider_failed',
                       'rejected', 'completed', 'download_failed'}


def now():
    return datetime.now(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def path_for(project, job_id):
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise ValueError('Invalid generation job ID')
    return project / '.generation-jobs' / (job_id + '.json')


@contextmanager
def lock_job(path):
    path.parent.mkdir(exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def version_of(board, kind, key, version):
    if kind not in {'characters', 'locations'}:
        raise ValueError('Unknown reference kind')
    item = board.get('continuity', {}).get(kind, {}).get(key)
    if not item:
        raise ValueError('Unknown continuity reference')
    selected = next((v for v in item['versions'] if v['id'] == version), None)
    if not selected:
        raise ValueError('Unknown reference version')
    return item, selected


def plan_generation(project, board, *, target, prompt, media_type, model,
                    duration=3, aspect_ratio='16:9', input_references=None, variant='main'):
    if not prompt.strip() or len(prompt) > 40000 or len(variant) > 100:
        raise ValueError('Provide a prompt and a short take variant')
    if media_type not in {'image', 'video'} or aspect_ratio not in {'16:9', '1:1', '3:4', '9:16'}:
        raise ValueError('Unsupported media type or aspect ratio')
    target = copy.deepcopy(target)
    if target.get('type') == 'reference':
        if media_type != 'image':
            raise ValueError('A continuity reference must be an image')
        _, specification = version_of(board, target['kind'], target['key'], target['version'])
        required = set(board.get('reference_dependencies', {}).get(target['key'], []))
    elif target.get('type') == 'shot':
        specification = next((s for s in board['scenes'] if s['id'] == target['scene_id']), None)
        if specification is None:
            raise ValueError('Shot not found')
        required = set(specification.get('characters', []))
        if specification.get('location'):
            required.add(specification['location'])
        pins = specification.get('reference_versions', {})
        if input_references is None:
            input_references = [{'kind': 'characters' if k in board['continuity']['characters'] else 'locations',
                                 'key': k, 'version': pins.get(k)} for k in sorted(required)]
        if any(pins.get(r['key']) != r['version'] for r in input_references):
            raise ValueError('Shot inputs must use its pinned reference versions')
    else:
        raise ValueError('Choose a shot or continuity reference target')
    inputs = []
    references = input_references or []
    if required - {r['key'] for r in references}:
        raise ValueError('Include the required accepted continuity references')
    for reference in references:
        item, version = version_of(board, **reference)
        if version.get('status') != 'accepted' or not version.get('image_paths'):
            raise ValueError('Choose accepted visual references before generating this target')
        # A version can include complementary views, all preserved in the quote.
        for relative in version['image_paths']:
            path = media_file(project, relative)
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError('Reference image exceeds 20 MB')
            try:
                image = fitz.Pixmap(str(path))
                if image.width < 1 or image.height < 1:
                    raise ValueError()
            except Exception:
                raise ValueError('Continuity reference is not a valid raster image') from None
            inputs.append({**reference, 'name': item['name'], 'path': relative,
                           'sha256': digest_file(path)})
    if len(inputs) > 5:
        raise ValueError('Choose at most five reference images for this job')
    if media_type == 'image':
        if model != IMAGE_MODEL:
            raise ValueError('Choose the quoted image model')
        output_cost, input_rate = Decimal('0.04'), Decimal('0.01')
        params = {'resolution': '1k', 'quality': 'low'}
    else:
        if model not in VIDEO_RATES:
            raise ValueError('Choose a quoted video model')
        output_rate, reference_rate, maximum = VIDEO_RATES[model]
        if not isinstance(duration, int) or not 1 <= duration <= maximum:
            raise ValueError('Video duration exceeds the selected model range')
        output_cost, input_rate = Decimal(output_rate) * duration, Decimal(reference_rate)
        params = {'resolution': '720p', 'duration': duration}
    cost = output_cost + input_rate * len(inputs)
    quote = {'currency': 'USD', 'estimated_cost_usd': str(cost), 'outputs': 1,
             'input_images': len(inputs), 'output_cost_usd': str(output_cost),
             'input_image_rate_usd': str(input_rate), 'pricing_url': PRICING_URL,
             'pricing_checked': PRICING_CHECKED, 'valid_until': PRICING_VALID_UNTIL,
             'basis': 'published API list price; OAuth credit/entitlement not verified; taxes excluded',
             'attempts': 1, 'public_storage_requested': False}
    # Editorial selections and previously registered candidates must not change
    # the identity of an otherwise identical generation request.
    spec_keys = ('id', 'name', 'appearance', 'outfit', 'es', 'en') if target['type'] == 'reference' else (
        'id', 'shot_id', 'characters', 'location', 'reference_versions', 'visual_direction',
        'video_prompt_en', 'continuity', 'text')
    specification = {k: copy.deepcopy(specification[k]) for k in spec_keys if k in specification}
    reference_legend = '\n'.join(
        (f'<IMAGE_{i}>' if media_type == 'video' else f'Input image {i+1}') +
        f": {r['name']} ({r['key']}, {r['version']}). Preserve this identity and its listed outfit/architecture."
        for i, r in enumerate(inputs))
    effective_prompt = prompt.strip() + ('\nAccepted reference roles:\n'+reference_legend if inputs else '')
    plan = {'schema': 'video-studio.generation/v1', 'target': target,
            'target_specification': specification, 'media_type': media_type,
            'model': model, 'prompt': effective_prompt, 'aspect_ratio': aspect_ratio,
            'parameters': params, 'input_references': inputs, 'variant': variant,
            'quote': quote, 'source_revision': board.get('source_revision'),
            'review_status': 'candidate_requires_visual_review'}
    plan['plan_sha256'] = fingerprint(plan)
    return {**plan, 'job_id': plan['plan_sha256'][:24]}


def prepare_generation(project, plan):
    path = path_for(project, plan['job_id'])
    with lock_job(path):
        if path.exists():
            record = json.loads(path.read_text())
            if record['plan'] != plan:
                raise ValueError('Generation content mismatch')
            return record
        record = {'job_id': plan['job_id'], 'plan': plan, 'status': 'awaiting_approval',
                  'created_at': now().isoformat(), 'remote_request_id': None,
                  'generation_submitted': False}
        atomic(path, record)
        return record


def status(project, job_id):
    path = path_for(project, job_id)
    if not path.exists():
        raise ValueError('Generation job not found')
    record = json.loads(path.read_text())
    active = False
    with path.with_suffix('.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
    # A stale local state is not evidence of a running process or of a failure.
    if record['status'] == 'submitting' and not active:
        record = {**record, 'status': 'submission_unknown'}
    if record['status'] == 'completed':
        output = project / record.get('output', '')
        if not output.is_file() or digest_file(output) != record.get('output_sha256'):
            record = {**record, 'status': 'download_failed', 'error': 'Local candidate missing or changed; recover the existing provider request.'}
    return {**record, 'active_lock': active}


def approve(project, job_id, *, plan_sha256, max_cost_usd, statement):
    path = path_for(project, job_id)
    with lock_job(path):
        record = json.loads(path.read_text()); plan = record['plan']
        if plan_sha256 != plan['plan_sha256']:
            raise ValueError('Approval does not match the displayed plan')
        amount = Decimal(str(max_cost_usd))
        if not amount.is_finite() or amount < Decimal(plan['quote']['estimated_cost_usd']):
            raise ValueError('Approval must cover the displayed estimate')
        if len(statement.strip()) < 10 or len(statement) > 2000:
            raise ValueError('Record the human authorization for this specific job')
        if now() >= datetime.fromisoformat(plan['quote']['valid_until']):
            raise ValueError('Refresh the price quotation before authorization')
        if record['status'] in STATES_AFTER_SUBMIT:
            raise ValueError('This job has already been submitted; recover it without a new paid call')
        record.update(status='approved', approval={'plan_sha256': plan_sha256,
                      'max_cost_usd': str(amount), 'statement': statement.strip(),
                      'approved_at': now().isoformat(), 'expires_at': (now()+timedelta(hours=24)).isoformat()})
        atomic(path, record)
        return record


def withdraw(project, job_id):
    path = path_for(project, job_id)
    with lock_job(path):
        record = json.loads(path.read_text())
        if record.get('generation_submitted'):
            raise ValueError('An already submitted generation cannot be withdrawn locally')
        record.update(status='withdrawn', approval=None)
        atomic(path, record)
        return record


class XaiTransport:
    """Credentials stay in memory; polling stays on the issuing account."""
    def __init__(self, credential_identity=None):
        self.credentials = _credentials(credential_identity=credential_identity)
        self.credential_identity = self.credentials['credential_identity']

    def request(self, method, endpoint, **kwargs):
        response = requests.request(method, self.credentials['base_url'].rstrip('/') + endpoint,
            headers={'Authorization': 'Bearer '+self.credentials['api_key'],
                     'Content-Type': 'application/json', 'User-Agent': self.credentials['user_agent']},
            timeout=45, allow_redirects=False, **kwargs)
        return response

    def download(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or not any(
                parsed.hostname == d or parsed.hostname.endswith('.'+d) for d in ('x.ai', 'xaiusercontent.com')):
            raise ValueError('Unexpected provider media download host')
        response = requests.get(url, timeout=90, allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 200 * 1024 * 1024:
            raise ValueError('Generated asset exceeds download limit')
        return response.content


def payload(project, plan):
    refs = []
    for item in plan['input_references']:
        path = media_file(project, item['path'])
        if digest_file(path) != item['sha256']:
            raise ValueError('A pinned reference image changed; prepare a new quote')
        pix = fitz.Pixmap(str(path))
        uri = 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode()
        refs.append({'url': uri, 'type': 'image_url'})
    body = {'model': plan['model'], 'prompt': plan['prompt'], 'aspect_ratio': plan['aspect_ratio'],
            **plan['parameters']}
    if plan['media_type'] == 'image':
        body.update(n=1, deferred=True, response_format='url')
        if refs:
            body['images'] = refs
        endpoint = '/images/edits' if refs else '/images/generations'
    else:
        if refs:
            body['reference_images'] = [{'url': r['url']} for r in refs]
        endpoint = '/videos/generations'
    # No catbox upload, permanent public storage or serialized bearer/data URI.
    return endpoint, body


def save_result(project, record, data, transport):
    plan = record['plan']; directory = project / 'generations' / record['job_id']
    directory.mkdir(parents=True, exist_ok=True)
    if plan['media_type'] == 'image':
        item = (data.get('data') or [{}])[0]
        raw = base64.b64decode(item['b64_json']) if item.get('b64_json') else transport.download(item['url'])
    else:
        raw = transport.download(data['video']['url'])
    # Preserve the provider's original bytes even when decoding/probing rejects
    # them, and preserve a JPEG source separately from its PNG working copy.
    source_hash = hashlib.sha256(raw).hexdigest()
    source = directory / ('source-'+source_hash[:24]+('.mp4' if plan['media_type']=='video' else '.bin'))
    if not source.exists():
        temporary_source = source.with_name('.'+source.name+'-'+uuid.uuid4().hex)
        temporary_source.write_bytes(raw)
        temporary_source.replace(source)
    source_record = {'path': str(source.relative_to(project)), 'sha256': source_hash}
    originals = record.setdefault('provider_originals', [])
    if source_record not in originals:
        originals.append(source_record)
    if plan['media_type'] == 'image':
        image = fitz.Pixmap(raw); raw = image.tobytes('png')
        result = {'width': image.width, 'height': image.height}
        extension = '.png'
    else:
        extension = '.mp4'; result = {}
    output = directory / ('candidate' + extension)
    temporary = directory / ('.download-'+uuid.uuid4().hex+extension)
    temporary.write_bytes(raw)
    try:
        if extension == '.mp4':
            result = inspect(temporary)
            if not any(v.get('codec_type') == 'video' for v in result.get('streams', [])):
                raise ValueError('Provider output contains no video')
            actual = float(result['format']['duration'])
            if abs(actual-plan['parameters']['duration']) > .5:
                raise ValueError('Provider output duration differs from the planned source')
        if output.exists() and digest_file(output) != hashlib.sha256(raw).hexdigest():
            # Retain every recovered provider result, including unexpected variants.
            output = directory / ('candidate-'+uuid.uuid4().hex[:8]+extension)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    record.update(status='completed', output=str(output.relative_to(project)),
                  output_sha256=digest_file(output), probe=result, finished_at=now().isoformat(), error=None)
    return record


def run_generation(project, job_id, *, transport=None):
    """One submit OR one poll/download; repeated recovery never submits twice."""
    path = path_for(project, job_id)
    with lock_job(path):
        record = json.loads(path.read_text()); plan = record['plan']
        if record['status'] == 'completed' and record.get('output'):
            output = project / record['output']
            if output.is_file() and digest_file(output) == record.get('output_sha256'):
                return record
        remote = record.get('remote_request_id')
        if not remote:
            if record['status'] in STATES_AFTER_SUBMIT:
                if record['status'] == 'submitting':
                    record['status'] = 'submission_unknown'; atomic(path, record)
                return record
            approval = record.get('approval') or {}
            if record['status'] != 'approved' or approval.get('plan_sha256') != plan['plan_sha256']:
                raise ValueError('Human cost authorization is required before generation')
            if now() >= min(datetime.fromisoformat(approval['expires_at']),
                            datetime.fromisoformat(plan['quote']['valid_until'])):
                raise ValueError('Authorization or price quotation expired')
            endpoint, body = payload(project, plan)
            transport = transport or XaiTransport()
            record.update(status='submitting', generation_submitted=True,
                          submitted_at=now().isoformat(),
                          credential_identity=transport.credential_identity)
            atomic(path, record)  # persisted BEFORE the external side effect
            try:
                response = transport.request('POST', endpoint, json=body)
                if response.status_code >= 400:
                    record.update(status='rejected' if response.status_code < 500 else 'submission_unknown',
                                  error='Provider did not accept or confirm the request; no automatic resubmission.',
                                  provider_http_status=response.status_code)
                else:
                    data = response.json(); remote = data.get('request_id')
                    if remote:
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', remote):
                            raise ValueError('Invalid provider request ID')
                        record.update(status='pending', remote_request_id=remote)
                        atomic(path, record)
                    elif data.get('data') or data.get('video'):
                        record = save_result(project, record, data, transport)
                    else:
                        record.update(status='submission_unknown', error='No recoverable provider request ID returned.')
            except Exception as exc:
                record.update(status='pending' if record.get('remote_request_id') else 'submission_unknown',
                              error='Submission interrupted; recover the saved request without resubmitting.',
                              error_type=type(exc).__name__)
            atomic(path, record)
            return record
        transport = transport or XaiTransport(credential_identity=record.get('credential_identity'))
        # Pool rotation must never send a paid job to an unrelated account.
        if record.get('credential_identity') != transport.credential_identity:
            raise ValueError('Select the issuing Hermes credential before recovering this job')
        try:
            endpoint = '/images/' if plan['media_type'] == 'image' else '/videos/'
            response = transport.request('GET', endpoint + remote)
            if response.status_code not in {200, 202}:
                record.update(status='pending', error='Provider status unavailable; retry recovery, not generation.',
                              provider_http_status=response.status_code)
            else:
                data = response.json(); state = data.get('status')
                if state == 'failed':
                    record.update(status='provider_failed', error='Provider reports this generation failed; no automatic paid retry.')
                elif state == 'done' or (not state and (data.get('data') or data.get('video'))):
                    try:
                        record = save_result(project, record, data, transport)
                    except Exception as exc:
                        record.update(status='download_failed', error='Result download/validation failed; recover this request.',
                                      error_type=type(exc).__name__)
                else:
                    record.update(status='pending', error=None)
        except Exception as exc:
            record.update(status='pending', error='Status request interrupted; recover this same request.', error_type=type(exc).__name__)
        atomic(path, record)
        return record
