"""데이터 버전 관리 — HMAC 키 무중단 교체.

키가 바뀌면 토큰·가명 본문·임베딩·매핑이 모두 달라집니다. 그래서 새 키 데이터는 별도 스키마에 따로 만들고,
다 만들어지면 .env 의 HMAC_KEY 와 DATA_VERSION 을 함께 바꾼 뒤 서버를 한 대씩 재시작해 넘어갑니다.
(절차: docs/키_교체_절차.md)

    python -m starter.data_version status
    python -m starter.data_version create v2 --key-env NEW_HMAC_KEY     # 빈 스키마 rag_v2·vault_v2 생성, 적재 중(BUILDING)
    python -m starter.ingest --data-version v2 --key-env NEW_HMAC_KEY --reset
    python -m starter.person_dictionary --data-version v2 --key-env NEW_HMAC_KEY [--apply --plan …]
    python -m starter.data_version ready v2 --key-env NEW_HMAC_KEY      # 적재 결과 확인 후 준비 완료(READY)
    (.env: HMAC_KEY=새 키, DATA_VERSION=v2 → 서버 관리자에서 챗봇 1, 2 차례로 재시작 → 서버가 ACTIVE 로 표시)
    python -m starter.data_version retire v1                            # 예전 버전 사용 종료
    python -m starter.data_version drop v1 --yes                        # 예전 데이터 삭제 (되돌릴 수 없음)

- 문서 권한(rag.document_info)과 계정·정책·복원 기록(auth.*)은 버전과 관계없이 하나만 두고 함께 씁니다.
- 키 지문은 키 자체가 아니라 HMAC(키, 고정 문구) 앞 16자리입니다. 지문으로 키를 알아낼 수 없습니다.
- 서버는 시작할 때 verify_runtime() 으로 '키와 데이터 버전의 짝'을 확인하고, 맞지 않으면 시작하지 않습니다.
"""
import argparse
import hashlib
import hmac
import os
import sys

from . import config

KINDS = (("rag", "chunks"), ("vault", "person"), ("vault", "token_map"), ("vault", "person_mention"))
SAMPLE = 20


class DataVersionError(RuntimeError):
    """사용자에게 그대로 보여 줄 수 있는 데이터 버전 오류 (키 값은 담지 않음)."""


def fingerprint(key):
    if not key:
        raise DataVersionError("HMAC 키가 비어 있습니다. .env 를 확인하세요.")
    return hmac.new(key.encode(), b"pii-rag/data-version/key-fingerprint", hashlib.sha256).hexdigest()[:16]


def key_from(name):
    """키 이름(예: HMAC_KEY, NEW_HMAC_KEY)의 값을 환경변수 → .env 순서로 찾습니다."""
    if not name or not name.replace("_", "").isalnum():
        raise DataVersionError("키 이름은 HMAC_KEY, NEW_HMAC_KEY 처럼 적어 주세요.")
    value = os.getenv(name)
    if not value:
        from dotenv import dotenv_values
        value = dotenv_values(config.ROOT / ".env").get(name)
    if not value:
        raise DataVersionError(f".env 에 {name} 값이 없습니다.")
    return value


def _engine(engine=None):
    if engine is not None:
        return engine, False
    from .migrate import engine_from_env
    return engine_from_env(), True


def _schemas(version):
    try:
        return config.data_schemas(version)
    except ValueError as error:
        raise DataVersionError(str(error)) from None


def _row(c, version, lock=False):
    return c.exec_driver_sql("SELECT version,rag_schema,vault_schema,key_fingerprint,status FROM auth.data_version WHERE version=%s"
                             + (" FOR UPDATE" if lock else ""), (version,)).mappings().fetchone()


def _tables_exist(c, version):
    rag, vault = _schemas(version)
    names = {"rag": rag, "vault": vault}
    return all(c.exec_driver_sql("SELECT to_regclass(%s)", (f"{names[s]}.{t}",)).scalar() for s, t in KINDS)


def key_matches_data(c, version, key):
    """매핑 표의 토큰 일부를 이 키로 다시 계산해 데이터와 키가 짝인지 확인. 표가 비어 있으면 None."""
    from . import pii_pipeline
    _, vault = _schemas(version)
    rows = c.exec_driver_sql(f"SELECT token,label,normalized FROM {vault}.token_map ORDER BY token LIMIT {SAMPLE}").fetchall()
    if not rows:
        return None
    saved = config.HMAC_KEY
    config.HMAC_KEY = key
    try:
        hits = sum(1 for token, label, normalized in rows if pii_pipeline._token(label, normalized) == token)
    finally:
        config.HMAC_KEY = saved
    # 이름만 쓰인 언급을 같은 문서의 전체 이름 토큰에 묶는 처리 등으로 일부는 다를 수 있어 절반 이상이면 일치로 봅니다.
    return hits * 2 >= len(rows)


