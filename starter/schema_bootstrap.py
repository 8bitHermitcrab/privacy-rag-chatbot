"""기존 스타터 DB에 미리 정의된 기준 구조를 데이터 삭제 없이 보충합니다."""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
OWNERS = {'auth': 'migration_user', 'rag': 'rag_user', 'vault': 'vault_user'}


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def qualified(value):
    return '.'.join(ident(part) for part in value.split('.'))


def contract(name='baseline_contract.json'):
    return json.loads((ROOT / 'migrations' / name).read_text(encoding='utf-8'))


def index_signature(value):
    return re.sub(r'INDEX \S+ ON ', 'INDEX ON ', value)


def function_signature(value):
    return re.sub(r'\s+', '', value or '').lower()


def differences(actual, expected):
    """열 순서·기존 추가 열·동등한 제약 이름은 데이터 손실 없이 허용합니다."""
    issues = []
    for table, required in expected.items():
        if table == 'functions':
            found = dict(actual.get('functions', []))
            for name, body in required:
                if function_signature(found.get(name)) != function_signature(body):
                    issues.append(name + ': 갱신 함수 없음 또는 정의 다름')
            continue
        found = actual.get(table)
        if not found:
            issues.append(table + ': 테이블 없음')
            continue
        columns = {row[0]: row for row in found['columns']}
        for row in required['columns']:
            if row[0] not in columns:
                issues.append(table + '.' + row[0] + ': 열 없음')
            elif columns[row[0]] != row:
                issues.append(table + '.' + row[0] + ': 자료형·필수값·기본값·자동번호 정의 다름')
        definitions = {row[1] for row in found['constraints']}
        for name, value in required['constraints']:
            if value not in definitions:
                issues.append(table + '.' + name + ': 제약조건 없음 또는 정의 다름')
        indexes = {index_signature(row[1]) for row in found['indexes']}
        for name, value in required['indexes']:
            if index_signature(value) not in indexes:
                issues.append(table + '.' + name + ': 인덱스 없음 또는 정의 다름')
        triggers = dict(found['triggers'])
        for name, value in required['triggers']:
            if triggers.get(name) != value:
                issues.append(table + '.' + name + ': 트리거 없음 또는 정의 다름')
    return issues


