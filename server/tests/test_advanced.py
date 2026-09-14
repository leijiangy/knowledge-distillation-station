"""Real DSH runtime + local fake model; no production credentials or paid requests."""
import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from core import advanced
from core.ai import AIError


@pytest.fixture
def model_server(monkeypatch):
    pytest.importorskip('deepseek_harness')
    calls = []
    responses = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            plan = responses.pop(0)
            if plan.get('stall'):
                time.sleep(5)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [
                {'id': 'fake-model', 'model': 'deepseek-flash', 'created': 1,
                 'choices': [{'index': 0, 'delta': plan.get('delta', {'role': 'assistant', 'content': '解释完成。'}), 'finish_reason': None}]},
                {'id': 'fake-model', 'model': 'deepseek-flash', 'created': 1,
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': plan.get('finish', 'stop')}],
                 **({'usage': plan['usage']} if 'usage' in plan else {})},
            ]
            try:
                for chunk in chunks:
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(advanced.settings, 'DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(advanced.settings, 'DEEPSEEK_API', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(advanced.settings, 'DSH_MODEL', 'deepseek-flash')
    yield calls, responses
    server.shutdown()
    server.server_close()
    thread.join()


USAGE = {'prompt_tokens': 30, 'completion_tokens': 6, 'total_tokens': 36}


def run():
    return asyncio.run(advanced.execute(advanced.prepare('文章', '见 https://arxiv.org/abs/2301.00001', '当前段落')))


def test_runtime_has_only_read_tool_and_aggregates_actual_usage(model_server):
    calls, responses = model_server
    responses.extend([
        {'delta': {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'read1', 'type': 'function',
          'function': {'name': 'web_fetch', 'arguments': json.dumps({'url': 'http://127.0.0.1:9/private'})}}]},
         'finish': 'tool_calls', 'usage': USAGE},
        {'usage': USAGE},
    ])
    result = run()
    assert len(calls) == 2
    assert all([t['function']['name'] for t in call['tools']] == ['web_fetch'] for call in calls)
    assert 'non-public' in calls[1]['messages'][-1]['content']
    assert result['usage'] == {'prompt_tokens': 60, 'completion_tokens': 12, 'total_tokens': 72}
    assert result['finish_reason'] == 'stop'
    assert result['returned_model'] == 'deepseek-flash'
    assert result['response_id'] is None


@pytest.mark.parametrize('plan', [{}, {'usage': USAGE, 'finish': 'length'}])
def test_missing_usage_or_incomplete_output_cannot_be_billed(model_server, plan):
    calls, responses = model_server
    responses.append(plan)
    with pytest.raises(AIError):
        run()
    assert len(calls) == 1  # retry plugin is deliberately disabled


def test_budget_rejects_before_provider_dispatch(model_server):
    calls, _responses = model_server
    prepared = advanced.prepare('t', 'a', 's')
    prepared['prompt'] = 'x' * 110000
    with pytest.raises(AIError):
        asyncio.run(advanced.execute(prepared))
    assert calls == []


def test_changed_plugin_revision_does_not_execute(model_server):
    calls, _responses = model_server
    prepared = advanced.prepare('t', 'a', 's')
    prepared['plugin_revision'] = 'obsolete'
    with pytest.raises(AIError, match='配置已更新'):
        asyncio.run(advanced.execute(prepared))
    assert calls == []


def test_whole_run_timeout_closes_stalled_runtime(model_server, monkeypatch):
    calls, responses = model_server
    responses.append({'stall': True})
    monkeypatch.setattr(advanced, 'TIMEOUT_SECONDS', 2)
    started = time.monotonic()
    with pytest.raises(AIError):
        run()
    assert len(calls) == 1
    assert time.monotonic() - started < 6


@pytest.mark.parametrize('with_wire_total', [True, False])
def test_sdk_actual_usage_mapping_counts_cache_once(model_server, with_wire_total):
    _calls, responses = model_server
    usage = {**USAGE, 'prompt_cache_hit_tokens': 20}
    if not with_wire_total:
        del usage['total_tokens']
    responses.append({'usage': usage})
    result = run()
    assert result['usage'] == {'prompt_tokens': 30, 'completion_tokens': 6,
                               'total_tokens': 36, 'cache_hit_tokens': 20}