def verify_runtime(engine=None):
    """서버 시작 시 확인: .env 의 DATA_VERSION 과 HMAC_KEY 가 짝인지. 아니면 DataVersionError 로 시작을 막습니다."""
    version, key = config.DATA_VERSION, config.HMAC_KEY
    fp = fingerprint(key)
    engine, own = _engine(engine)
    try:
        with engine.begin() as c:
            c.exec_driver_sql("SELECT pg_advisory_xact_lock(738219461083)")
            row = _row(c, version, lock=True)
            if row is None:
                if version != "v1":
                    raise DataVersionError(f"데이터 버전 {version} 이 등록되어 있지 않습니다. data_version create 로 먼저 만드세요.")
                if key_matches_data(c, "v1", key) is False:
                    raise DataVersionError("HMAC_KEY 로 기존 데이터(v1)의 토큰을 다시 만들 수 없습니다. 적재할 때 쓴 키와 다릅니다.")
                c.exec_driver_sql("""INSERT INTO auth.data_version(version,rag_schema,vault_schema,key_fingerprint,status,note)
                    VALUES ('v1','rag','vault',%s,'ACTIVE','서버 첫 시작 때 현재 키로 등록')""", (fp,))
                return "v1"
            if not hmac.compare_digest(row["key_fingerprint"], fp):
                raise DataVersionError(f"HMAC_KEY 가 데이터 버전 {version} 을 만든 키와 다릅니다. .env 의 HMAC_KEY 와 DATA_VERSION 을 함께 바꿨는지 확인하세요.")
            if row["status"] == "BUILDING":
                raise DataVersionError(f"데이터 버전 {version} 은 아직 적재 중입니다. 적재 확인 후 data_version ready {version} 을 먼저 실행하세요.")
            if row["status"] == "RETIRED":
                raise DataVersionError(f"데이터 버전 {version} 은 사용 종료된 버전입니다. .env 의 DATA_VERSION 을 확인하세요.")
            if not _tables_exist(c, version):
                raise DataVersionError(f"데이터 버전 {version} 의 표가 없습니다.")
            if row["status"] == "READY":
                c.exec_driver_sql("UPDATE auth.data_version SET status='ACTIVE' WHERE version=%s", (version,))
            return version
    finally:
        if own:
            engine.dispose()


def prepare_target(version, key_env="HMAC_KEY", engine=None):
    """적재 도구용: 이 프로세스가 쓸 데이터 버전과 키를 정하고, 등록부의 키 지문과 맞는지 확인합니다."""
    key = key_from(key_env)
    _schemas(version)
    engine, own = _engine(engine)
    try:
        with engine.connect() as c:
            row = _row(c, version)
            if row is None and version != "v1":
                raise DataVersionError(f"데이터 버전 {version} 이 없습니다. data_version create {version} 으로 먼저 만드세요.")
            if row is not None:
                if not hmac.compare_digest(row["key_fingerprint"], fingerprint(key)):
                    raise DataVersionError(f"{key_env} 가 데이터 버전 {version} 을 만들 때 쓴 키와 다릅니다.")
                if row["status"] == "RETIRED":
                    raise DataVersionError(f"데이터 버전 {version} 은 사용 종료된 버전이라 적재할 수 없습니다.")
    finally:
        if own:
            engine.dispose()
    config.use_data_version(version)
    config.HMAC_KEY = key
    return version


