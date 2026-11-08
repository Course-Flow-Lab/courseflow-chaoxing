#!/usr/bin/env python3
"""Courseflow 超星模块入口：标准输出只传输 NDJSON 协议。"""
import json
import os
import sys
from collections import deque
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

def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n')
    sys.stdout.flush()

class Host:
    """单个模块串行处理短调用；等待宿主响应时保留其他请求，不丢弃并发用户消息。"""
    def __init__(self):
        self.counter = 1000000
        self.queue = deque()

    def call(self, method, params):
        self.counter += 1
        ident = self.counter
        send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        for line in sys.stdin:
            if len(line) > 8 * 1024 * 1024:
                raise Fault('invalid_params', '协议消息超过限制')
            message = json.loads(line)
            if 'method' in message:
                self.queue.append(message)
            elif message.get('id') == ident:
                if 'error' in message:
                    error = message['error']
                    raise Fault(error.get('code', 'host_error'), error.get('message', '宿主调用失败'))
                return message.get('result')
        raise EOFError()

def main():
    host = Host()
    platform = Platform(host, SETTINGS)
    while True:
        if host.queue:
            request = host.queue.popleft()
        else:
            line = sys.stdin.readline(8 * 1024 * 1024 + 1)
            if not line:
                return
            try:
                request = json.loads(line)
            except ValueError:
                sys.stderr.write('忽略无效 JSON 协议行\n')
                continue
        if 'id' not in request or 'method' not in request:
            continue
        try:
            result = platform.dispatch(request['method'], request.get('params') or {})
            response = {'result': result}
        except Fault as error:
            response = {'error': {'code': error.code, 'message': str(error)}}
        except EOFError:
            return
        except Exception as error:
            sys.stderr.write('模块调用失败：%s\n' % type(error).__name__)
            response = {'error': {'code': 'platform_protocol_changed', 'message': '平台页面或返回值无法识别，请核对官方页面'}}
        send({'jsonrpc': '2.0', 'id': request['id'], **response})

if __name__ == '__main__':
    main()
