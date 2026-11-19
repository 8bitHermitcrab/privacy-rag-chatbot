"""로컬 비밀 설정·관리자 인증·세션·작업 기록·최초 계정 준비."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]

def state_dir():
    override = os.getenv("SERVER_ADMIN_STATE_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise RuntimeError("관리자 설정 경로는 절대 경로여야 합니다.")
    elif os.name == "nt":
        path = Path(os.environ["LOCALAPPDATA"]) / "RagServerAdmin"
    else:
        path = Path.home() / ".local" / "share" / "rag-server-admin"
    path = path.resolve()
    if path == REPO or REPO in path.parents:
        raise RuntimeError("관리자 비밀 설정은 Git 작업 폴더 밖에 저장해야 합니다.")
    return path

def prepare_private_dir():
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        account = os.environ.get("USERDOMAIN", ".") + "\\" + os.environ["USERNAME"]
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
            account + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"], capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        if result.returncode:
            raise RuntimeError("관리자 설정 폴더의 접근 권한을 제한하지 못했습니다.")
    else:
        path.chmod(0o700)
    return path

def write_private(name, value):
    path = state_dir() / name
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".admin-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if isinstance(value, str):
                handle.write(value)
            else:
                json.dump(value, handle, ensure_ascii=False, indent=2)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def read_private(name):
    return json.loads((state_dir() / name).read_text(encoding="utf-8"))

ITERATIONS = 600_000

IDLE_SECONDS = 30 * 60

ABSOLUTE_SECONDS = 8 * 60 * 60

COOKIE = "rag_server_admin"

LOCK = threading.RLock()

def make_password(password):
    if not 14 <= len(password) <= 256:
        raise ValueError("비밀번호는 14~256자로 설정하세요.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return {"algorithm":"pbkdf2_sha256", "iterations":ITERATIONS, "salt":salt, "digest":digest}

def verify_password(password, record):
    if record.get("algorithm") != "pbkdf2_sha256" or record.get("iterations") != ITERATIONS:
        raise RuntimeError("지원하지 않는 관리자 인증 설정입니다.")
    if len(password) > 256:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), ITERATIONS).hex()
    return hmac.compare_digest(digest, record["digest"])

def audit(event, result="success"):
    # 요청 본문·비밀번호·토큰·연결 문자열·원문 로그는 기록하지 않습니다.
    with LOCK:
        path = state_dir() / "audit.jsonl"
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.replace(state_dir() / "audit.previous.jsonl")
        record = {"time":datetime.now(timezone.utc).isoformat(), "event":event, "result":result}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if __import__("os").name != "nt":
            path.chmod(0o600)

def recent_events():
    with LOCK:
        path = state_dir() / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()[-30:]][::-1]

class Security:
    def __init__(self):
        self.sessions = {}
        self.record = read_private("auth.json")

    def login(self, username, password):
        with LOCK:
            now = time.time()
            path = state_dir() / "login_attempts.json"
            failures = read_private("login_attempts.json") if path.exists() else []
            failures = [t for t in failures if now - t < 15 * 60]
            if len(failures) >= 5:
                return None, "locked"
            valid = verify_password(password, self.record["password"])
            valid = valid and hmac.compare_digest(username.encode(), self.record["username"].encode())
            if not valid:
                failures.append(now)
                write_private("login_attempts.json", failures)
                audit("login", "failure")
                return None, "invalid"
            write_private("login_attempts.json", [])
            self.sessions = {k:v for k,v in self.sessions.items() if now-v["last"]<IDLE_SECONDS and now-v["created"]<ABSOLUTE_SECONDS}
            if len(self.sessions) >= 32:
                self.sessions.clear()
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {"created":now,"last":now,"csrf":secrets.token_urlsafe(32)}
            audit("login")
            return token, "ok"

    def session(self, token, *, touch=True):
        with LOCK:
            session = self.sessions.get(token)
            now = time.time()
            if not session:
                return None
            if now-session["last"] >= IDLE_SECONDS or now-session["created"] >= ABSOLUTE_SECONDS:
                self.sessions.pop(token, None)
                return None
            if touch:
                session["last"] = now
            return dict(session)

    def logout(self, token):
        with LOCK:
            self.sessions.pop(token, None)
            audit("logout")

    def change_password(self, old, new):
        with LOCK:
            if not verify_password(old, self.record["password"]):
                return False
            if hmac.compare_digest(old.encode(), new.encode()):
                raise ValueError("기존 비밀번호와 다른 값을 사용하세요.")
            self.record["password"] = make_password(new)
            write_private("auth.json", self.record)
            self.sessions.clear()
            initial = state_dir() / "initial-login.txt"
            if initial.exists():
                initial.unlink()
            audit("password_change")
            return True

def setup_account(*, username='serveradmin', generate=False, reset=False):
    """프로그램을 중지한 상태에서 CLI로만 호출하는 최초 계정 준비."""
    if not username.isascii() or not username.replace('_','').isalnum() or len(username)>64:
        raise ValueError('계정명은 영문·숫자·밑줄 1~64자만 사용할 수 있습니다.')
    prepare_private_dir()
    if (state_dir()/'auth.json').exists() and not reset:
        raise ValueError('이미 계정이 있습니다. 재설정할 때만 --reset을 지정하세요.')
    password=secrets.token_urlsafe(24) if generate else getpass.getpass('새 관리자 비밀번호 (14자 이상): ')
    if not generate and password!=getpass.getpass('비밀번호 확인: '):
        raise ValueError('비밀번호가 다릅니다.')
    write_private('auth.json',{'username':username,'password':make_password(password)})
    write_private('login_attempts.json',[])
    if generate:
        write_private('initial-login.txt','서버 관리자 최초 로그인\n계정: '+username+'\n비밀번호: '+password+'\n\n로그인 후 비밀번호 변경 시 이 파일은 삭제됩니다. Git에 복사하지 마세요.\n')
        print('최초 로그인 정보 파일: '+str(state_dir()/'initial-login.txt'))
    elif (state_dir()/'initial-login.txt').exists():
        (state_dir()/'initial-login.txt').unlink()
    audit('account_setup')
    print('서버 관리자 계정 준비 완료')
