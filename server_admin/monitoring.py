"""분산기 요청 기록(requests.jsonl)을 모아 서버별 요청 수·오류·응답 시간과 데이터 버전 혼합 여부를 보여 줍니다."""
import json
import time
from collections import defaultdict

from .security import state_dir
from .proxy import REQUEST_LOG

WINDOWS = (15, 60, 360, 1440)


def _records(since):
    out = []
    for name in (REQUEST_LOG + '.previous', REQUEST_LOG):
        path = state_dir() / name
        if not path.exists():
            continue
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get('t', 0) >= since:
                    out.append(item)
    return out


def _p95(values):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * 0.95))]


def summary(minutes=60, now=None):
    if minutes not in WINDOWS:
        minutes = 60
    now = now or time.time()
    since = now - minutes * 60
    rows = _records(since)
    bucket = 60 if minutes <= 60 else 300 if minutes <= 360 else 1800
    start = int(since // bucket * bucket)
    slots = list(range(start, int(now) + 1, bucket))
    series = {key: [0] * len(slots) for key in ('node1', 'node2', 'none')}
    errors = [0] * len(slots)
    nodes = defaultdict(lambda: {'requests': 0, 'errors': 0, 'ms': [], 'versions': defaultdict(int), 'chat': 0})
    for r in rows:
        key = r.get('node') or 'none'
        i = min(len(slots) - 1, max(0, int((r['t'] - start) // bucket)))
        series.setdefault(key, [0] * len(slots))[i] += 1
        failed = r.get('status', 0) == 0 or r.get('status', 0) >= 500
        if failed:
            errors[i] += 1
        n = nodes[key]
        n['requests'] += 1
        n['errors'] += failed
        n['ms'].append(r.get('ms', 0))
        n['chat'] += r.get('kind') == 'chat'
        if r.get('version'):
            n['versions'][r['version']] += 1
    table = {}
    for key, n in nodes.items():
        table[key] = {'requests': n['requests'], 'errors': n['errors'], 'chat': n['chat'],
                      'avg_ms': round(sum(n['ms']) / len(n['ms'])) if n['ms'] else None, 'p95_ms': _p95(n['ms']),
                      'versions': dict(n['versions'])}
    versions = sorted({v for n in nodes.values() for v in n['versions']})
    recent = [{k: r.get(k) for k in ('t', 'node', 'version', 'kind', 'method', 'status', 'ms')} for r in rows[-40:]][::-1]
    return {'minutes': minutes, 'bucket_seconds': bucket, 'slots': slots, 'series': series, 'errors': errors,
            'nodes': table, 'versions_seen': versions, 'recent': recent, 'total': len(rows)}


def mixed_state():
    """두 서버가 지금 서로 다른 데이터 버전으로 실행 중인지와, 기록상 그 상태가 언제부터인지."""
    from .data_versions import running_version
    from .operations import NODES
    running = {node: running_version(node) for node in NODES}
    known = {v for v in running.values() if v}
    if len(known) < 2:
        return {'mixed': False, 'running': running}
    # 기록을 거꾸로 보며 두 버전이 모두 나오기 시작한 시각을 찾습니다 (최근 24시간 안에서).
    since = None
    seen_last = {}
    for r in reversed(_records(time.time() - 86400)):
        if r.get('node') and r.get('version'):
            seen_last.setdefault(r['node'], r['version'])
            if r['version'] != running.get(r['node']):
                break
            since = r['t']
    return {'mixed': True, 'running': running, 'since': since}