def reconcile(connection, capture, target_contract='baseline_contract.json'):
    """호출자가 관리하는 단일 트랜잭션 안에서만 실행합니다. 실패 시 전체 롤백합니다."""
    target = contract(target_contract)
    original = capture(connection)
    # 자료형과 자동번호 변환은 데이터 의미를 바꿀 수 있어 자동 처리하지 않습니다.
    for table, required in target.items():
        if table == 'functions' or table not in original:
            continue
        cols = {row[0]: row for row in original[table]['columns']}
        for name, kind, mandatory, default, identity in required['columns']:
            old = cols.get(name)
            if old and (old[1] != kind or old[4] != identity):
                raise RuntimeError(f'{table}.{name}: 기존 자료형 {old[1]} / 자동번호 {old[4] or "없음"}, 필요한 자료형 {kind} / 자동번호 {identity or "없음"}. 자동 변환하지 않았습니다.')
            if old and old[3] != default and old[3].startswith('nextval('):
                raise RuntimeError(f'{table}.{name}: 기존 번호 생성 기본값을 자동 교체할 수 없습니다.')
        existing = dict(original[table]['constraints'])
        definitions = set(existing.values())
        for name, definition in required['constraints']:
            if name in existing and existing[name] != definition and definition not in definitions:
                raise RuntimeError(f'{table}.{name}: 기존 제약조건 정의가 달라 자동 교체하지 않았습니다.')

    for schema, owner in OWNERS.items():
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS {ident(schema)} AUTHORIZATION {ident(owner)}')
    for name, definition in target['functions']:
        current = dict(original.get('functions', [])).get(name)
        if current and function_signature(current) != function_signature(definition):
            raise RuntimeError(name + ': 기존 갱신 함수와 달라 덮어쓰지 않았습니다.')
        if not current:
            connection.exec_driver_sql(definition)

    # 열은 기존 순서를 유지하고 빠진 열만 추가합니다.
    for table, required in target.items():
        if table == 'functions':
            continue
        qtable = qualified(table)
        if table not in original:
            definitions = []
            for name, kind, mandatory, default, identity in required['columns']:
                field = ident(name) + ' ' + kind
                if identity:
                    field += ' GENERATED ' + ('ALWAYS' if identity == 'a' else 'BY DEFAULT') + ' AS IDENTITY'
                elif default:
                    field += ' DEFAULT ' + default
                if mandatory:
                    field += ' NOT NULL'
                definitions.append(field)
            connection.exec_driver_sql(f'CREATE TABLE {qtable} (' + ','.join(definitions) + ')')
            log.info('필수 테이블 생성: %s', table)
            continue
        columns = {row[0]: row for row in original[table]['columns']}
        for name, kind, mandatory, default, identity in required['columns']:
            old = columns.get(name)
            if not old:
                populated = connection.exec_driver_sql(f'SELECT EXISTS(SELECT 1 FROM {qtable})').scalar()
                if mandatory and not default and populated:
                    raise RuntimeError(f'{table}.{name}: 데이터가 있는 테이블에 필수 열을 임의 값으로 채울 수 없습니다.')
                field = ident(name) + ' ' + kind
                if identity:
                    field += ' GENERATED ' + ('ALWAYS' if identity == 'a' else 'BY DEFAULT') + ' AS IDENTITY'
                elif default:
                    field += ' DEFAULT ' + default
                if mandatory:
                    field += ' NOT NULL'
                connection.exec_driver_sql(f'ALTER TABLE {qtable} ADD COLUMN {field}')
                log.info('필수 열 추가: %s.%s', table, name)
            else:
                if old[3] != default:
                    action = 'SET DEFAULT ' + default if default else 'DROP DEFAULT'
                    connection.exec_driver_sql(f'ALTER TABLE {qtable} ALTER COLUMN {ident(name)} {action}')
                if old[2] != mandatory:
                    if mandatory and connection.exec_driver_sql(f'SELECT EXISTS(SELECT 1 FROM {qtable} WHERE {ident(name)} IS NULL)').scalar():
                        raise RuntimeError(f'{table}.{name}: NULL 데이터가 있어 필수값 제약을 적용하지 않았습니다.')
                    connection.exec_driver_sql(f'ALTER TABLE {qtable} ALTER COLUMN {ident(name)} ' + ('SET NOT NULL' if mandatory else 'DROP NOT NULL'))

    # 이미 적재된 청크의 문서 ID만 메타데이터로 보충하며 권한은 미확정으로 유지합니다.
    connection.exec_driver_sql('''INSERT INTO rag.document_info(document_id,document_type,metadata_status)
        SELECT doc_id, CASE WHEN count(DISTINCT doc_type)=1 THEN min(doc_type) ELSE NULL END, 'PENDING' FROM rag.chunks
        WHERE NOT EXISTS (SELECT 1 FROM rag.document_info d WHERE d.document_id=rag.chunks.doc_id)
        GROUP BY doc_id''')

    current = capture(connection)
    # 참조 대상 PK/UNIQUE를 먼저 만든 후 FK를 추가합니다.
    for foreign in (False, True):
        for table, required in target.items():
            if table == 'functions':
                continue
            definitions = {row[1] for row in current[table]['constraints']}
            for name, value in required['constraints']:
                if value.startswith('FOREIGN KEY') != foreign or value in definitions:
                    continue
                connection.exec_driver_sql(f'ALTER TABLE {qualified(table)} ADD CONSTRAINT {ident(name)} {value}')

    current = capture(connection)
    for table, required in target.items():
        if table == 'functions':
            continue
        definitions = {index_signature(row[1]) for row in current[table]['indexes']}
        for name, value in required['indexes']:
            if index_signature(value) not in definitions:
                connection.exec_driver_sql(value)
        found = dict(current[table]['triggers'])
        for name, value in required['triggers']:
            if name in found and found[name] != value:
                raise RuntimeError(table + '.' + name + ': 기존 트리거 정의가 달라 교체하지 않았습니다.')
            if name not in found:
                connection.exec_driver_sql(value)

    # 기존 사용자 지정 열·추가 테이블은 건드리지 않고 정식 객체의 소유권만 정리합니다.
    for schema, owner in OWNERS.items():
        connection.exec_driver_sql(f'ALTER SCHEMA {ident(schema)} OWNER TO {ident(owner)}')
    for table in target:
        if table != 'functions':
            connection.exec_driver_sql(f'ALTER TABLE {qualified(table)} OWNER TO {ident(OWNERS[table.split(".")[0]])}')
    for name, _ in target['functions']:
        connection.exec_driver_sql(f'ALTER FUNCTION {qualified(name[:-2])}() OWNER TO {ident(OWNERS[name.split(".")[0]])}')
    connection.exec_driver_sql('GRANT USAGE ON SCHEMA auth TO rag_user')
    connection.exec_driver_sql('GRANT SELECT ON auth.employee_info,auth.team_info TO rag_user')
    connection.exec_driver_sql('REVOKE ALL ON SCHEMA vault FROM PUBLIC,rag_user')
    connection.exec_driver_sql('REVOKE ALL ON SCHEMA rag FROM PUBLIC,vault_user')
    issues = differences(capture(connection), target)
    if issues:
        raise RuntimeError('자동 보충 후 구조 검사 실패: ' + '; '.join(issues))
