#!/usr/bin/env python3
"""Courseflow 超星模块入口：标准输出只传输 NDJSON 协议。"""
import json
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.dont_write_bytecode = True

try:
    SETTINGS = json.loads(os.environ.get('CF_MODULE_SETTINGS', '{}'))
    if not isinstance(SETTINGS, dict) or SETTINGS.get('source', 'live') not in ('live', 'fixture'):
        raise ValueError()
except (ValueError, TypeError):
    sys.stderr.write('模块配置必须是 JSON 对象，source 只能为 live 或 fixture\n')
    raise SystemExit(2)
python = SETTINGS.get('python')
if python and str(Path(sys.executable).absolute()) != str(Path(python).absolute()):
    if not Path(python).is_absolute() or not Path(python).is_file():
        sys.stderr.write('python 必须指向服务器上的 Python 可执行文件\n')
        raise SystemExit(2)
    os.execv(python, [python, str(Path(__file__).absolute())])

from cx.platform import Platform, Fault

OUTPUT_LOCK = threading.Lock()
MAX_LINE = 8 * 1024 * 1024

def send(message):
    with OUTPUT_LOCK:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n')
        sys.stdout.flush()

class Host:
    """仅主线程读取协议，按编号把宿主响应交还给等待中的工作线程。"""
    def __init__(self):
        self.counter = 1000000
        self.pending = {}
        self.lock = threading.Lock()
        self.context = threading.local()
        self.closed = False

    def call(self, method, params):
        remaining = getattr(self.context, 'deadline', time.monotonic()+27) - time.monotonic()
        if remaining <= .05:
            raise Fault('platform_unavailable', '超星请求等待过久，请稍后重试')
        if method == 'cw.net.fetch':
            params = {**params, 'timeout_ms': min(params.get('timeout_ms', 10000), max(1, int((remaining-.05)*1000)))}
        event, reply = threading.Event(), []
        with self.lock:
            if self.closed: raise EOFError()
            self.counter += 1
            ident = self.counter
            self.pending[ident] = (event, reply)
        send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        ready = event.wait(remaining)
        with self.lock:
            self.pending.pop(ident, None)
        if not ready:
            raise Fault('platform_unavailable', '超星请求等待过久，请稍后重试')
        if not reply: raise EOFError()
        message = reply[0]
        if 'error' in message:
            error = message['error']
            raise Fault(error.get('code', 'host_error'), error.get('message', '宿主调用失败'))
        return message.get('result')

    def receive(self, message):
        with self.lock:
            waiting = self.pending.get(message.get('id'))
            if waiting:
                event, reply = waiting
                reply.append(message)
                event.set()

    def close(self):
        with self.lock:
            self.closed = True
            for event, _ in self.pending.values(): event.set()

def main():
    host = Host()
    platform = Platform(host, SETTINGS)
    queues, queue_lock = {}, threading.Lock()
    budget = 27.0

    def handle(request, deadline):
        host.context.deadline = deadline
        try:
            if time.monotonic()+.05 >= deadline:
                raise Fault('platform_unavailable', '超星请求排队过久，请稍后重试')
            result = platform.dispatch(request['method'], request.get('params') or {})
            response = {'result': result}
        except Fault as error:
            response = {'error': {'code': error.code, 'message': str(error)}}
        except EOFError:
            return
        except Exception as error:
            sys.stderr.write('模块调用失败：%s\n' % type(error).__name__)
            response = {'error': {'code': 'platform_protocol_changed', 'message': '平台页面或返回值无法识别，请核对官方页面'}}
        if not host.closed:
            send({'jsonrpc': '2.0', 'id': request['id'], **response})

    def drain(key):
        while not host.closed:
            with queue_lock:
                if not queues[key]:
                    del queues[key]
                    return
                request, deadline = queues[key].popleft()
            handle(request, deadline)

    # 同一会话仍顺序处理；登录单独保留线程，目录或学习请求不会堵住扫码。
    workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix='cx-work')
    auth_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='cx-auth')
    try:
        while True:
            line = sys.stdin.readline(MAX_LINE+1)
            if not line: break
            if len(line) > MAX_LINE:
                sys.stderr.write('协议消息超过限制\n')
                break
            try:
                request = json.loads(line)
            except ValueError:
                sys.stderr.write('忽略无效 JSON 协议行\n')
                continue
            if not isinstance(request, dict): continue
            if 'method' not in request:
                host.receive(request)
                continue
            if 'id' not in request or not isinstance(request['method'], str): continue
            if request['method'] == 'platform.describe':
                limits = (request.get('params') or {}).get('limits') or {}
                budget = min(27.0, max(.001, number_timeout(limits.get('call_timeout_ms'))*.9))
                handle(request, time.monotonic()+budget)
                continue
            auth = request['method'].startswith('auth.') and request['method'] not in ('auth.verify', 'auth.keepalive')
            key = (auth, str((request.get('params') or {}).get('session', '')))
            with queue_lock:
                new = key not in queues
                queues.setdefault(key, deque()).append((request, time.monotonic()+budget))
                if new: (auth_workers if auth else workers).submit(drain, key)
    finally:
        host.close()
        workers.shutdown(wait=True, cancel_futures=True)
        auth_workers.shutdown(wait=True, cancel_futures=True)

def number_timeout(value):
    return value/1000 if isinstance(value, (int, float)) and value > 0 else 30

if __name__ == '__main__':
    main()
