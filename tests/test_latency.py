"""模拟慢接口和并发协议，不访问平台或使用真实账号。"""
import json
import os
import select
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from cx.platform import Platform, Fault
from test_module import Host, COURSE, RAW, POINT


class CatalogLatencyTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.platform = Platform(self.host, {'source': 'live'})
        self.platform.state('s', 'courses', [RAW])

    def test_course_cache_expires_and_is_not_reused_by_another_session(self):
        course = '<div class="course"><a href="/a?cpi=33"></a><input class="clazzId" value="22"><input class="courseId" value="11"><span class="course-name">合成课程</span></div>'
        self.host.responses = ['', course]
        first = self.platform.courses({'session': 's'})
        self.assertEqual(first['courses'][0]['id'], '11')
        self.assertEqual(self.platform.courses({'session': 's'})['courses'], first['courses'])
        self.assertEqual(len(self.host.requests), 2)
        self.host.time += 30
        self.host.responses = ['', course]
        self.platform.courses({'session': 's'})
        self.assertEqual(len(self.host.requests), 4)
        self.host.responses = ['', course]
        self.platform.courses({'session': 'new'})
        self.assertEqual(len(self.host.requests), 6)

    def test_optional_requests_fail_fast_and_do_not_change_write_timeouts(self):
        self.host.responses = [Fault('platform_unavailable', '模拟超时'), '', '{}']
        self.assertIsNone(self.platform.api('s', 'https://mooc1-api.chaoxing.com/gas/clazz', optional=True))
        self.platform.request('s', 'https://mooc1.chaoxing.com/read')
        self.platform.api('s', 'https://mooc1.chaoxing.com/submit', stage='submit')
        self.assertEqual([r['timeout_ms'] for r in self.host.requests], [3000, 10000, 25000])
        self.assertEqual([r['commit'] for r in self.host.requests], [False, False, True])

    def test_folder_scan_keeps_the_previous_catalog_until_it_is_complete(self):
        old = {**RAW, 'courseId': '99'}
        self.platform.state('s', 'courses', [old])
        def card(ident):
            return f'<div class="course"><a href="/a?cpi=33"></a><input class="clazzId" value="22"><input class="courseId" value="{ident}"><span class="course-name">合成课程</span></div>'
        self.host.responses = ['<ul class="file-list"><li fileid="9"></li></ul>', card('11'), card('11')+card('12')]
        first = self.platform.courses({'session': 's'})
        self.assertEqual(self.platform.state('s', 'courses'), [old])
        last = self.platform.courses({'session': 's', 'cursor': first['next_cursor']})
        self.assertIsNone(last['next_cursor'])
        self.assertEqual([c['id'] for c in first['courses']+last['courses']], ['11', '12'])
        self.assertEqual([c['courseId'] for c in self.platform.state('s', 'courses')], ['11', '12'])
        self.assertEqual(len(self.platform.courses({'session': 's'})['courses']), 2)

    def test_parallel_cards_preserve_order_extra_pages_locks_and_shared_cache(self):
        points = [{**POINT, 'id': str(i), 'title': '合成章节'+str(i)} for i in range(1, 7)]
        points[-1]['locked'] = True
        cache = {'points': points, 'units': [], 'papers': [], 'finished': [], 'unfinished': [], 'nodes': {},
                 'seen': {}, 'scan': 'synthetic-scan', 'complete': False, 'cpi': RAW['cpi']}
        self.platform.state('s', 'outline:11/22:units', cache)
        gate = threading.Barrier(4)
        seen, lock = [], threading.Lock()

        def request(_session, _url, data):
            point, card = int(data['knowledgeid']), int(data['num'])
            with lock: seen.append((point, card))
            if point <= 4 and card == 0: gate.wait(timeout=2)
            if point == 5 or card > (1 if point == 1 else 0): return ''
            return 'mArg = '+json.dumps({'attachments': [{'jobid': f'{point}-{card}', 'type': 'video',
                'isPassed': point == 2, 'property': {'name': f'合成视频{point}-{card}'}}]})+';'

        self.platform.request = request
        cursor = {'phase': 'cards', 'point': 0, 'scan': 'synthetic-scan'}
        units = []
        for _ in range(10):
            page = self.platform.outline({'session': 's', 'course': COURSE, 'cursor': cursor}, 'units')
            units.extend(page['units'])
            cursor = page.get('next_cursor')
            if not cursor: break
        else: self.fail('目录分页没有结束')
        self.assertEqual([item['id'] for item in units], ['1-0', '2-0', '3-0', '4-0', '1-1', 'empty:5', 'locked:6'])
        self.assertEqual(units[1]['state'], 'completed')
        self.assertFalse(units[-1]['selectable'])
        self.assertNotIn((6, 0), seen)
        self.assertEqual(len(seen), 10)
        self.assertEqual(self.platform.outline({'session': 's', 'course': COURSE}, 'units')['units'], units)
        self.assertEqual(self.platform.outline({'session': 's', 'course': COURSE}, 'papers')['papers'], [])
        self.assertEqual(len(seen), 10, '完成目录供学习和测验共同复用')

    def test_stale_cursor_does_not_mix_two_scans(self):
        self.platform.state('s', 'outline:11/22:units', {'scan': 'new'})
        with self.assertRaisesRegex(Fault, '扫描已更新'):
            self.platform.outline({'session': 's', 'course': COURSE, 'cursor': {'phase': 'cards', 'scan': 'old'}}, 'units')
        self.assertEqual(self.host.requests, [])


