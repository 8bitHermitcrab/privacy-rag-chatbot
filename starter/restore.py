"""채팅 화면용 개인정보 복원: 토큰 → 원래 값(전체) 또는 일부만 보이는 값(일부).

- LLM 에는 항상 가명처리된 글만 보냅니다. 복원은 답변이 나온 뒤 화면으로 보내기 직전에만 합니다.
- 어떤 종류를 어느 수준으로 보여줄지는 팀·직책 정책(auth.team_position_policy.restore_*)이 정합니다.
- 응답에는 허용된 토큰의 표시값만 담고, 화면이 토큰 자리에 그 값을 끼워 넣습니다. (본문은 가명 그대로)
- 일부 복원 값은 서버에서 만들어 보내므로 원래 값 전체가 브라우저로 가지 않습니다.
"""
import re

TOKEN_RE = re.compile(r'<([A-Z]+)_[0-9a-f]{8}>')
# 토큰 접두어 → 정책의 복원 종류. 번호류는 접두어가 숫자 모양으로 정해지므로 접두어 기준이 정확합니다.
PREFIX_KIND = {'PERSON': 'person', 'PHONE': 'phone', 'EMAIL': 'email', 'ADDRESS': 'address',
               'RRN': 'rrn', 'ACCOUNT': 'account', 'CARD': 'card'}
PARTIAL_ONLY = ('rrn', 'account', 'card')


def tokens_in(*texts):
    found = []
    for text in texts:
        for m in TOKEN_RE.finditer(text or ''):
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


def kind_of(token):
    m = TOKEN_RE.fullmatch(token)
    return PREFIX_KIND.get(m.group(1)) if m else None


def _digits(value):
    return re.sub(r'\D', '', value or '')


def partial(kind, normalized, original):
    """일부 복원 표시값. 원래 값의 앞뒤 일부만 남기고 나머지는 * 로 가립니다."""
    if kind == 'person':
        v = normalized or ''
        if len(v) <= 1:
            return '*'
        if len(v) == 2:
            return v[0] + '*'
        return v[0] + '*' * (len(v) - 2) + v[-1]
    if kind == 'phone':
        d = _digits(normalized)
        if len(d) < 7:
            return '*' * len(d)
        head = 2 if d.startswith('02') else 3
        return d[:head] + '-****-' + d[-4:]
    if kind == 'email':
        local, _, domain = (normalized or '').partition('@')
        return (local[:2] + '***@' + domain) if domain else '***'
    if kind == 'address':
        words = (original or normalized or '').split()
        if len(words) >= 3:
            return ' '.join(words[:2]) + ' ***'
        text = ''.join(words)
        return text[:5] + '***' if len(text) > 5 else '***'
    if kind == 'rrn':
        d = _digits(normalized)
        return d[:6] + '-*******' if len(d) >= 6 else '*******'
    if kind == 'account':
        d = _digits(normalized)
        return '*' * max(len(d) - 4, 0) + d[-4:] if len(d) > 4 else '****'
    if kind == 'card':
        d = _digits(normalized)
        return '****-****-****-' + d[-4:] if len(d) >= 4 else '****'
    return '***'


def full(kind, normalized, originals):
    if kind == 'person':
        return normalized
    # 표시용으로는 문서에 실제로 쓰인 모양(하이픈 등)을 우선 사용합니다.
    return (sorted(originals, key=len, reverse=True)[0] if originals else None) or normalized


def restore_map(tokens, levels, fetch):
    """허용된 토큰만 {token: {'kind','level','value'}} 로 돌려줍니다.

    levels: {'person': 'FULL', ...}. fetch(tokens) → {token: (normalized, originals)}.
    주민번호·계좌·카드는 정책값이 잘못 들어와도 일부까지만 보여줍니다.
    """
    wanted = {}
    for token in tokens:
        kind = kind_of(token)
        level = levels.get(kind, 'NONE') if kind else 'NONE'
        if kind in PARTIAL_ONLY and level == 'FULL':
            level = 'PARTIAL'
        if level in ('PARTIAL', 'FULL'):
            wanted[token] = (kind, level)
    if not wanted:
        return {}
    found = fetch(list(wanted))
    out = {}
    for token, (kind, level) in wanted.items():
        if token not in found:
            continue
        normalized, originals = found[token]
        value = full(kind, normalized, originals) if level == 'FULL' else partial(kind, normalized, (originals or [None])[0])
        if value:
            out[token] = {'kind': kind, 'level': level, 'value': value}
    return out


def fetch_from_vault(tokens):
    from .db import vault_conn
    with vault_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT token, normalized, originals FROM vault.token_map WHERE token = ANY(%s)', (tokens,))
        return {t: (n, list(o or [])) for t, n, o in cur.fetchall()}


def summary(restored):
    """복원 기록용 요약: 종류별 수준 (원래 값은 넣지 않음)."""
    levels = {}
    for item in restored.values():
        levels.setdefault(item['kind'], item['level'])
    return levels
