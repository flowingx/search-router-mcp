"""Check the stdio MCP server and optionally run user-supplied live searches."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


SERVER = Path(__file__).with_name('search_mcp.py').resolve()
PROVIDERS = ('auto', 'tavily', 'keenable', 'zhihu', 'serper')


def config():
    print('[mcp_servers.search_router]')
    print('command = ' + json.dumps(sys.executable, ensure_ascii=False))
    print('args = [' + json.dumps(str(SERVER), ensure_ascii=False) + ']')
    print('tool_timeout_sec = 120')


def verify(checks, show_status):
    messages = []

    def send(method, params=None, notification=False):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if not notification:
            message['id'] = len(messages)
        messages.append(message)
        return message.get('id')

    initialized = send('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                     'clientInfo': {'name': 'verify', 'version': '1'}})
    send('notifications/initialized', notification=True)
    catalog = send('tools/list')
    status_id = send('tools/call', {'name': 'search_status'}) if show_status else None
    searches = [(provider, send('tools/call', {'name': 'web_search', 'arguments': {
        'provider': provider, 'query': query, 'limit': 1,
    }})) for provider, query in checks]
    request = ''.join(json.dumps(message) + '\n' for message in messages)
    process = subprocess.run([sys.executable, str(SERVER)], input=request, encoding='utf-8',
                             capture_output=True, timeout=30 + 120 * len(searches), check=True)
    replies = {reply['id']: reply for reply in map(json.loads, process.stdout.splitlines())}
    assert replies[initialized]['result']['serverInfo']['name'] == 'search-router'
    tools = {tool['name'] for tool in replies[catalog]['result']['tools']}
    assert {'search_status', 'web_search'} <= tools
    print('PASS MCP stdio handshake and tool discovery')
    if status_id is not None:
        statuses = replies[status_id]['result']['structuredContent']
        for name, entry in statuses.items():
            print(f"{name}: {entry['status']}; remaining={entry.get('remaining')}; source={entry.get('source', '-')}")

    passed = True
    for provider, reply_id in searches:
        data = replies[reply_id]['result']['structuredContent']
        ok = data['status'] == 'ok' and bool(data['results']) and (provider == 'auto' or data['provider'] == provider)
        print(f"{'PASS' if ok else 'FAIL'} search {provider}: {data['status']}; selected={data.get('provider')}")
        for attempt in data['attempts']:
            print(f"  {attempt['provider']}: {attempt['action']} ({attempt['status']})")
        if ok:
            print('  ' + data['results'][0]['url'])
        passed = passed and ok
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--print-config', action='store_true', help='Print a portable Codex TOML block for this checkout.')
    parser.add_argument('--status', action='store_true', help='Query available provider balances without running a search.')
    parser.add_argument('--check', nargs=2, metavar=('PROVIDER', 'QUERY'), action='append', default=[],
                        help='Run one live search. Repeat to test other providers. May consume credits.')
    args = parser.parse_args()
    for provider, query in args.check:
        if provider not in PROVIDERS or not 1 <= len(query.strip()) <= 1000:
            parser.error('Each check needs a known provider (or auto) and a 1–1000 character query.')
    if args.print_config:
        config()
        return 0
    try:
        return 0 if verify(args.check, args.status) else 1
    except (AssertionError, KeyError, ValueError, OSError, subprocess.SubprocessError):
        print('FAIL unable to complete the MCP protocol check', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
