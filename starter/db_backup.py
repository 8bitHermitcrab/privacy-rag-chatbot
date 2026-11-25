"""DB 백업·복원 확인 (Docker 의 pii_rag_db 컨테이너 안 pg_dump/pg_restore 사용).

    python -m starter.db_backup              # 백업 1개 만들기 (+ 오래된 백업 정리)
    python -m starter.db_backup list         # 백업 목록
    python -m starter.db_backup verify       # 가장 최근 백업을 임시 DB 에 복원해 표별 행 수가 같은지 확인 후 임시 DB 삭제
    python -m starter.db_backup verify 파일명

- 백업 파일에는 원래 개인정보(vault)와 계정 정보가 들어 있습니다. 그래서 저장소(Git) 폴더가 아니라
  서버 관리자와 같은 비공개 폴더(%LOCALAPPDATA%\\RagServerAdmin\\db_backups, 본인·SYSTEM 만 접근)에 둡니다.
  다른 곳에 두려면 .env 의 DB_BACKUP_DIR 에 절대 경로를 적습니다. 그 폴더의 접근 권한은 직접 관리해야 합니다.
- 형식은 pg_dump custom(-Fc, 압축). 스키마·데이터·권한(GRANT)·데이터 버전 스키마(rag_v2 …)까지 모두 포함합니다.
  DB 계정(rag_user 등)의 비밀번호는 포함하지 않습니다. 새 DB 에 복원할 때는 시스템 시작으로 계정을 먼저 만든 뒤 복원합니다.
- 운영 DB 를 덮어쓰는 복원 명령은 일부러 만들지 않았습니다. 실제 복원은 docs/DB_백업_복원.md 절차를 따릅니다.
- 클라우드(서버 3)에서는 cron 으로 tools/db_backup.sh 를 실행하고, .env 의 DB_BACKUP_COPY_TO(예: backup@10.0.0.12:/var/backups/pii_rag)
  를 적으면 백업 폴더를 서버 2 로 rsync(SSH) 복사합니다. 서버 3 이 망가져도 서버 2 에 백업이 남게 하기 위함입니다.
  복사본도 같은 보관 개수를 따르며(오래된 파일은 복사 대상에서도 삭제), 파일 권한은 본인만 읽기(600)입니다.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = os.getenv("DB_CONTAINER", "pii_rag_db")
KEEP = int(os.getenv("DB_BACKUP_KEEP", "14"))
NAME = re.compile(r"^pii_rag_\d{8}_\d{6}\.dump$")
COPY_TARGET = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:/[A-Za-z0-9._/-]+$")
CHECK_TABLES = ("auth.user_info", "auth.employee_info", "auth.team_position_policy", "auth.restore_log", "auth.data_version",
                "rag.document_info", "rag.chunks", "vault.token_map", "vault.person", "vault.person_mention")


class BackupError(RuntimeError):
    pass


def settings():
    env = {**dotenv_values(ROOT / ".env"), **{k: v for k, v in os.environ.items() if k.startswith(("POSTGRES_", "DB_BACKUP"))}}
    if not env.get("POSTGRES_PASSWORD"):
        raise BackupError(".env 의 POSTGRES_PASSWORD 가 필요합니다.")
    return env


def backup_dir(env):
    custom = env.get("DB_BACKUP_DIR")
    if custom:
        path = Path(custom).expanduser()
        if not path.is_absolute():
            raise BackupError("DB_BACKUP_DIR 은 절대 경로여야 합니다.")
        path.mkdir(parents=True, exist_ok=True)
        return path
    from server_admin.security import prepare_private_dir, state_dir
    prepare_private_dir()                      # 본인·SYSTEM 만 접근하도록 권한 설정된 폴더
    path = state_dir() / "db_backups"
    path.mkdir(exist_ok=True)
    return path


def _docker():
    from server_admin.__main__ import docker_executable
    try:
        return docker_executable()
    except Exception:
        raise BackupError("docker 명령을 찾지 못했습니다. Docker Desktop 실행 상태를 확인하세요.") from None


def _exec(env, args, **kwargs):
    """컨테이너 안에서 실행. 비밀번호는 명령줄이 아니라 환경변수(PGPASSWORD)로만 넘깁니다."""
    child = {**os.environ, "PGPASSWORD": env["POSTGRES_PASSWORD"]}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run([_docker(), "exec", "-i", "-e", "PGPASSWORD", CONTAINER, *args], env=child, **flags, **kwargs)


def _user_db(env):
    return env.get("POSTGRES_USER", "admin"), env.get("POSTGRES_DB", "pii_rag")


def create(env=None):
    env = env or settings()
    user, db = _user_db(env)
    folder = backup_dir(env)
    final = folder / f"pii_rag_{datetime.now():%Y%m%d_%H%M%S}.dump"
    partial = final.with_suffix(".partial")
    started = time.time()
    with open(partial, "wb") as handle:
        result = _exec(env, ["pg_dump", "-U", user, "-d", db, "-Fc", "--no-password"], stdout=handle,
                       stderr=subprocess.PIPE, timeout=1800)
    if result.returncode or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise BackupError("pg_dump 실패: " + result.stderr.decode("utf-8", "replace")[-500:])
    partial.replace(final)                     # 다 쓴 뒤에만 정식 이름으로 (중간에 끊긴 파일을 백업으로 착각하지 않게)
    removed = prune(folder)
    result = {"file": str(final), "bytes": final.stat().st_size, "seconds": round(time.time() - started, 1), "removed_old": removed}
    if env.get("DB_BACKUP_COPY_TO"):
        result["copied_to"] = copy_offsite(folder, env["DB_BACKUP_COPY_TO"])
    return result


def copy_command(folder, target):
    """백업 폴더를 다른 서버로 맞춰 복사하는 rsync 명령. 백업 파일만 대상으로 하고, 여기서 정리된 옛 백업은 저쪽에서도 지웁니다."""
    if not COPY_TARGET.fullmatch(target or ""):
        raise BackupError("DB_BACKUP_COPY_TO 는 사용자@서버:/절대경로 형식이어야 합니다. 예: backup@10.0.0.12:/var/backups/pii_rag")
    return ["rsync", "-a", "--chmod=D700,F600", "--delete", "--include=pii_rag_*.dump", "--exclude=*",
            "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(folder) + "/", target.rstrip("/") + "/"]


def copy_offsite(folder, target):
    if os.name == "nt":
        raise BackupError("다른 서버 복사(DB_BACKUP_COPY_TO)는 우분투 서버에서만 사용합니다.")
    done = subprocess.run(copy_command(folder, target), capture_output=True, text=True, timeout=1800)
    if done.returncode:
        # 백업 파일 자체는 이미 만들어졌으므로 남겨 두고, 복사 실패만 알립니다 (cron 기록·종료코드로 확인)
        raise BackupError(f"백업은 만들었지만 {target} 복사에 실패했습니다: " + done.stderr[-300:])
    return target


def backups(folder):
    return sorted((p for p in folder.iterdir() if NAME.fullmatch(p.name)), key=lambda p: p.name)


def prune(folder, keep=None):
    keep = KEEP if keep is None else keep
    old = backups(folder)[:-keep] if keep > 0 else []
    for path in old:
        path.unlink()
    return [p.name for p in old]


def _counts(env, db):
    user, _ = _user_db(env)
    sql = "SELECT " + ",".join(f"(SELECT count(*) FROM {t})" for t in CHECK_TABLES) + ";"
    extra = ("SELECT string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname ~ '^(rag|vault)_v[0-9]+$';")
    out = _exec(env, ["psql", "-U", user, "-d", db, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql, "-c", extra],
                capture_output=True, text=True, encoding="utf-8", timeout=120)
    if out.returncode:
        raise BackupError(f"{db} 행 수 확인 실패: " + out.stderr[-300:])
    lines = out.stdout.strip().splitlines()
    return dict(zip(CHECK_TABLES, map(int, lines[0].split("|")))), lines[1] if len(lines) > 1 else ""


def verify(name=None, env=None):
    """백업을 임시 DB 에 복원해 운영 DB 와 표별 행 수를 비교하고 임시 DB 는 지웁니다. 운영 DB 는 읽기만 합니다."""
    env = env or settings()
    user, live = _user_db(env)
    folder = backup_dir(env)
    files = backups(folder)
    if not files:
        raise BackupError("백업 파일이 없습니다.")
    path = folder / name if name else files[-1]
    if not NAME.fullmatch(path.name) or not path.is_file():
        raise BackupError("백업 파일 이름을 확인하세요: " + path.name)
    temp = "pii_rag_restore_check_" + datetime.now().strftime("%H%M%S")
    run = lambda args, **kw: _exec(env, args, capture_output=True, text=True, encoding="utf-8", timeout=600, **kw)
    created = run(["createdb", "-U", user, temp])
    if created.returncode:
        raise BackupError("임시 DB 생성 실패: " + created.stderr[-300:])
    try:
        with open(path, "rb") as handle:
            restored = _exec(env, ["pg_restore", "-U", user, "-d", temp, "--no-password", "--exit-on-error"],
                             stdin=handle, capture_output=True, timeout=1800)
        if restored.returncode:
            raise BackupError("복원 실패: " + restored.stderr.decode("utf-8", "replace")[-500:])
        backup_counts, backup_versions = _counts(env, temp)
        live_counts, live_versions = _counts(env, live)
    finally:
        run(["dropdb", "-U", user, "--if-exists", temp])
    diff = {t: (backup_counts[t], live_counts[t]) for t in CHECK_TABLES if backup_counts[t] != live_counts[t]}
    return {"file": path.name, "restored_ok": True, "counts": backup_counts, "version_schemas": backup_versions,
            "differs_from_live_now": diff, "note": "백업 이후 운영 DB 가 바뀌었으면 차이가 있을 수 있습니다." if diff else "운영 DB 와 같음"}


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(description="DB 백업")
    ap.add_argument("command", nargs="?", default="create", choices=("create", "list", "verify"))
    ap.add_argument("name", nargs="?", help="verify 할 백업 파일 이름 (기본: 가장 최근)")
    a = ap.parse_args(argv)
    try:
        if a.command == "create":
            result = create()
        elif a.command == "list":
            env = settings(); folder = backup_dir(env)
            result = {"folder": str(folder), "keep": KEEP, "files": [{"name": p.name, "bytes": p.stat().st_size} for p in backups(folder)]}
        else:
            result = verify(a.name)
    except (BackupError, subprocess.TimeoutExpired) as error:
        print("중단: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