def test_advanced_quote_binds_membership_context_and_budget(monkeypatch):
    import main
    saved = []
    context = {'uid': 'user1', 'key': 'answer/1', 'commit': 'a' * 40,
               'segment_id': 'seg-a', 'seg_index': 0, 'segment_text': '文章段落',
               'item': {'title': '标题', 'content': '文章段落 https://github.com/org/repo'}, 'plan': {}}
    async def quote_context(_request, _body):
        return context, None
    async def account(_uid):
        return {'membership_tier': 'premium', 'membership_expires_at': '2099-01-01T00:00:00Z'}
    async def parent(*_args):
        return {'id': 1, 'content': '父解释'}
    async def create(**kwargs):
        saved.append(kwargs)
        return {'id': kwargs['operation_id'], 'action': 'advanced', 'status': 'quoted',
                'quoted_microcredits': kwargs['quote'].quoted_microcredits}
    monkeypatch.setattr(main.settings, 'BILLING_ENABLED', True)
    monkeypatch.setattr(main, '_quote_context', quote_context)
    monkeypatch.setattr(main.billing, 'account', account)
    monkeypatch.setattr(main.billing, 'create_quote_record', create)
    monkeypatch.setattr(main.reading_store, 'get_private_node', parent)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            return await client.post('/api/reading/quote', json={
                'action': 'advanced', 'parent_id': 1, 'idempotency_key': str(uuid4())})
    response = asyncio.run(request())
    assert response.status_code == 200, response.text
    assert response.json()['quoted_points'] == 120000
    assert len(saved) == 1
    record = saved[0]
    assert record['intent'].action == 'advanced'
    assert record['prepared_request']['executor'] == 'dsh'
    assert 'https://github.com/org/repo' in record['prepared_request']['prompt']
    assert record['policy'].prompt_template_revision.startswith('advanced-')
    assert record['request_hash_value'] != record['intent'].request_hash()


def test_worker_uses_dsh_and_existing_settlement(monkeypatch):
    import main
    events = []
    async def execute(prepared):
        assert prepared['executor'] == 'dsh'
        events.append('execute')
        return {'content': '高级解释', 'usage': {'total_tokens': 72}}
    async def record(operation_id, executor_token, result):
        assert (operation_id, executor_token) == ('op', 'worker')
        assert result['usage']['total_tokens'] == 72
        events.append('record')
    async def settle(operation_id):
        assert operation_id == 'op'
        events.append('settle')
    monkeypatch.setattr(main.advanced, 'execute', execute)
    monkeypatch.setattr(main.billing, 'record_result', record)
    monkeypatch.setattr(main.billing, 'finalize_operation', settle)
    asyncio.run(main._run_billing_operation({'id': 'op', 'executor_token': 'worker',
        'action': 'advanced', 'status': 'dispatched', 'messages_json': {'executor': 'dsh'}}))
    assert events == ['execute', 'record', 'settle']


def test_prepare_keeps_plain_text_links_and_marks_truncation():
    prepared = advanced.prepare('论文', 'x' * 20000, '见 https://github.com/org/repo', '关键观点')
    assert 'https://github.com/org/repo' in prepared['prompt']
    assert '已截取' in prepared['prompt']
    assert prepared['max_tokens'] + prepared['input_token_upper_bound'] == 120000


@pytest.mark.parametrize('tier,expires,status', [
    ('standard', '2099-01-01T00:00:00Z', 403),
    ('premium', '2000-01-01T00:00:00Z', 403),
    ('premium', '2099-01-01T00:00:00', 403),
    ('premium', None, 403),
    ('premium', '2099-01-01T00:00:00Z', None),
])
def test_membership_is_server_owned_and_expires(monkeypatch, tier, expires, status):
    import main
    async def account(_uid):
        return {'membership_tier': tier, 'membership_expires_at': expires}
    monkeypatch.setattr(main.billing, 'account', account)
    response = asyncio.run(main._require_advanced_member('user1'))
    assert (response.status_code if response is not None else None) == status


def test_recheck_membership_before_reserving(monkeypatch):
    import main
    async def account(_uid):
        return {'membership_tier': 'standard'}
    async def operation(_uid, _id):
        return {'action': 'advanced', 'status': 'quoted'}
    async def reserve(*_args):
        pytest.fail('non-member must never reserve credits')
    monkeypatch.setattr(main.settings, 'BILLING_ENABLED', True)
    monkeypatch.setattr(main, 'require_account', lambda _r: ('user1', None))
    monkeypatch.setattr(main.billing, 'account', account)
    monkeypatch.setattr(main.billing, 'get_operation', operation)
    monkeypatch.setattr(main.billing, 'reserve_operation', reserve)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            return await client.post('/api/reading/advanced', json={'operation_id': str(uuid4()), 'membership_tier': 'premium'})
    response = asyncio.run(request())
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'MEMBERSHIP_REQUIRED'
