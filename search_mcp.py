"""Local MCP search router. Runs on Python 3.12 and uses uvx for Zhihu."""

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / 'search-keys.env.local'
KEY_NAMES = {
    'tavily': 'TAVILY_API_KEY', 'keenable': 'KEENABLE_API_KEY',
    'zhihu': 'ZHIHU_ACCESS_SECRET', 'serper': 'SERPER_API_KEY',
}
SEARCH_APIS = {
    'tavily': ('https://api.tavily.com/search', 'results'),
    'keenable': ('https://api.keenable.ai/v1/search', 'results'),
    'serper': ('https://google.serper.dev/search', 'organic'),
}
ROUTES = {
    'china': ['zhihu', 'serper', 'tavily', 'keenable'],
    'research': ['keenable', 'tavily', 'serper', 'zhihu'],
    'official': ['tavily', 'serper', 'keenable', 'zhihu'],
    'general': ['serper', 'tavily', 'keenable', 'zhihu'],
}
MCP_VERSIONS = {'2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'}


class ProviderError(Exception):
    def __init__(self, code, http_status=None):
        self.details = {'status': code}
        if http_status is not None:
            self.details['http_status'] = http_status


def error_code(status, body):
    message = body.casefold()
    if status in (402, 432) or re.search(
        r'insufficient[_ ](?:free[_ ])?(?:credits?|balance|quota)|(?:not enough|out of|no remaining) (?:credits?|balance|quota)|(?:credits?|balance|quota).{0,22}(?:exhausted|depleted|exceeded)|余额不足|额度不足|额度已用完|30002', message
    ):
        return 'insufficient_quota'
    if status == 401 or re.search(r'(?:invalid|missing|malformed) (?:api key|access secret|token)|invalid_api_key|unauthorized|鉴权失败|无效.{0,8}(?:密钥|凭证)', message):
        return 'invalid_key'
    if status == 403:
        return 'access_denied'
    if status == 429:
        return 'rate_limited'
    if status == 400:
        return 'request_rejected'
    if status and status >= 500:
        return 'upstream_error'
    return 'provider_error'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(provider, key, url, payload=None):
    auth = {'Authorization': f'Bearer {key}'} if provider == 'tavily' else {'X-API-Key': key}
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, headers={**auth, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            message = exc.read(8192).decode('utf-8', errors='replace')
        raise ProviderError(error_code(exc.code, message), exc.code) from None
    except (OSError, TimeoutError):
        raise ProviderError('network_error') from None
    except ValueError:
        raise ProviderError('invalid_response') from None
    if not isinstance(result, dict):
        raise ProviderError('invalid_response')
    return result


def zhihu(key, *args):
    env = dict(os.environ)
    env.update(PYTHONIOENCODING='utf-8', ZHIHU_ACCESS_SECRET=key, ZHIHU_ALLOW_PAID='false')
    env.setdefault('ZHIHU_SEARCH_HOME', str(ROOT / 'zhihu-state'))
    env.setdefault('UV_CACHE_DIR', str(Path(tempfile.gettempdir()) / 'codex-search-uv-cache'))
    env.setdefault('UV_TOOL_DIR', str(Path(tempfile.gettempdir()) / 'codex-search-uv-tools'))
    env.setdefault('UV_HTTP_RETRIES', '0')
    env.setdefault('UV_HTTP_TIMEOUT', '10')
    uvx = Path.home() / '.local' / 'bin' / ('uvx.exe' if os.name == 'nt' else 'uvx')
    options = list(args)
    format_position = options.index('--') if '--' in options else len(options)
    options[format_position:format_position] = ['--format', 'json']
    command = [str(uvx) if uvx.is_file() else 'uvx', '--no-config',
               '--default-index', 'https://pypi.org/simple', 'zhihu-search==3.2.0', *options]
    try:
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', env=env, timeout=25, check=False)
    except FileNotFoundError:
        raise ProviderError('dependency_missing') from None
    except subprocess.TimeoutExpired:
        raise ProviderError('timeout') from None
    except OSError:
        raise ProviderError('local_error') from None
    try:
        response = json.loads(process.stdout)
    except ValueError:
        raise ProviderError('invalid_response' if not process.returncode else 'dependency_or_network_error') from None
    if not isinstance(response, dict):
        raise ProviderError('invalid_response')
    if process.returncode or not response.get('success'):
        message = str(response.get('error', ''))
        status_match = re.search(r'(?<!\d)(401|402|403|429|5\d\d)(?!\d)', message)
        status = int(status_match[0]) if status_match else None
        if '本地保护存储' in message:
            raise ProviderError('local_state_unwritable')
        raise ProviderError(error_code(status, message), status)
    return response.get('data')


def count(value):
    return value if type(value) is int and value >= 0 else None


def difference(data, limit_name, used_name):
    limit, used = count(data.get(limit_name)), count(data.get(used_name))
    return max(0, limit - used) if limit is not None and used is not None else None


def quota(provider, key):
    if provider == 'keenable':
        return {'remaining': None, 'source': 'reported_after_search'}
    if provider == 'serper':
        data = request_json(provider, key, 'https://google.serper.dev/account')
        return {'remaining': count(data.get('balance')), 'unit': 'credits', 'source': 'account_api'}
    if provider == 'zhihu':
        data = zhihu(key, 'quota', '--api-id', 'zhihu_search')
        for entry in data if isinstance(data, list) else []:
            if isinstance(entry, dict) and entry.get('APIID') == 'zhihu_search':
                return {'remaining': count(entry.get('RemainingQuota')), 'unit': 'free_searches', 'source': 'quota_api'}
        return {'remaining': None, 'source': 'quota_api'}
    data = request_json(provider, key, 'https://api.tavily.com/usage')
    key_data, account = data.get('key') or {}, data.get('account') or {}
    key_left = difference(key_data, 'limit', 'usage')
    plan_left = difference(account, 'plan_limit', 'plan_usage')
    paygo_left = difference(account, 'paygo_limit', 'paygo_usage')
    known = [amount for amount in (key_left, plan_left) if amount is not None]
    return {'remaining': min(known, default=None), 'unit': 'plan_credits',
            'source': 'usage_api', 'key_remaining': key_left,
            'plan_remaining': plan_left, 'paygo_remaining': paygo_left}


def search_provider(provider, key, query, limit):
    if provider == 'zhihu':
        data = zhihu(key, 'search', '--scope', 'zhihu', '--count', str(limit), '--', query)
        items = data.get('Items') if isinstance(data, dict) else None
        fields = 'Title', 'Url', 'ContentText'
        remaining = None
    else:
        url, field = SEARCH_APIS[provider]
        payload = {'q': query, 'num': limit} if provider == 'serper' else {'query': query, 'max_results': limit}
        if provider == 'tavily':
            payload['search_depth'] = 'basic'
        data = request_json(provider, key, url, payload)
        items = data.get(field, [] if provider == 'serper' and 'searchParameters' in data else None)
        fields = ('title', 'link', 'snippet') if provider == 'serper' else ('title', 'url', 'content' if provider == 'tavily' else 'snippet')
        usage = data.get('usage')
        remaining = count(usage.get('credits_remaining')) if isinstance(usage, dict) else None
    if not isinstance(items, list):
        raise ProviderError('invalid_response')
    title, url, snippet = fields
    results = [{'title': str(item.get(title, ''))[:300], 'url': item[url],
                'snippet': str(item.get(snippet, ''))[:1200]}
               for item in items[:limit] if isinstance(item, dict) and isinstance(item.get(url), str)
               and item[url].startswith(('https://', 'http://'))]
    return results, remaining


def infer_topic(query):
    if re.search(r'知乎|中国大陆|国内|小红书|豆瓣|B站|(?i:site:zhihu\.com)', query):
        return 'china'
    if re.search(r'签证|官方|(?i:\b(?:whv|visa|official|government)\b)', query):
        return 'official'
    if re.search(r'论文|科研|学术|(?i:\b(?:arxiv|openreview|research|iclr|neurips|benchmark)\b)', query):
        return 'research'
    return 'china' if re.search(r'[\u4e00-\u9fff]', query) else 'general'


class SearchRouter:
    def __init__(self):
        self.keys = {}
        self.cache = {}

    def load_keys(self):
        values = {}
        if KEY_FILE.exists():
            for line in KEY_FILE.read_text(encoding='utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                if name.strip() in KEY_NAMES.values():
                    values[name.strip()] = value.strip()
        for provider, name in KEY_NAMES.items():
            key = values.get(name) or os.environ.get(name, '').strip()
            if self.keys.get(provider) != key:
                self.cache.pop(provider, None)
            self.keys[provider] = key

    def provider_status(self, provider, refresh=False):
        key = self.keys[provider]
        if not key:
            return {'status': 'missing_key', 'remaining': None}
        cached = self.cache.get(provider)
        ttl = 300 if cached and cached.get('source') == 'last_search' else 60
        if cached and (not refresh or provider == 'keenable') and time.time() - cached['checked_at'] < ttl:
            return cached
        try:
            info = quota(provider, key)
            status = 'unknown_quota' if info['remaining'] is None else 'insufficient_quota' if info['remaining'] == 0 else 'available'
            info['status'] = status
        except ProviderError as exc:
            info = {**exc.details, 'remaining': None}
        info['checked_at'] = int(time.time())
        self.cache[provider] = info
        return info

    def all_statuses(self, providers, refresh=False):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return dict(zip(providers, pool.map(lambda provider: self.provider_status(provider, refresh), providers)))

    def search(self, query, topic='auto', provider='auto', limit=3):
        topic = infer_topic(query) if topic == 'auto' else topic
        candidates = ROUTES[topic] if provider == 'auto' else [provider]
        statuses = self.all_statuses(candidates)
        ordered = sorted(candidates, key=lambda name: 0 < (statuses[name]['remaining'] or 0) <= 5)
        attempts = []
        allowed = {'available', 'unknown_quota', 'network_error', 'upstream_error', 'provider_error', 'invalid_response'}
        for name in ordered:
            info = statuses[name]
            if info['status'] not in allowed:
                attempts.append({'provider': name, 'action': 'skipped', **info})
                continue
            try:
                results, remaining = search_provider(name, self.keys[name], query, limit)
            except ProviderError as exc:
                attempts.append({'provider': name, 'action': 'failed', **exc.details})
                if exc.details['status'] in {'invalid_key', 'access_denied', 'insufficient_quota', 'rate_limited'}:
                    self.cache[name] = {**info, **exc.details, 'remaining': 0 if exc.details['status'] == 'insufficient_quota' else None,
                                        'source': 'search_error', 'checked_at': int(time.time())}
                continue
            updated = dict(info)
            if remaining is not None:
                updated.update(remaining=remaining, unit='credits', source='last_search', checked_at=int(time.time()))
            elif info['remaining'] is not None:
                updated.update(remaining=max(0, info['remaining'] - 1), source='estimated_after_search')
            updated['status'] = ('unknown_quota' if updated['remaining'] is None else
                                 'insufficient_quota' if updated['remaining'] == 0 else 'available')
            self.cache[name] = updated
            attempts.append({'provider': name, 'action': 'searched', 'status': 'ok' if results else 'no_results'})
            if results or provider != 'auto':
                return {'status': 'ok' if results else 'no_results', 'topic': topic, 'provider': name,
                        'quota': updated, 'results': results, 'attempts': attempts}
        status = 'no_results' if any(entry['status'] == 'no_results' for entry in attempts) else 'all_providers_unavailable'
        return {'status': status, 'topic': topic, 'provider': None, 'results': [], 'attempts': attempts}


TOOLS = [
    {'name': 'search_status', 'description': 'Inspect search provider availability and remaining credits. A null balance is unknown. Refresh queries available balance APIs without running searches.',
     'inputSchema': {'type': 'object', 'properties': {'refresh': {'type': 'boolean', 'default': False}}, 'additionalProperties': False}},
    {'name': 'web_search', 'description': 'Search with one of four providers. Choose topic from the user intent: china for domestic Chinese questions and Zhihu discussions, research for papers, official for government or official sites, general otherwise. Auto uses topic and quota, falls back on errors, and reports attempts. Pin a provider for comparisons or an explicitly requested source.',
     'inputSchema': {'type': 'object', 'required': ['query'], 'additionalProperties': False,
                     'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                                    'topic': {'type': 'string', 'enum': ['auto', *ROUTES]},
                                    'provider': {'type': 'string', 'enum': ['auto', *KEY_NAMES]},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}}},
]


def call_tool(router, params):
    name, args = params.get('name'), params.get('arguments', {})
    if not isinstance(args, dict):
        raise ValueError('Arguments must be an object.')
    router.load_keys()
    if name == 'search_status' and set(args) <= {'refresh'} and type(args.get('refresh', False)) is bool:
        data = router.all_statuses(list(KEY_NAMES), args.get('refresh', False))
    elif name == 'web_search':
        query, topic, provider, limit = (args.get('query'), args.get('topic', 'auto'),
                                         args.get('provider', 'auto'), args.get('limit', 3))
        if (set(args) - {'query', 'topic', 'provider', 'limit'} or not isinstance(query, str)
                or not 1 <= len(query.strip()) <= 1000 or topic not in ['auto', *ROUTES]
                or provider not in ['auto', *KEY_NAMES] or type(limit) is not int or not 1 <= limit <= 10):
            raise ValueError('Invalid search arguments.')
        data = router.search(query.strip(), topic, provider, limit)
    else:
        raise ValueError('Unknown tool or invalid arguments.')
    return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}],
            'structuredContent': data, 'isError': data.get('status') == 'all_providers_unavailable'}


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    router = SearchRouter()
    for line in sys.stdin:
        request_id = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError('Expected a JSON-RPC object.')
            if 'id' not in message:
                continue
            request_id = message['id']
            method, params = message.get('method'), message.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('Expected an object for params.')
            if method == 'initialize':
                requested = params.get('protocolVersion')
                version = requested if isinstance(requested, str) and requested in MCP_VERSIONS else '2025-11-25'
                result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'search-router', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                result = call_tool(router, params)
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found.'}}), flush=True)
                continue
            response = {'result': result}
        except json.JSONDecodeError:
            response = {'error': {'code': -32700, 'message': 'Invalid JSON.'}}
        except (ValueError, TypeError):
            response = {'error': {'code': -32602, 'message': 'Invalid arguments.'}}
        except (OSError, AttributeError):
            response = {'error': {'code': -32603, 'message': 'Local settings or provider response could not be read.'}}
        print(json.dumps({'jsonrpc': '2.0', 'id': request_id, **response}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
