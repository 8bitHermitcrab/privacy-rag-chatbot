"""로컬 시험 전용: 웹 챗봇을 확인할 직원 계정을 만들고, 담당자가 없는 문서를 시험팀으로 확정합니다.

    python -m tools.dev_chat_account --create                 # 사번 DEV-0001 로 계정 만들기 (아이디 = 사번)
    python -m tools.dev_chat_account --create --emp T-1001    # 다른 사번으로
    python -m tools.dev_chat_account --create --scope OWN     # 본인 담당 문서만 검색
    python -m tools.dev_chat_account --undo              # 시험 설정 되돌리기

주의
  - 실제 담당 배정이 아니라 '담당자·팀이 비어 있는 PENDING 문서'를 시험팀으로 임의 확정합니다. 내 로컬 DB에서만 쓰세요.
  - 이미 담당자나 팀이 정해진 문서는 건드리지 않습니다.
  - .env 의 DB 관리자 계정(POSTGRES_USER/POSTGRES_PASSWORD)으로 접속합니다.
  - 되돌리기는 지우지 않고 비활성화합니다 (계정 RETIRED, 직원 PENDING, 정책 NONE, 팀 비활성, 문서 PENDING).
"""
import argparse
import re
import secrets

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from starter import config
from starter.business_admin import password_hash

TEAM, TEAM_NAME, POSITION, ROLE = "DEV_TEAM", "로컬 시험팀", "사원", "STAFF"


def admin_conn():
    env = dotenv_values(config.ROOT / ".env")
    return psycopg.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                           user=env.get("POSTGRES_USER", "admin"), password=env["POSTGRES_PASSWORD"],
                           row_factory=dict_row)


def log(cur, action, reason, after):
    actor = cur.execute("""SELECT u.user_id FROM auth.user_info u JOIN auth.role_info r USING(role_code)
        WHERE r.can_manage_users AND u.status='ACTIVE' ORDER BY u.user_id LIMIT 1""").fetchone()
    if actor:   # 업무 관리자가 아직 없으면 기록 생략
        cur.execute("""INSERT INTO auth.permission_change_log(actor_user_id,target_role_code,action,reason,after_value)
            VALUES (%s,%s,%s,%s,%s)""", (actor["user_id"], ROLE, action, reason, Jsonb(after)))


def create(emp, scope):
    login = emp   # 관리자 '회원 초대'와 같은 규칙: 아이디 = 사번
    temporary = secrets.token_urlsafe(18)
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO auth.team_info(team_id,team_name,is_active) VALUES (%s,%s,true)
            ON CONFLICT (team_id) DO UPDATE SET is_active=true""", (TEAM, TEAM_NAME))
        cur.execute("""INSERT INTO auth.employee_info(employee_id,team_id,position_code,verification_status)
            VALUES (%s,%s,%s,'CONFIRMED')
            ON CONFLICT (employee_id) DO UPDATE SET team_id=EXCLUDED.team_id,position_code=EXCLUDED.position_code,
                verification_status='CONFIRMED'""", (emp, TEAM, POSITION))
        cur.execute("""INSERT INTO auth.team_position_policy(team_id,position_code,search_scope,can_restore)
            VALUES (%s,%s,%s,false)
            ON CONFLICT (team_id,position_code) DO UPDATE SET search_scope=EXCLUDED.search_scope""", (TEAM, POSITION, scope))
        user = cur.execute("""SELECT user_id,employee_id,login_id FROM auth.user_info
            WHERE employee_id=%s OR login_id=%s ORDER BY (employee_id=%s) DESC LIMIT 1""", (emp, login, emp)).fetchone()
        if user and user["employee_id"] != emp:
            raise SystemExit(f"'{login}' 아이디가 다른 직원에 연결돼 있습니다. --emp 로 다른 사번을 쓰세요.")
        if user:
            login = user["login_id"]   # 이미 이 사번에 연결된 계정이 있으면 그 아이디를 그대로 씀
            cur.execute("""UPDATE auth.user_info SET password_hash=%s,status='ACTIVE',must_change_password=true,
                password_changed_at=now() WHERE user_id=%s""", (password_hash(temporary), user["user_id"]))
        else:
            cur.execute("""INSERT INTO auth.user_info(login_id,password_hash,employee_id,role_code,must_change_password)
                VALUES (%s,%s,%s,%s,true)""", (login, password_hash(temporary), emp, ROLE))
        docs = cur.execute("""UPDATE rag.document_info SET owner_employee_id=%s,team_id=%s,metadata_status='CONFIRMED'
            WHERE metadata_status='PENDING' AND owner_employee_id IS NULL AND team_id IS NULL
            RETURNING document_id""", (emp, TEAM)).fetchall()
        total = cur.execute("SELECT count(*) AS n FROM rag.document_info WHERE team_id=%s AND metadata_status='CONFIRMED'",
                            (TEAM,)).fetchone()["n"]
        log(cur, "DEV_SEED", "로컬 시험용 챗봇 계정 준비",
            {"login_id": login, "team_id": TEAM, "position_code": POSITION, "search_scope": scope, "documents_confirmed": len(docs)})
    print(f"시험 계정 준비 완료\n  아이디: {login}\n  임시 비밀번호: {temporary}  (첫 로그인 때 14자 이상으로 변경)\n"
          f"  팀: {TEAM} / 직책: {POSITION} / 검색 범위: {scope}\n"
          f"  이번에 확정한 문서: {len(docs)}건, 시험팀 문서 합계: {total}건\n"
          f"  주소: http://127.0.0.1:8000/chat/login")


def undo(emp):
    with admin_conn() as conn, conn.cursor() as cur:
        n = cur.execute("""UPDATE rag.document_info SET owner_employee_id=NULL,team_id=NULL,metadata_status='PENDING'
            WHERE team_id=%s RETURNING document_id""", (TEAM,)).fetchall()
        cur.execute("UPDATE auth.user_info SET status='RETIRED' WHERE employee_id=%s", (emp,))
        cur.execute("UPDATE auth.team_position_policy SET search_scope='NONE',can_restore=false WHERE team_id=%s", (TEAM,))
        cur.execute("UPDATE auth.employee_info SET verification_status='PENDING' WHERE employee_id=%s", (emp,))
        cur.execute("UPDATE auth.team_info SET is_active=false WHERE team_id=%s", (TEAM,))
        log(cur, "DEV_SEED_UNDO", "로컬 시험용 챗봇 계정 비활성화", {"team_id": TEAM, "documents_reverted": len(n)})
    print(f"되돌리기 완료: 문서 {len(n)}건 PENDING 으로, 시험 계정·직원·정책·팀 비활성화")


def main():
    ap = argparse.ArgumentParser(description="로컬 시험용 웹 챗봇 계정")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", action="store_true")
    g.add_argument("--undo", action="store_true")
    ap.add_argument("--emp", default="DEV-0001", help="시험용 사번 (아이디로도 씀)")
    ap.add_argument("--scope", choices=["TEAM", "OWN"], default="TEAM")
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', a.emp):
        raise SystemExit('사번은 영문·숫자·_.- 만 쓸 수 있습니다.')
    create(a.emp, a.scope) if a.create else undo(a.emp)


if __name__ == "__main__":
    main()
