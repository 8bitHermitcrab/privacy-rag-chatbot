"""두 탐지기 모두 처음 보는 '블라인드 평가셋' 만들기 — 문서는 HCX 가 쓰고, 정답은 넣은 값으로 자동 생성

왜 이렇게 하나
  - 내 탐지기(gen_synth 합성 데이터)와 팀원 탐지기(generated 데이터)는 각자 만든 데이터로 학습했으므로
    어느 한쪽이 만든 데이터로 평가하면 그쪽이 유리하다 → 문서 작성은 두 탐지기와 무관한 LLM(HCX)이 맡는다.
  - 개인정보 값(이름·번호·주소)은 이 스크립트가 미리 정해서 넣으라고 지시하고, 정답 위치는
    LLM 이 아니라 '넣은 값을 원문에서 문자열로 찾아서' 만든다 → 위치 오류가 없다.
  - 값이 바뀌어 들어갔거나, 목록에 없는 번호·이름을 LLM 이 지어낸 문서는 자동으로 버린다.

사용법 (레포 루트, .env 의 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 사용)
    # 1) 계획만 만들기 (값·문서 유형 확정, API 호출 없음) → 팀원과 같이 확인 가능
    python -m tools.make_blind_set plan --n 300 --seed 2026 --out data/blind \\
        --exclude-names-from data/*.jsonl
    # 2) HCX 로 문서 생성 (중간에 끊겨도 다시 실행하면 이어서 함)
    python -m tools.make_blind_set generate --out data/blind
    # 3) 정답 만들기 + 검증 → data/blind/blind_eval.jsonl (평가 파일), rejected.csv (버린 문서와 이유)
    python -m tools.make_blind_set build --out data/blind

라벨 기준은 강사님 데이터와 같다: 성+직급('김 과장')·성만은 개인정보 아님, 이름만('선영 님')은 개인정보.
값은 모두 가상의 값이다. 실제 개인정보·실제 문서를 넣지 말 것.
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import requests

LABELS = ["person", "phone_number", "email", "address", "resident_registration_number",
          "account_number", "card_number"]

# ---------------------------------------------------------------- 값 만들기
SURNAMES = ("김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 전 홍 유 고 문 양 손 배 백 허 남 심 노 하 곽 성 차 "
            "주 우 구 민 류 나 진 지 엄 채 원 천 방 공 현 함 변 염 여 추 도 소 석 선 설 마 길 연 위 표 명 기 반 왕 금 옥 육 인 맹 제 모 탁 국 어 은 편 용").split()
COMPOUND = ["남궁", "선우", "황보", "제갈", "독고", "사공", "서문"]
# 실제로 흔한 이름 (무작위 음절 조합은 '해충' 같은 어색한 이름이 나와서 쓰지 않음)
GIVEN2 = ("민준 서준 도윤 예준 시우 하준 주원 지호 지후 준우 준서 건우 도현 현우 지훈 우진 선우 서진 민재 현준 연우 유준 정우 승우 승현 시윤 준혁 은우 지환 승민 "
          "지우 유찬 윤우 민성 준영 시후 진우 지원 수호 재윤 시현 동현 태윤 민우 재원 한결 윤호 은찬 이준 민규 성민 재민 태민 상훈 영수 영호 성호 정훈 동욱 "
          "서연 서윤 지우 서현 민서 하은 하윤 윤서 지유 지민 채원 지아 수아 다은 은서 예은 수빈 소율 예린 지윤 하린 시은 유나 서아 민지 수민 채은 가은 예서 "
          "윤아 다인 은지 현서 소윤 지안 아린 연서 지현 서영 혜진 은영 미경 정희 수진 영희 미영 혜원 경희 선영 지혜 유진 은정 나연 보람 슬기 다혜 하늘 "
          "재희 경민 상우 형준 병철 종민 기훈 진호 광수 명수 태호 용준 석진 정민 혜린 소희 주희 미란 순자 옥순 춘자 말순 덕희 영자 정숙 경자").split()
GIVEN1 = list("준혁훈진현민결솔빈율윤린별담건찬람")   # 외자
TITLES_AFTER_NAME = ["님", " 고객님", " 고객", "씨", " 과장", " 대리", " 팀장", " 차장", " 주임"]

ADDR = [("서울특별시", "강서구", "공항대로"), ("서울특별시", "노원구", "동일로"), ("서울특별시", "관악구", "남부순환로"),
        ("서울특별시", "송파구", "올림픽로"), ("서울특별시", "은평구", "통일로"), ("서울특별시", "성동구", "왕십리로"),
        ("부산광역시", "해운대구", "센텀중앙로"), ("부산광역시", "사하구", "낙동대로"), ("부산광역시", "동래구", "충렬대로"),
        ("대구광역시", "북구", "칠곡중앙대로"), ("대구광역시", "동구", "아양로"), ("인천광역시", "연수구", "컨벤시아대로"),
        ("인천광역시", "부평구", "부평대로"), ("광주광역시", "서구", "상무중앙로"), ("대전광역시", "유성구", "대학로"),
        ("울산광역시", "남구", "삼산로"), ("세종특별자치시", "", "한누리대로"), ("경기도", "수원시 영통구", "광교중앙로"),
        ("경기도", "고양시 일산동구", "중앙로"), ("경기도", "화성시", "동탄대로"), ("경기도", "남양주시", "다산중앙로"),
        ("강원특별자치도", "원주시", "능라동길"), ("충청북도", "청주시 흥덕구", "가경로"), ("충청남도", "아산시", "번영로"),
        ("전북특별자치도", "전주시 덕진구", "기린대로"), ("전라남도", "순천시", "연향번영길"), ("경상북도", "포항시 남구", "포스코대로"),
        ("경상남도", "창원시 성산구", "중앙대로"), ("제주특별자치도", "제주시", "연북로")]
SIDO_SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "경기도": "경기",
              "충청남도": "충남", "경상남도": "경남"}
BUILDINGS = ["푸른숲아파트", "한빛마을", "해오름빌라", "청솔타운", "늘봄아파트", "새벽빛오피스텔", "금강빌라", "솔내아파트"]
BANKS = [("신한", "3-3-6"), ("국민", "6-2-6"), ("우리", "4-3-6"), ("농협", "3-4-4-2"), ("하나", "3-6-5"),
         ("카카오뱅크", "4-2-7"), ("기업", "3-6-2-3"), ("새마을금고", "4-4-4-1")]
MAIL_DOMAINS = ["naver.com", "gmail.com", "daum.net", "kakao.com", "hanmail.net", "outlook.kr", "nate.com"]
ROMAN = "ga na da ra ma ba sa a ja cha ka ta pa ha geon min seo ji hoon yeon woo jin hye su bin eun hyun".split()

DOC_TYPES = [
    ("고객 상담 메모", "상담원이 남긴 상담 기록. 날짜·문의 내용·처리 결과·후속 조치"),
    ("콜센터 통화 요약", "통화 녹취를 요약한 기록. 화자 구분이 있는 짧은 대화 인용 포함"),
    ("민원 회신 이메일", "고객 민원에 회신하는 정중한 이메일. 인사말·답변·안내·서명"),
    ("VOC 처리 현황 표", "여러 고객의 문의를 한 줄씩 정리한 표. 항목은 | 로 구분, 머리글 포함"),
    ("사내 메신저 대화", "직원끼리 업무 메신저로 주고받은 대화. '이름: 내용' 형식이나 짧은 말투"),
    ("지점 내부 보고서", "지점장에게 올리는 업무 보고. 번호 목록과 소제목(기호 없이 '1. 개요' 식)"),
    ("대출 서류 접수 확인서", "서류 접수 내역과 보완 요청을 정리한 양식형 문서. '항목: 값' 줄이 많음"),
    ("카드 분실·재발급 처리 기록", "분실 신고부터 재발급 발송까지의 처리 기록"),
    ("주소·연락처 변경 처리 결과", "고객 정보 변경 신청과 처리 결과 안내"),
    ("이상거래 탐지 보고", "FDS 탐지 내용·조치·검토 의견을 적은 보고"),
    ("보이스피싱 피해 접수 기록", "피해 경위·지급정지 조치·안내 사항을 적은 기록"),
    ("이벤트 당첨 안내 문자", "짧은 안내 문자 2~3건을 이어 붙인 형식"),
    ("연체 안내 공문", "격식 있는 안내 공문. 문서번호·제목·본문·붙임"),
    ("상속 관련 업무 메모", "상속인 확인·서류 안내·후속 일정을 적은 메모"),
]
NO_PII_TYPES = [("사내 공지문", "직원 대상 일반 공지. 일정·장소·유의사항"),
                ("상품 안내문", "금융 상품 설명. 금리·조건·유의사항")]


def _digits(n, rng):
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def make_name(rng, used, exclude):
    for _ in range(1000):
        sur = rng.choice(COMPOUND) if rng.random() < 0.06 else rng.choice(SURNAMES)
        given = rng.choice(GIVEN1) if rng.random() < 0.08 else rng.choice(GIVEN2)
        full = sur + given
        if full not in used and full not in exclude:
            used.add(full)
            return sur, given
    raise RuntimeError("이름 후보 부족")


def make_phone(rng):
    r = rng.random()
    if r < 0.6:
        return f"010-{_digits(4, rng)}-{_digits(4, rng)}"
    if r < 0.7:
        return "010" + _digits(8, rng)
    if r < 0.8:
        return f"010 {_digits(4, rng)} {_digits(4, rng)}"
    area = rng.choice(["02", "031", "032", "051", "053", "062", "042"])
    return f"{area}-{_digits(3 if area == '02' and rng.random() < .5 else 4, rng)}-{_digits(4, rng)}"


def make_email(rng):
    uid = rng.choice(ROMAN) + rng.choice(ROMAN) + (str(rng.randint(1, 99)) if rng.random() < .6 else "")
    if rng.random() < .3:
        uid = uid[:3] + rng.choice([".", "_"]) + uid[3:]
    return f"{uid}@{rng.choice(MAIL_DOMAINS)}"


def make_address(rng):
    sido, sgg, road = rng.choice(ADDR)
    if rng.random() < .25 and sido in SIDO_SHORT:
        sido = SIDO_SHORT[sido]
    base = " ".join(x for x in (sido, sgg, road) if x) + f" {rng.randint(1, 450)}"
    r = rng.random()
    if r < .45:
        base += f", {rng.randint(101, 115)}동 {rng.randint(1, 25)}{rng.randint(1, 4):02d}호"
    elif r < .7:
        base += f" {rng.choice(BUILDINGS)} {rng.randint(101, 1505)}호"
    return base


def make_rrn(rng):
    y, m, d = rng.randint(50, 99) if rng.random() < .7 else rng.randint(0, 5), rng.randint(1, 12), rng.randint(1, 28)
    g = rng.choice("12") if y >= 50 else rng.choice("34")
    return f"{y:02d}{m:02d}{d:02d}-{g}{_digits(6, rng)}"


def make_account(rng):
    bank, fmt = rng.choice(BANKS)
    acct = "-".join(_digits(int(k), rng) for k in fmt.split("-"))
    if rng.random() < .15:
        acct = acct.replace("-", "")
    return bank, acct


def make_card(rng):
    parts = [_digits(4, rng) for _ in range(4)]
    parts[0] = rng.choice(["4", "5", "9"]) + parts[0][1:]
    r = rng.random()
    return "-".join(parts) if r < .7 else (" ".join(parts) if r < .85 else "".join(parts))


def load_exclude(patterns):
    names = set()
    for pat in patterns or []:
        for path in glob.glob(pat):
            try:
                for line in open(path, encoding="utf-8"):
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    text = d.get("text", "")
                    for e in d.get("entities", []):
                        if e.get("label") == "person" and text:
                            n = re.sub(r"\s+", "", text[e["start"]:e["end"]])
                            names.add(n)
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
    return names


def plan(n, seed, exclude):
    rng = random.Random(seed)
    used = set()
    docs = []
    for i in range(n):
        if rng.random() < 0.06:
            dtype, desc = rng.choice(NO_PII_TYPES)
            docs.append({"id": f"B{i + 1:04d}", "doc_type": dtype, "desc": desc, "people": []})
            continue
        dtype, desc = rng.choice(DOC_TYPES)
        k = rng.choice([1, 1, 2, 2, 3]) if "표" not in dtype else rng.randint(3, 5)
        people = []
        for j in range(k):
            sur, given = make_name(rng, used, exclude)
            p = {"role": "고객" if j == 0 or rng.random() < .6 else rng.choice(["상속인", "보증인", "가족", "직원", "신청인"]),
                 "full": sur + given, "given": given,
                 "call_given": len(given) >= 2 and rng.random() < 0.35,   # 뒤에서 이름만으로도 부르기
                 "spaced": rng.random() < 0.08}                            # '윤 선영' 처럼 띄어 쓰기
            if p["spaced"]:
                p["full"] = sur + " " + given
            if rng.random() < .55:
                p["phone_number"] = make_phone(rng)
            if rng.random() < .2:
                p["email"] = make_email(rng)
            if rng.random() < .35:
                p["address"] = make_address(rng)
            if rng.random() < .18:
                p["resident_registration_number"] = make_rrn(rng)
            if rng.random() < .25:
                p["bank"], p["account_number"] = make_account(rng)
            if rng.random() < .18:
                p["card_number"] = make_card(rng)
            people.append(p)
        docs.append({"id": f"B{i + 1:04d}", "doc_type": dtype, "desc": desc, "people": people})
    return docs


# ---------------------------------------------------------------- HCX 요청
FIELD_KO = {"phone_number": "연락처", "email": "이메일", "address": "주소", "resident_registration_number": "주민등록번호",
            "account_number": "계좌번호", "card_number": "카드번호"}


def required_values(d):
    """문서에 반드시 그대로 들어가야 하는 값 목록 [(라벨, 값)]"""
    out = []
    for p in d["people"]:
        out.append(("이름", p["full"]))
        for f in ("phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"):
            if f in p:
                out.append((FIELD_KO[f], p[f]))
    return out


def prompt_for(d, rng):
    lines = [f"문서 유형: {d['doc_type']} — {d['desc']}",
             f"분량: 한국어 {rng.choice(['300~500', '400~700', '600~900'])}자 정도"]
    if d["people"]:
        lines.append("\n등장 인물 (모두 가상의 인물):")
        for k, p in enumerate(d["people"], 1):
            extra = f" — 문서 뒤쪽에서 한 번 이상 성 없이 '{p['given']} 님'처럼 이름만으로도 부를 것" if p["call_given"] else ""
            lines.append(f"{k}. {p['role']} {p['full']}{extra}")
        lines.append("\n본문에 반드시 그대로 넣어야 하는 값 (하나도 빠짐없이, 글자·띄어쓰기·하이픈 그대로):")
        for k, p in enumerate(d["people"], 1):
            for f in ("phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"):
                if f in p:
                    bank = f" ({p['bank']}은행)" if f == "account_number" else ""
                    lines.append(f"- {p['full']}의 {FIELD_KO[f]}: {p[f]}{bank}")
    rules = [
        "위 값은 모두 본문에 한 번 이상, 바꾸지 않고 그대로 쓴다. 다 쓴 뒤 목록의 값이 전부 본문에 있는지 스스로 확인한다.",
        "빈칸이나 자리표시([ ], OOO, XXX, ○○○)를 쓰지 않고, 번호를 별표(*)로 가리지 않는다. 고객센터 번호가 필요하면 1588-0000 같은 대표번호를 쓴다.",
        "목록에 없는 사람 이름, 전화번호, 이메일, 주소, 주민등록번호, 계좌번호, 카드번호를 새로 만들지 않는다.",
        "직원·담당자·서명에는 절대 성+이름을 쓰지 않는다. '담당자', '상담원', '김 과장', '박 대리'처럼 성과 직급만 쓴다.",
        "은행·회사·기관 이름, 날짜, 금액, 접수번호 같은 다른 정보는 자연스럽게 써도 된다.",
        "마크다운 기호(#, **, ```), 설명이나 머리말 없이 문서 본문만 출력한다.",
    ]
    if not d["people"]:
        rules = ["어떤 개인의 이름·연락처·주소·주민등록번호·계좌번호·카드번호도 쓰지 않는다.", rules[1], rules[3], rules[4], rules[5]]
    lines.append("\n규칙:\n" + "\n".join(f"- {r}" for r in rules))
    return "\n".join(lines)


SYSTEM = "너는 한국 금융회사의 업무 문서를 실제처럼 작성하는 데이터 작성자다. 모든 개인정보는 사용자가 준 가상의 값만 쓴다."


def call_llm(user, temperature, history=None):
    base, key, model = os.getenv("LLM_API_BASE", ""), os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", "")
    if not (base and key and model):
        raise SystemExit(".env 에 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 이 필요합니다.")
    body = {"model": model, "temperature": temperature, "max_completion_tokens": 2048,
            "messages": [{"role": "system", "content": SYSTEM}, *(history or []), {"role": "user", "content": user}]}
    effort = os.getenv("BLIND_REASONING", "none")
    if effort:
        body["reasoning_effort"] = effort
    # 400 이면 모델이 받지 않는 옵션을 하나씩 빼고 다시 시도 (HCX-007 은 max_tokens 대신 max_completion_tokens)
    fallbacks = ["reasoning_effort", "max_completion_tokens", "temperature"]
    for attempt in range(10):
        r = requests.post(base.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=180)
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code == 400:
            drop = next((k for k in fallbacks if k in body), None)
            if drop:
                print(f"  400 → '{drop}' 옵션을 빼고 다시 시도 ({r.text[:200]})")
                body.pop(drop)
                continue
            raise SystemExit(f"LLM 400: {r.text[:500]}")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("LLM 429 가 계속됨")


def mock_text(d):
    """API 없이 파이프라인만 시험할 때 (평가에 쓰지 말 것)"""
    out = [f"{d['doc_type']} 기록"]
    for p in d["people"]:
        s = f"{p['role']} {p['full']}님 문의."
        for f in ("phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"):
            if f in p:
                s += f" {FIELD_KO[f]}: {p[f]}."
        if p["call_given"]:
            s += f" 담당 김 과장이 {p['given']} 님께 회신함."
        out.append(s)
    return "\n".join(out)


# ---------------------------------------------------------------- 정답 만들기·검증
HANGUL = re.compile(r"[가-힣]")
NUM_PATTERNS = [re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"),   # 휴대전화
                re.compile(r"(?<!\d)0(?:2|[3-6]\d)-\d{3,4}-\d{4}(?!\d)"),          # 지역번호 전화
                re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)"),                         # 주민
                re.compile(r"(?<!\d)(?:\d{4}([- ])\d{4}\1\d{4}\1\d{4}|\d{4}-\d{6}-\d{5})(?!\d)"),  # 카드 모양 (4-4-4-4, 4-6-5)
                re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")]                               # 이메일
ACCOUNT_LIKE = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,7}){1,3}(?![\d-])|(?<!\d)\d{9,14}(?!\d)")
# 자리표시: "[주소를 입력하세요]" 같은 빈칸, OOO/○○○, 가린 번호(010-****-1234). 제목용 [이벤트 안내] 나 [윤서연 고객님] 은 허용
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,30}(?:입력|기재|작성|넣어|채워|예시)[^\]\n]{0,30}\]|[OＯ○]{2,}|[X×]{3,}|[\d-]\*{2,}|\*{2,}[\d-]|[\w.]\*+@|@\*|\d-?\*(?![\s*])|\*[\d@]")
DATE_MASK = re.compile(r"\*\s?[년월일시분]")   # 날짜를 가린 것: 버리지는 않고 생성 때 고쳐 달라고만 요청
MARKDOWN = [(re.compile(r"\*\*([^*\n]+?)\*\*"), r"\1"),            # **굵게** → 굵게 (가린 번호 ****는 남음)
            (re.compile(r"__([^_\n]+?)__"), r"\1"),
            (re.compile(r"(?m)^\s*(?:#{1,6}\s+)"), ""),
            (re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$"), "")]


def clean_markdown(text):
    for pat, rep in MARKDOWN:
        text = pat.sub(rep, text)
    return text
ACCOUNT_CUE = re.compile(r"계좌|입금|출금|이체|송금|카드")
TITLE_WORDS = "님|씨|고객|과장|대리|팀장|차장|부장|사원|주임|선생|원장|지점장"
SUR_ALT = "|".join(sorted(COMPOUND + SURNAMES, key=len, reverse=True))
# 성 + 이름 2글자 + 호칭 (3글자 이상만 의심: '신규 고객' 같은 2글자 일반어는 제외)
NAME_LIKE = re.compile(rf"(?<![가-힣])((?:{SUR_ALT})[가-힣]{{2}})\s?(?={TITLE_WORDS})")
COMMON_GIVEN = set(GIVEN2) | set(("소영 영숙 미정 경희 유나 예서 지우 순자 영미 은주 미숙 정미 현주 수정 은희 혜영 경숙 명숙 미라 지영 성희 "
                                   "민수 영철 철수 영진 준호 성수 정호 상철 동수 재훈 현철 승훈 길동 수현 지수 민정 은비 하나 보영 예지 "
                                   "서영 다연 채린 지은 수연 민아 혜수 소연 미나 주연 은혜 정아 현정 진영 혜정 수경 유리 은숙 경미").split())
COMMON_NAME = re.compile(rf"(?<![가-힣])((?:{SUR_ALT})\s?(?:{'|'.join(sorted(COMMON_GIVEN, key=len, reverse=True))}))(?=[^가-힣]|[이가은는을를의에께님씨도와과랑입야아한]|$)")   # 뒤가 조사·호칭일 때만 ('고지우편' 제외)
# '원하는 고객', '방문한 고객', '은행권 고객' 처럼 끝이 조사·어미인 말은 이름이 아님
NOT_NAME_END = set("는을를의에로와도된할될한던인권원측별용")
NOT_NAMES = set("주거래 방문객 주민번 한국인 외국인 연체자 신청인 동거인 대리인 명의인 수령인 예금주 계약자 피해자 가입자 사용자 소유자 상속인 "
                "보증인 채무자 채권자 납부자 고령자 장애인 이용자 거래자 신규자 우수자 기존의 해당자 대상자 관계자 책임자".split())


def find_all(text, value):
    out, i = [], text.find(value)
    while i >= 0:
        out.append((i, i + len(value)))
        i = text.find(value, i + 1)
    return out


CALL_AFTER = re.compile(r"\s?(?:님|씨)")


def apply_given_calls(d, text):
    """HCX 가 '이름만 부르기'를 따르지 않은 인물은, 성+이름이 2번 이상 나올 때 마지막 '○○○ 님/씨' 하나를
    '○○ 님/씨' 로 바꾼다 (첫 등장은 성+이름 그대로 둠). 바꾼 인물 수를 함께 돌려준다."""
    changed = 0
    for p in d["people"]:
        if not p["call_given"] or p["spaced"]:
            continue
        full, given = p["full"], p["given"]
        fulls = [s0 for s0, _ in find_all(text, full) if s0 == 0 or not HANGUL.match(text[s0 - 1])]
        if any((s0 == 0 or not HANGUL.match(text[s0 - 1])) and not any(f <= s0 < f + len(full) for f in fulls)
               for s0, _ in find_all(text, given)):
            continue                      # 이미 이름만 부른 곳이 있음
        cands = [s0 for s0 in fulls[1:] if CALL_AFTER.match(text, s0 + len(full))]
        if not cands:
            continue
        s0 = cands[-1]
        text = text[:s0] + given + text[s0 + len(full):]
        changed += 1
    return text, changed


def find_issues(d, text):
    """고쳐 쓰기 요청에 쓸 문제 목록 (사람이 읽는 문장)"""
    t = clean_markdown(re.sub(r"^```[a-z]*\s*|\s*```$", "", text.strip()))
    out = []
    miss = [f"{k} {v}" for k, v in required_values(d) if v not in t]
    if miss:
        out.append("다음 값이 빠졌거나 바뀌었으니 글자 그대로 넣어 줘: " + " / ".join(miss))
    if DATE_MASK.search(t):
        out.append("'*월 *일'처럼 별표로 가린 날짜를 실제 날짜로 바꿔 줘")
    if PLACEHOLDER.search(t):
        out.append("별표로 가린 번호나 자리표시(OOO, [입력하세요] 등)를 없애고, 고객센터 번호는 1588-0000 같은 대표번호로 바꿔 줘")
    known = {re.sub(r"\s", "", p["full"]) for p in d["people"]}
    fake = sorted({m.group(1) for m in NAME_LIKE.finditer(t)
                   if m.group(1) not in known and m.group(1) not in NOT_NAMES and m.group(1)[-1] not in NOT_NAME_END
                   and not any(m.group(1) in re.sub(r"\s", "", p["full"]) for p in d["people"])})
    if fake:
        out.append("목록에 없는 이름 " + ", ".join(fake) + " 은(는) '김 과장'처럼 성과 직급만 남기고 이름을 지워 줘")
    givens = [p for p in d["people"] if p["call_given"]
              and not any(not HANGUL.match(t[s0 - 1]) if s0 else True for s0, _ in find_all(t, p["given"])
                          if not any(fs <= s0 < fe for fs, fe in find_all(t, p["full"])))]
    if givens:
        out.append("다음 사람은 문서 뒤쪽에서 성 없이 이름만으로도 한 번 이상 불러 줘: "
                   + ", ".join(f"{p['full']} → '{p['given']} 님'" for p in givens))
    return out


def build_doc(d, text):
    text = clean_markdown(re.sub(r"^```[a-z]*\s*|\s*```$", "", text.strip()))
    m = PLACEHOLDER.search(text)
    if m:
        return None, f"자리표시 남음: {m.group(0)[:12]}"
    text, n_given_edit = apply_given_calls(d, text)
    spans = []
    for p in d["people"]:
        full_spans = [s for s in find_all(text, p["full"]) if s[0] == 0 or not HANGUL.match(text[s[0] - 1])]
        if not full_spans:
            return None, f"이름 누락: {p['full']}"
        form = "spaced" if p["spaced"] else "full"
        spans += [(s, e, "person", form) for s, e in full_spans]
        sur = p["full"].replace(" ", "")[:len(p["full"].replace(" ", "")) - len(p["given"])]
        alt = sur + " " + p["given"] if not p["spaced"] else sur + p["given"]          # 띄어쓰기만 다른 표기
        alt_spans = [s for s in find_all(text, alt) if s[0] == 0 or not HANGUL.match(text[s[0] - 1])]
        spans += [(s, e, "person", "full" if p["spaced"] else "spaced") for s, e in alt_spans]
        full_spans = full_spans + alt_spans
        if len(p["given"]) >= 2:      # 지시 여부와 상관없이, 이름만 부른 곳은 모두 개인정보
            g = [s for s in find_all(text, p["given"])
                 if (s[0] == 0 or not HANGUL.match(text[s[0] - 1]))
                 and not any(fs <= s[0] < fe for fs, fe in full_spans)]
            spans += [(s, e, "person", "given") for s, e in g]
        for f in ("phone_number", "email", "address", "resident_registration_number", "account_number", "card_number"):
            if f in p:
                hits = find_all(text, p[f])
                if not hits:
                    return None, f"값 누락: {f}"
                spans += [(s, e, f, None) for s, e in hits]
    # 겹침 정리 (긴 것 우선)
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    kept = []
    for s in spans:
        if kept and s[0] < kept[-1][1]:
            continue
        kept.append(s)
    covered = lambda a, b: any(s < b and a < e for s, e, _, _ in kept)   # noqa: E731
    for pat in NUM_PATTERNS:
        for m in pat.finditer(text):
            if not covered(m.start(), m.end()):
                return None, f"목록에 없는 번호·이메일: {re.sub(r'[0-9A-Za-z]', '#', m.group(0))}"
    for m in ACCOUNT_LIKE.finditer(text):     # 계좌처럼 보이는 번호는 앞에 계좌·입금 같은 말이 있을 때만 의심
        if not covered(m.start(), m.end()) and len(re.sub(r"\D", "", m.group(0))) >= 9 \
                and ACCOUNT_CUE.search(text[max(0, m.start() - 15):m.start()]):
            return None, f"목록에 없는 계좌 의심: {re.sub(r'[0-9]', '#', m.group(0))}"
    known = {re.sub(r"\s", "", p["full"]) for p in d["people"]}
    for m in COMMON_NAME.finditer(text):
        name = re.sub(r"\s", "", m.group(1))
        if not covered(m.start(1), m.end(1)) and name not in known:
            return None, f"목록에 없는 이름 의심: {name[0]}{'○' * (len(name) - 1)}"
    for m in NAME_LIKE.finditer(text):
        name = m.group(1)
        if covered(m.start(1), m.end(1)) or name in known or name in NOT_NAMES or name[-1] in NOT_NAME_END:
            continue
        return None, f"목록에 없는 이름 의심: {name[0]}{'○' * (len(name) - 1)}"
    ents = [{"label": lab, "start": s, "end": e, "text": text[s:e], **({"form": form} if form else {})}
            for s, e, lab, form in kept]
    doc = {"id": d["id"], "doc_type": d["doc_type"], "source": "hcx_blind", "text": text, "entities": ents}
    if n_given_edit:
        doc["edit"] = f"이름만 부르기 {n_given_edit}곳 후처리"
    return doc, None


# ---------------------------------------------------------------- 명령
def cmd_plan(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    exclude = load_exclude(a.exclude_names_from)
    docs = plan(a.n, a.seed, exclude)
    with open(out / "plan.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    n_ent = sum(1 + sum(k in p for k in FIELD_KO) for d in docs for p in d["people"])
    print(f"계획 {len(docs)}건 (개인정보 없는 문서 {sum(not d['people'] for d in docs)}), 값 약 {n_ent}개, "
          f"제외한 기존 이름 {len(exclude)}개 → {out / 'plan.jsonl'}")


def cmd_generate(a):
    out = Path(a.out)
    docs = [json.loads(l) for l in open(out / "plan.jsonl", encoding="utf-8")]
    raw_path = out / "raw.jsonl"
    done = {json.loads(l)["id"] for l in open(raw_path, encoding="utf-8")} if raw_path.exists() else set()
    todo = [d for d in docs if d["id"] not in done][: a.limit or None]
    print(f"생성할 문서 {len(todo)}건 (이미 {len(done)}건)")
    with open(raw_path, "a", encoding="utf-8") as f:
        for k, d in enumerate(todo, 1):
            rng = random.Random(d["id"])
            if a.mock:
                text = mock_text(d)
            else:
                first = prompt_for(d, rng)
                text = call_llm(first, a.temperature)
                for _ in range(3):    # 빠진 값·가린 번호·지어낸 이름이 있으면 콕 집어 고쳐 쓰기 요청
                    issues = find_issues(d, text)
                    if not issues:
                        break
                    fix = "앞 문서를 아래처럼 고쳐서 전체를 다시 출력해 줘. 다른 규칙은 그대로 지켜 줘.\n- " + "\n- ".join(issues)
                    time.sleep(a.sleep)
                    text = call_llm(fix, a.temperature,
                                    history=[{"role": "user", "content": first}, {"role": "assistant", "content": text}])
            f.write(json.dumps({"id": d["id"], "text": text}, ensure_ascii=False) + "\n")
            f.flush()
            if k % 10 == 0:
                print(f"  {k}/{len(todo)}")
            if not a.mock:
                time.sleep(a.sleep)


def cmd_build(a):
    out = Path(a.out)
    plan_ = {d["id"]: d for d in map(json.loads, open(out / "plan.jsonl", encoding="utf-8"))}
    raws = {}
    for l in open(out / "raw.jsonl", encoding="utf-8"):
        r = json.loads(l)
        raws[r["id"]] = r["text"]
    good, bad = [], []
    for i, text in raws.items():
        doc, why = build_doc(plan_[i], text)
        (good.append(doc) if doc else bad.append((i, plan_[i]["doc_type"], why)))
    good.sort(key=lambda d: d["id"])
    path = out / "blind_eval.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for d in good:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(out / "rejected.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("id", "doc_type", "reason"), *bad])
    cnt = {l: sum(e["label"] == l for d in good for e in d["entities"]) for l in LABELS}
    forms = {k: sum(e.get("form") == k for d in good for e in d["entities"]) for k in ("full", "given", "spaced")}
    want = sum(p["call_given"] for d in good for p in plan_[d["id"]]["people"])
    got = sum(1 for d in good for p in plan_[d["id"]]["people"] if p["call_given"]
              and any(e.get("form") == "given" and e["text"] == p["given"] for e in d["entities"]))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    edited = sum(1 for d in good if "edit" in d)
    print(f"이름만 부르기: 지시한 {want}명 중 {got}명 반영 (그중 HCX 가 안 따라 후처리한 문서 {edited}건)")
    print(f"통과 {len(good)}건 / 버림 {len(bad)}건 (이유: {out / 'rejected.csv'})")
    print(f"개인정보 {sum(cnt.values())}개: {cnt}")
    print(f"이름 형태: {forms}")
    print(f"평가 파일: {path}  sha256 앞 16자리 {sha}  ← 이 값을 팀원과 공유해 같은 파일인지 확인")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", default="data/blind")
    p.add_argument("--exclude-names-from", nargs="*", default=[], help="여기 나온 이름은 쓰지 않음 (jsonl, glob 가능)")
    g = sub.add_parser("generate")
    g.add_argument("--out", default="data/blind")
    g.add_argument("--limit", type=int, default=0, help="이번에 만들 최대 문서 수 (0 = 전부)")
    g.add_argument("--sleep", type=float, default=1.5, help="요청 사이 쉬는 시간(초)")
    g.add_argument("--temperature", type=float, default=0.5)
    g.add_argument("--mock", action="store_true", help="API 없이 파이프라인 시험 (평가에 쓰지 말 것)")
    b = sub.add_parser("build")
    b.add_argument("--out", default="data/blind")
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    {"plan": cmd_plan, "generate": cmd_generate, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
