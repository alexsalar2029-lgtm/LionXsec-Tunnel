"""OpenAI requests with explicit output structure and no secret-bearing errors."""
import base64
import json
from urllib.parse import quote

import requests

OPENAI_API = 'https://api.openai.com/v1'
CONTENT_TYPES = {'educational': 'آموزشی', 'news': 'خبری', 'event': 'رویداد',
                 'product': 'معرفی محصول', 'article': 'مقاله', 'personal': 'شخصی', 'other': 'عمومی'}
ANALYSIS_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'decision': {'type': 'string', 'enum': ['ready', 'review', 'blocked']},
        'title': {'type': 'string'}, 'body': {'type': 'string'}, 'reason': {'type': 'string'},
        'content_type': {'type': 'string', 'enum': list(CONTENT_TYPES)},
        'source_date': {'type': 'string'},
    },
    'required': ['decision', 'title', 'body', 'reason', 'content_type', 'source_date'],
}


class AIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_key(key):
    if not isinstance(key, str) or not 10 <= len(key.strip()) <= 4096 or any(not 33 <= ord(c) <= 126 for c in key.strip()):
        raise ValueError('openai_key_invalid')
    return key.strip()


def _request(method, path, key, **kwargs):
    if not key:
        raise AIError('openai_key_missing')
    try:
        response = requests.request(method, OPENAI_API + path,
                                    headers={'Authorization': 'Bearer ' + key},
                                    timeout=kwargs.pop('timeout',(3,20)), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise AIError('openai_unavailable') from None
    codes = {401: 'openai_unauthorized', 403: 'openai_forbidden',
             404: 'openai_model_missing', 429: 'openai_rate_limited'}
    if response.status_code in codes:
        if response.status_code == 429:
            try:
                if response.json().get('error', {}).get('code') == 'insufficient_quota':
                    raise AIError('openai_quota_exceeded')
            except (ValueError, TypeError, AttributeError):
                pass
        raise AIError(codes[response.status_code])
    if not 200 <= response.status_code < 300:
        raise AIError('openai_request_failed' if response.status_code < 500 else 'openai_unavailable')
    try:
        return response.json()
    except ValueError:
        raise AIError('ai_invalid_response') from None


def check_openai(key, model):
    result = _request('GET', '/models/' + quote(model, safe=''), key)
    if not isinstance(result, dict) or not result.get('id'):
        raise AIError('ai_invalid_response')
    return {'provider': 'openai', 'model': result['id']}


def openai_analysis(prompt, filename, kind, text, images, model, key, transport=None):
    content = [{'type': 'input_text', 'text': json.dumps(
        {'filename': filename, 'file_type': kind, 'text': text[:12000]}, ensure_ascii=False)}]
    content += [{'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(image).decode()}
                for image in images]
    payload = {'model': model, 'instructions': prompt, 'store': False,
               'input': [{'role': 'user', 'content': content}], 'max_output_tokens': 1600,
               'text': {'format': {'type': 'json_schema', 'name': 'publisher_analysis',
                                   'strict': True, 'schema': ANALYSIS_SCHEMA}}}
    raw = transport(payload) if transport else _request('POST', '/responses', key, json=payload)
    try:
        if raw.get('status') == 'incomplete':
            raise AIError('ai_invalid_response')
        parts = [part for item in raw['output'] if item.get('type') == 'message'
                 for part in item.get('content', [])]
        if any(part.get('type') == 'refusal' for part in parts):
            raise AIError('ai_flagged_content')
        text = ''.join(part['text'] for part in parts if part.get('type') == 'output_text')
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AIError('ai_invalid_response') from None


def generate_background(prompt,model,key):
    result=_request('POST','/images/generations',key,timeout=(5,90),json={
        'model':model,'prompt':prompt,'n':1,'size':'1536x1024','quality':'low'})
    try: return base64.b64decode(result['data'][0]['b64_json'],validate=True)
    except (KeyError,IndexError,ValueError,TypeError): raise AIError('image_invalid_response') from None