def create(version, key_env, engine=None):
    """빈 버전 스키마를 기본 표와 같은 구조로 만듭니다. 외래키는 같은 버전 표(또는 공용 rag.document_info)를 가리킵니다."""
    if version == "v1":
        raise DataVersionError("v1 은 기본 스키마라 새로 만들 수 없습니다.")
    rag, vault = _schemas(version)
    fp = fingerprint(key_from(key_env))
    from .db import data_sql
    engine, own = _engine(engine)
    try:
        with engine.begin() as c:
            c.exec_driver_sql("SELECT set_config('search_path','pg_catalog',true)")
            c.exec_driver_sql("SELECT pg_advisory_xact_lock(738219461083)")
            if _row(c, version) is not None or c.exec_driver_sql(
                    "SELECT count(*) FROM pg_namespace WHERE nspname IN (%s,%s)", (rag, vault)).scalar():
                raise DataVersionError(f"데이터 버전 {version} 이 이미 있습니다.")
            used = c.exec_driver_sql("SELECT version FROM auth.data_version WHERE key_fingerprint=%s", (fp,)).scalar()
            if used:
                raise DataVersionError(f"{key_env} 는 이미 {used} 에서 쓰는 키입니다. 새 키를 넣으세요.")
            c.exec_driver_sql(f'CREATE SCHEMA "{rag}" AUTHORIZATION rag_user')
            c.exec_driver_sql(f'CREATE SCHEMA "{vault}" AUTHORIZATION vault_user')
            owners = {"rag": "rag_user", "vault": "vault_user"}
            for schema, table in KINDS:
                base, target = f"{schema}.{table}", data_sql(f"{schema}.{table}", version)
                c.exec_driver_sql(f"CREATE TABLE {target} (LIKE {base} INCLUDING ALL)")
                c.exec_driver_sql(f"ALTER TABLE {target} OWNER TO {owners[schema]}")
                for name, definition in c.exec_driver_sql(
                        "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='f' ORDER BY conname",
                        (base,)).fetchall():
                    c.exec_driver_sql(f'ALTER TABLE {target} ADD CONSTRAINT "{name}" {data_sql(definition, version)}')
                for (definition,) in c.exec_driver_sql(
                        "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal ORDER BY tgname",
                        (base,)).fetchall():
                    c.exec_driver_sql(data_sql(definition, version))
                # 소유자 외 계정에 준 권한도 기본 표와 같게 맞춥니다.
                for grantee, privilege in c.exec_driver_sql(
                        """SELECT pg_get_userbyid(a.grantee), a.privilege_type FROM pg_class t, aclexplode(t.relacl) a
                           WHERE t.oid=%s::regclass AND a.grantee <> t.relowner AND a.grantee <> 0""", (base,)).fetchall():
                    c.exec_driver_sql(f'GRANT {privilege} ON {target} TO "{grantee}"')
            c.exec_driver_sql("""INSERT INTO auth.data_version(version,rag_schema,vault_schema,key_fingerprint,status,note)
                VALUES (%s,%s,%s,%s,'BUILDING',%s)""", (version, rag, vault, fp, f"{key_env} 로 생성"))
        return {"version": version, "rag_schema": rag, "vault_schema": vault, "status": "BUILDING"}
    finally:
        if own:
            engine.dispose()


def counts(c, version):
    rag, vault = _schemas(version)
    names = {"rag": rag, "vault": vault}
    out = {}
    for schema, table in KINDS:
        name = f"{names[schema]}.{table}"
        out[table] = c.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar() if c.exec_driver_sql(
            "SELECT to_regclass(%s)", (name,)).scalar() else None
    return out


def ready(version, key_env, engine=None):
    """적재가 끝난 버전을 확인하고 준비 완료(READY)로 바꿉니다."""
    key = key_from(key_env)
    engine, own = _engine(engine)
    try:
        with engine.begin() as c:
            row = _row(c, version, lock=True)
            if row is None or row["status"] != "BUILDING":
                raise DataVersionError(f"데이터 버전 {version} 은 적재 중(BUILDING) 상태가 아닙니다.")
            if not hmac.compare_digest(row["key_fingerprint"], fingerprint(key)):
                raise DataVersionError(f"{key_env} 가 데이터 버전 {version} 을 만들 때 쓴 키와 다릅니다.")
            n = counts(c, version)
            if not n["chunks"] or not n["token_map"]:
                raise DataVersionError(f"데이터 버전 {version} 에 적재된 내용이 없습니다: {n}")
            rag, _ = _schemas(version)
            orphan = c.exec_driver_sql(f"""SELECT count(DISTINCT doc_id) FROM {rag}.chunks k
                WHERE NOT EXISTS (SELECT 1 FROM rag.document_info d WHERE d.document_id=k.doc_id)""").scalar()
            if orphan:
                raise DataVersionError(f"문서 권한 표에 없는 문서 {orphan}건이 있습니다.")
            if key_matches_data(c, version, key) is not True:
                raise DataVersionError(f"{key_env} 로 {version} 의 토큰을 다시 만들 수 없습니다. 다른 키로 적재된 것 같습니다.")
            c.exec_driver_sql("UPDATE auth.data_version SET status='READY' WHERE version=%s", (version,))
            return {"version": version, "status": "READY", "counts": n}
    finally:
        if own:
            engine.dispose()


