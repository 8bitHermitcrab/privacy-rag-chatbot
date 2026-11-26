"""데이터 버전(HMAC 키 교체) 관리 — 서버 관리자 전용.

- 키는 버전마다 한 번 정합니다(서버가 만들거나 관리자가 두 번 입력). 키 값은 화면·응답·작업 기록에 내보내지 않고,
  저장소 밖 비공개 폴더의 data_keys.json 에만 둡니다. 화면에는 키 대신 지문(16자리)만 보여 줍니다.
- 적용은 챗봇 서버별로 합니다. node_versions.json 에 서버별 데이터 버전을 적어 두고, 서버를 시작할 때
  그 버전의 키와 DATA_VERSION 을 그 프로세스 환경으로만 넘깁니다(.env 보다 우선). 지정이 없으면 .env 를 따릅니다.
- 전환은 기존 재시작 절차(트래픽 제외 → 요청 대기 → 재시작 → 정상 확인 → 복귀)를 그대로 쓰고,
  새 버전으로 기동하지 못하면 이전 버전으로 되돌려 다시 시작합니다.
- 두 서버가 모두 새 버전이 된 뒤에만 .env 기본값 갱신·예전 버전 종료를 허용하고, 삭제는 24시간 안의 백업과 버전 이름 입력을 요구합니다.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from .security import REPO, read_private, state_dir, write_private, audit

KEYS_FILE = "data_keys.json"
ASSIGN_FILE = "node_versions.json"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{32,200}$")   # .env 한 줄에 안전하게 쓸 수 있는 문자만
BUILD_KEY_ENV = "DV_BUILD_KEY"


class DataVersionUIError(RuntimeError):
    """화면에 그대로 보여 줄 수 있는 오류 (키 값은 담지 않음)."""


# ---------------------------------------------------------------- 비공개 저장
def _keys():
    return read_private(KEYS_FILE) if (state_dir() / KEYS_FILE).exists() else {}


def _save_key(version, key):
    keys = _keys()
    keys[version] = key
    write_private(KEYS_FILE, keys)


def assignments():
    return read_private(ASSIGN_FILE) if (state_dir() / ASSIGN_FILE).exists() else {}


def _assign(node, version):
    data = assignments()
    if version is None:
        data.pop(node, None)
    else:
        data[node] = version
    write_private(ASSIGN_FILE, data)


def _env_file():
    from dotenv import dotenv_values
    return dotenv_values(REPO / ".env")


def env_default_version():
    return (_env_file().get("DATA_VERSION") or "v1").strip() or "v1"


def node_env(node):
    """챗봇 프로세스에 넘길 환경 (지정 버전이 있고 키를 보관 중일 때만). 없으면 빈 dict → .env 를 따름."""
    version = assignments().get(node)
    if not version:
        return {}
    key = _keys().get(version)
    if not key:
        raise DataVersionUIError(f"데이터 버전 {version} 의 키를 보관하고 있지 않아 서버를 시작하지 않았습니다.")
    return {"DATA_VERSION": version, "HMAC_KEY": key}


def effective_version(node):
    return assignments().get(node) or env_default_version()


# ---------------------------------------------------------------- 등록부·키 확인
def _dv():
    from starter import config, data_version
    # 기본값 갱신(.env) 뒤에도 이 관리 프로세스가 최신 DATA_VERSION 으로 판단하도록 매번 맞춥니다.
    config.use_data_version(env_default_version())
    return data_version


def _engine():
    from starter.migrate import engine_from_env
    return engine_from_env()


def import_env_keys(rows):
    """.env 의 HMAC_KEY / NEW_HMAC_KEY 중 등록부 지문과 맞는 키를 비공개 저장소로 옮겨 둡니다(값은 그대로 .env 에도 남음)."""
    dv = _dv()
    keys = _keys()
    env = _env_file()
    candidates = [env.get(name) for name in ("HMAC_KEY", "NEW_HMAC_KEY") if env.get(name)]
    changed = False
    for row in rows:
        if row["version"] in keys:
            continue
        for value in candidates:
            if dv.fingerprint(value) == row["key_fingerprint"]:
                keys[row["version"]] = value
                changed = True
                break
    if changed:
        write_private(KEYS_FILE, keys)
    return keys


def _registry(c):
    return [dict(r) for r in c.exec_driver_sql(
        "SELECT version,rag_schema,vault_schema,key_fingerprint,status,note,created_at,updated_at FROM auth.data_version ORDER BY version").mappings()]


def next_version(rows):
    numbers = [int(r["version"][1:]) for r in rows]
    return "v" + str(max(numbers or [1]) + 1)


def last_backup():
    try:
        from starter.db_backup import backup_dir, backups, settings as backup_settings
        files = backups(backup_dir(backup_settings()))
    except Exception:
        return None
    if not files:
        return None
    stamp = datetime.strptime(files[-1].name[8:23], "%Y%m%d_%H%M%S")
    return {"name": files[-1].name, "time": stamp.isoformat(), "age_hours": round((datetime.now() - stamp).total_seconds() / 3600, 1)}


def running_version(node):
    from .operations import NODES, local_json
    info = local_json(NODES[node], "/api/info")
    return (info or {}).get("data_version") if info else None


def overview():
    from .operations import NODES
    dv = _dv()
    engine = _engine()
    try:
        with engine.connect() as c:
            rows = _registry(c)
            keys = import_env_keys(rows)
            for r in rows:
                r["key_stored"] = r["version"] in keys
                r["created_at"], r["updated_at"] = str(r["created_at"]), str(r["updated_at"])
                exists = dv._tables_exist(c, r["version"])
                r["tables_exist"] = exists
                r["counts"] = dv.counts(c, r["version"]) if exists else None
    finally:
        engine.dispose()
    env = _env_file()
    default = env_default_version()
    default_row = next((r for r in rows if r["version"] == default), None)
    nodes = []
    for node in NODES:
        nodes.append({"id": node, "name": "챗봇 서버 " + node[-1], "assigned": assignments().get(node),
                      "effective": effective_version(node), "running": running_version(node)})
    return {
        "versions": rows,
        "nodes": nodes,
        "env": {"data_version": default,
                "key_matches": bool(default_row and env.get("HMAC_KEY") and dv.fingerprint(env["HMAC_KEY"]) == default_row["key_fingerprint"]),
                "new_key_present": bool(env.get("NEW_HMAC_KEY"))},
        "next_version": next_version(rows),
        "backup": last_backup(),
        "build": build.snapshot(),
    }


# ---------------------------------------------------------------- 새 버전 만들기 (백그라운드)
class Build:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"busy": False}

    def snapshot(self):
        with self.lock:
            out = dict(self.state)
        if "started" in out:
            out["elapsed_seconds"] = round((out.get("finished") or time.time()) - out.pop("started"), 1)
            out.pop("finished", None)
        return out

    def set(self, **fields):
        with self.lock:
            self.state.update(fields)


build = Build()
STEPS = ["스키마 생성", "새 키로 문서 적재 (임베딩 포함)", "인물사전 미리보기", "인물사전 반영", "적재 결과 확인"]


def check_key(key, confirm):
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise DataVersionUIError("키는 공백 없이 32~200자의 영문·숫자·기호(. _ ~ + / = -)로 입력하세요.")
    if key != confirm:
        raise DataVersionUIError("두 번 입력한 키가 서로 다릅니다.")
    return key


def start_build(source, key=None, confirm=None):
    if source == "generate":
        key = secrets.token_urlsafe(48)
    elif source == "input":
        key = check_key(key, confirm)
    else:
        raise DataVersionUIError("키를 만드는 방법을 선택하세요.")
    with build.lock:
        if build.state.get("busy"):
            raise DataVersionUIError("새 버전을 만드는 중입니다. 끝난 뒤 다시 시도하세요.")
        build.state = {"busy": True, "step": 0, "steps": STEPS, "message": "준비 중", "started": time.time()}
    engine = _engine()
    try:
        with engine.connect() as c:
            version = next_version(_registry(c))
    finally:
        engine.dispose()
    build.set(version=version)
    threading.Thread(target=_run_build, args=(version, key), daemon=True).start()
    return {"version": version}


def _run(args, key, label, timeout=1800):
    env = {**os.environ, BUILD_KEY_ENV: key, "PYTHONIOENCODING": "utf-8"}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    done = subprocess.run([sys.executable, "-B", "-m", *args], cwd=REPO, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **flags)
    log = state_dir() / "data_version_build.log"
    with log.open("a", encoding="utf-8") as handle:     # 키는 환경변수로만 넘기므로 출력에 들어가지 않습니다
        handle.write(f"=== {datetime.now():%F %T} {label} (종료코드 {done.returncode})\n{done.stdout[-4000:]}\n{done.stderr[-4000:]}\n")
    if done.returncode:
        lines = [l for l in (done.stderr or done.stdout).splitlines() if l.strip()]
        raise DataVersionUIError(f"{label} 실패: " + (lines[-1][:200] if lines else "기록을 확인하세요."))
    return done.stdout


def _run_build(version, key):
    dv = _dv()
    try:
        build.set(step=1, message=f"{version} 스키마를 만들고 있습니다.")
        dv.create(version, key_env="서버 관리자 화면", key=key)
        _save_key(version, key)                      # 스키마를 만든 뒤에만 키를 보관 (만들기 실패 시 남기지 않음)
        audit("dv_build_create")
        build.set(step=2, message="새 키로 문서를 다시 적재하고 있습니다. 임베딩 때문에 몇 분 걸립니다.")
        _run(["starter.ingest", "--data-version", version, "--key-env", BUILD_KEY_ENV, "--reset"], key, "적재")
        build.set(step=3, message="새 토큰 기준으로 인물사전을 미리 계산하고 있습니다.")
        out = _run(["starter.person_dictionary", "--data-version", version, "--key-env", BUILD_KEY_ENV], key, "인물사전 미리보기")
        top = re.search(r"(?m)^\{\s*$", out)          # 진행 줄 다음, 줄 맨 앞의 '{' 부터가 최종 결과 JSON
        plan = json.loads(out[top.start():])["plan"]
        build.set(step=4, message="인물사전을 반영하고 있습니다.")
        _run(["starter.person_dictionary", "--data-version", version, "--key-env", BUILD_KEY_ENV, "--apply", "--plan", plan],
             key, "인물사전 반영")
        build.set(step=5, message="적재 건수와 키 짝을 확인하고 있습니다.")
        result = dv.ready(version, key_env="서버 관리자 화면", key=key)
        build.set(busy=False, step=6, ok=True, finished=time.time(),
                  message=f"{version} 준비 완료. 문서 {result['counts']['chunks']}건, 토큰 {result['counts']['token_map']}종.")
        audit("dv_build")
    except Exception as error:
        message = str(error) if isinstance(error, (DataVersionUIError, dv.DataVersionError)) else "새 버전을 만들지 못했습니다. 작업 기록 파일을 확인하세요."
        build.set(busy=False, ok=False, finished=time.time(), message=message + " (만들던 버전은 적재 중 상태로 남습니다)")
        audit("dv_build", "failure")


# ---------------------------------------------------------------- 서버별 전환
def switch(node, version):
    from .operations import NODES, control, OperationError, managed_process
    if node not in NODES:
        raise DataVersionUIError("등록되지 않은 서버입니다.")
    engine = _engine()
    try:
        with engine.connect() as c:
            row = next((r for r in _registry(c) if r["version"] == version), None)
    finally:
        engine.dispose()
    if row is None or row["status"] not in ("READY", "ACTIVE"):
        raise DataVersionUIError(f"{version} 은 준비 완료 또는 사용 중인 버전이 아닙니다.")
    if version not in _keys():
        raise DataVersionUIError(f"{version} 의 키를 보관하고 있지 않습니다.")
    if not managed_process(node):
        raise DataVersionUIError("관리 프로그램이 시작한 서버만 전환할 수 있습니다. 먼저 서버를 시작하세요.")
    previous = assignments().get(node)
    if effective_version(node) == version:
        raise DataVersionUIError(f"이미 {version} 으로 지정된 서버입니다.")
    _assign(node, version)
    try:
        control(node, "restart", node + ":restart")
    except OperationError as error:
        # 새 버전으로 기동하지 못했으면 이전 지정으로 되돌리고 다시 시작합니다.
        _assign(node, previous)
        try:
            if not managed_process(node):
                control(node, "start", node + ":start")
        except OperationError:
            pass
        audit("dv_switch", "failure")
        raise DataVersionUIError(f"{version} 으로 전환하지 못해 이전 설정으로 되돌렸습니다: {error}") from None
    audit("dv_switch")
    return {"node": node, "version": version, "running": running_version(node)}


# ---------------------------------------------------------------- 기본값(.env)·종료·삭제
def _all_nodes_on(version):
    from .operations import NODES
    return all(effective_version(n) == version and running_version(n) == version for n in NODES)


def promote(version):
    """두 서버가 모두 이 버전일 때 .env 의 HMAC_KEY·DATA_VERSION 을 이 버전으로 바꿉니다(이전 .env 는 비공개 폴더에 보관)."""
    if not _all_nodes_on(version):
        raise DataVersionUIError("두 서버가 모두 이 버전으로 실행 중일 때만 기본값으로 저장할 수 있습니다.")
    key = _keys().get(version)
    if not key:
        raise DataVersionUIError(f"{version} 의 키를 보관하고 있지 않습니다.")
    path = REPO / ".env"
    backup = state_dir() / "env_backups"
    backup.mkdir(exist_ok=True)
    shutil.copy2(path, backup / f".env.{datetime.now():%Y%m%d_%H%M%S}")
    rewrite_env(path, {"HMAC_KEY": key, "DATA_VERSION": version}, remove={"NEW_HMAC_KEY"} if _env_file().get("NEW_HMAC_KEY") == key else set())
    write_private(ASSIGN_FILE, {})               # 이제 .env 가 같은 값을 주므로 서버별 지정은 비웁니다
    audit("dv_promote")
    return {"data_version": version}


def rewrite_env(path, updates, remove=frozenset()):
    """KEY=값 줄만 바꾸거나 추가/삭제하고 나머지 줄(주석·순서)은 그대로 둡니다."""
    lines = path.read_text(encoding="utf-8").splitlines()
    seen, out = set(), []
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if name in remove:
            continue
        if name in updates:
            if name in seen:
                continue
            out.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def retire(version):
    from .operations import NODES
    if any(effective_version(n) == version or running_version(n) == version for n in NODES) or env_default_version() == version:
        raise DataVersionUIError("이 버전을 쓰는 서버나 .env 기본값이 남아 있어 종료할 수 없습니다.")
    engine = _engine()
    try:
        result = _dv().retire(version, engine=engine)
    finally:
        engine.dispose()
    audit("dv_retire")
    return result


def drop(version, typed):
    if typed != version:
        raise DataVersionUIError("삭제할 버전 이름을 그대로 입력하세요.")
    backup = last_backup()
    if not backup or backup["age_hours"] > 24:
        raise DataVersionUIError("24시간 안에 만든 DB 백업이 없습니다. 먼저 DB_백업을 실행하세요.")
    engine = _engine()
    try:
        result = _dv().drop(version, engine=engine)
    finally:
        engine.dispose()
    keys = _keys()
    keys.pop(version, None)
    write_private(KEYS_FILE, keys)
    audit("dv_drop")
    return result