class RpcProcess:
    def __init__(self):
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).parents[1]/'main.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'CF_MODULE_SETTINGS': '{"source":"live"}'})
        self.buffer = b''

    def send(self, value):
        self.process.stdin.write((json.dumps(value)+'\n').encode())
        self.process.stdin.flush()

    def call(self, ident, method, params):
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})

    def receive(self, timeout=3):
        until = time.monotonic()+timeout
        while b'\n' not in self.buffer:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, until-time.monotonic()))
            if not ready: raise AssertionError('模块没有及时返回协议消息')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk: raise AssertionError('模块意外退出')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def answer(self, message, result):
        self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})

    def close(self):
        self.process.stdin.close()
        try: self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise AssertionError('关闭协议后模块仍有遗留线程')
        finally:
            self.process.stdout.close()
            self.process.stderr.close()


class RpcLatencyTests(unittest.TestCase):
    def setUp(self):
        self.rpc = RpcProcess()
        self.addCleanup(self.rpc.close)

    def test_slow_catalog_does_not_block_password_login_or_describe(self):
        self.rpc.call(1, 'catalog.units', {'session': 'slow', 'course': COURSE})
        while True:
            message = self.rpc.receive()
            if message['method'] == 'cw.net.fetch': break
            key = message['params'].get('key')
            self.rpc.answer(message, {'value': [RAW] if key == 'courses' else None})
        # 保留目录请求未响应；登录必须能独立完成。
        self.rpc.call(2, 'auth.begin', {'session': 'fast', 'method': 'password'})
        self.rpc.call(3, 'platform.describe', {})
        results = {}
        while len(results) < 2:
            message = self.rpc.receive()
            if 'method' not in message:
                results[message['id']] = message
            elif message['method'] == 'cw.state': self.rpc.answer(message, {})
            elif message['method'] == 'cw.clock.now': self.rpc.answer(message, {'now': 100})
            else: self.fail('快速登录不应调用网络')
        self.assertEqual(results[2]['result']['challenge']['kind'], 'password')
        self.assertEqual(results[3]['result']['platform']['id'], 'cx')
        # tearDown 在宿主请求仍等待时关闭输入，同时验证关闭不会挂死。

    def test_out_of_order_host_responses_reach_the_correct_session(self):
        for ident, session in [(1, 'a'), (2, 'b')]: self.rpc.call(ident, 'auth.keepalive', {'session': session})
        requests = [self.rpc.receive(), self.rpc.receive()]
        self.assertTrue(all(r['method'] == 'cw.net.fetch' for r in requests))
        for request in reversed(requests):
            session = request['params']['session']
            self.rpc.answer(request, {'status': 200, 'body': ''})
            identity = self.rpc.receive()
            self.assertEqual(identity['method'], 'cw.session.identity')
            self.assertEqual(identity['params']['session'], session)
            self.rpc.answer(identity, {'user_id': session, 'display_name': session})
            result = self.rpc.receive()
            self.assertEqual(result['id'], 1 if session == 'a' else 2)
            self.assertEqual(result['result']['account_raw'], session)

    def test_expired_queued_calls_do_not_issue_late_network_requests(self):
        self.rpc.call(9, 'platform.describe', {'limits': {'call_timeout_ms': 150}})
        self.assertEqual(self.rpc.receive()['id'], 9)
        self.rpc.call(1, 'auth.keepalive', {'session': 'slow'})
        self.rpc.call(2, 'auth.keepalive', {'session': 'slow'})
        request = self.rpc.receive()
        self.assertEqual(request['method'], 'cw.net.fetch')
        self.assertLessEqual(request['params']['timeout_ms'], 135)
        first, second = self.rpc.receive(), self.rpc.receive()
        self.assertEqual([first['id'], second['id']], [1, 2])
        self.assertEqual([first['error']['code'], second['error']['code']], ['platform_unavailable']*2)


if __name__ == '__main__': unittest.main()