def retire(version, engine=None):
    if version == config.DATA_VERSION:
        raise DataVersionError(f".env 의 DATA_VERSION 이 아직 {version} 입니다. 새 버전으로 바꾸고 서버를 재시작한 뒤 실행하세요.")
    engine, own = _engine(engine)
    try:
        with engine.begin() as c:
            row = _row(c, version, lock=True)
            if row is None:
                raise DataVersionError(f"데이터 버전 {version} 이 없습니다.")
            if not c.exec_driver_sql("SELECT 1 FROM auth.data_version WHERE status='ACTIVE' AND version<>%s", (version,)).fetchone():
                raise DataVersionError("다른 사용 중(ACTIVE) 버전이 없어 종료할 수 없습니다.")
            c.exec_driver_sql("UPDATE auth.data_version SET status='RETIRED' WHERE version=%s", (version,))
            return {"version": version, "status": "RETIRED"}
    finally:
        if own:
            engine.dispose()


def drop(version, engine=None):
    """사용 종료(RETIRED)된 버전의 데이터를 지웁니다. v1 은 공용 문서 권한 표가 같은 스키마에 있어 표를 비우기만 합니다."""
    if version == config.DATA_VERSION:
        raise DataVersionError(f".env 의 DATA_VERSION 이 {version} 입니다.")
    rag, vault = _schemas(version)
    engine, own = _engine(engine)
    try:
        with engine.begin() as c:
            row = _row(c, version, lock=True)
            if row is None or row["status"] != "RETIRED":
                raise DataVersionError(f"데이터 버전 {version} 은 사용 종료(RETIRED) 상태가 아닙니다. retire 를 먼저 실행하세요.")
            if version == "v1":
                c.exec_driver_sql("TRUNCATE rag.chunks, vault.person_mention, vault.person, vault.token_map")
            else:
                c.exec_driver_sql(f'DROP SCHEMA "{rag}" CASCADE')
                c.exec_driver_sql(f'DROP SCHEMA "{vault}" CASCADE')
            c.exec_driver_sql("UPDATE auth.data_version SET note=coalesce(note||' / ','')||'데이터 삭제 '||now()::date WHERE version=%s", (version,))
            return {"version": version, "dropped": True}
    finally:
        if own:
            engine.dispose()


def status(engine=None):
    engine, own = _engine(engine)
    try:
        with engine.connect() as c:
            rows = c.exec_driver_sql("SELECT version,rag_schema,vault_schema,status,note,created_at,updated_at FROM auth.data_version ORDER BY version").mappings().fetchall()
            out = []
            for r in rows:
                item = {k: (str(v) if k.endswith("_at") else v) for k, v in r.items()}
                item["counts"] = counts(c, r["version"]) if not (r["status"] == "RETIRED" and r["version"] != "v1"
                                                                 and not _tables_exist(c, r["version"])) else None
                out.append(item)
            current = {"DATA_VERSION": config.DATA_VERSION}
            row = _row(c, config.DATA_VERSION)
            current["HMAC_KEY_matches"] = bool(row) and config.HMAC_KEY and hmac.compare_digest(row["key_fingerprint"], fingerprint(config.HMAC_KEY))
            return {"current_env": current, "versions": out}
    finally:
        if own:
            engine.dispose()


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(description="데이터 버전 관리 (HMAC 키 무중단 교체)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="버전 목록과 표별 행 수")
    for name, text in (("create", "빈 버전 스키마 생성"), ("ready", "적재 확인 후 준비 완료")):
        p = sub.add_parser(name, help=text)
        p.add_argument("version")
        p.add_argument("--key-env", required=True, help="새 키가 들어 있는 .env 항목 이름 (예: NEW_HMAC_KEY)")
    sub.add_parser("retire", help="사용 종료").add_argument("version")
    p = sub.add_parser("drop", help="사용 종료된 버전의 데이터 삭제")
    p.add_argument("version")
    p.add_argument("--yes", action="store_true", help="되돌릴 수 없음을 확인")
    a = ap.parse_args(argv)
    try:
        if a.command == "status":
            result = status()
        elif a.command == "create":
            result = create(a.version, a.key_env)
        elif a.command == "ready":
            result = ready(a.version, a.key_env)
        elif a.command == "retire":
            result = retire(a.version)
        else:
            if not a.yes:
                raise DataVersionError("데이터를 지우는 작업입니다. 확인했다면 --yes 를 붙여 다시 실행하세요.")
            result = drop(a.version)
    except DataVersionError as error:
        print("중단: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
