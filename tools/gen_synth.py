"""합성 학습 데이터 생성기 — 템플릿 + 가상 인물, 채우는 순간 정답 위치 기록

    python -m tools.gen_synth --n 10000 --out data/synth_train_10k.jsonl --seed 7 \
        --exclude-names-from data/holdout_labeled.jsonl data/<강사 test> data/pii_synth_finance_gen_fresh.jsonl ...

데이터셋을 만든 방식과 같이 '사람이 쓴 빈칸 템플릿에 가상 인물 정보를 코드로 채워 넣는' 방식입니다.
  - 인물 이름: 일반 성씨 + 이름 음절 조합, 순우리말 이름, 외자 — 데이터셋 인물 목록(personas)은 쓰지 않음
  - 번호: 형식만 맞춘 무작위 가상 값 (주민번호 성별자리·날짜 형식, 카드 16자리 등)
  - 어려운 사례: 성 없이 이름만(씨·님·아·야·이랑·한테), 외자, 띄어 쓴 이름, 조사 부착, 복성,
                 번호 형식 변형(하이픈 없음·공백·점·괄호·+82)
  - 오탐 미끼(라벨 없음): 회사 대표번호, 사건·접수·주문 번호, '김 과장', 순우리말 상호·상품명, 금액·날짜
정답 기준(데이터셋과 같게): 이름은 조사·호칭 제외, 이름만 부를 때도 호칭 제외, 주소는 건물·동·호까지 포함.
출력은 데이터셋과 같은 jsonl (id, split, doc_type, template, set, text, entities, hard_cases).
"""
import argparse
import json
import random
import re

# ================================================================ 이름 재료 (일반 어휘)
SURNAMES = ("김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 류 전 홍 고 문 양 손 배 백 허 유 남 심 노 하 곽 성 차 주 "
            "우 구 민 진 나 지 엄 채 원 천 방 공 현 함 변 염 여 추 도 소 석 선 설 마 길 연 위 표 명 기 반 왕 금 옥 육 인 "
            "맹 제 모 탁 국 어 은 편 용").split()
_TOP_W = [30, 20, 12, 7, 7, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
SUR_W = _TOP_W + [1] * (len(SURNAMES) - len(_TOP_W))   # 흔한 성씨일수록 자주
COMPOUND = ["남궁", "제갈", "선우", "황보", "독고", "사공", "서문"]
G1 = list("민서지하도시예수주윤은채현준유다소승우태재건아연나가보세해이혜정경성영동상진원희미효선규한리온율")
G2 = list("준서윤우연원진민아은현호빈율린희영수정훈혁석람솔별결온담슬안나비기하경주성철용환규찬태경혜인")
NATIVE = ("하늘 가람 다운 슬기 나래 보람 한결 새롬 해솔 다온 아름 바다 이슬 초롱 한별 누리 소망 겨레 미르 다솜 "
          "하람 라온 가온 빛나 단비 봄이 여름 나린 아리 시원 은별 별이 하랑 도담 마루 온새").split()
SINGLE = list("준린솔별결혁훈현진윤담온율산란찬휘빈건웅")
EN_FIRST = "James Emily Lucas Sophia Daniel Olivia Ryan Grace Kevin Chloe".split()
EN_LAST = "Kim Lee Park Choi Jung Kang Cho Yoon Jang Lim".split()
ROMAN = {c: r for c, r in zip("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지",
                              "kim lee park choi jung kang cho yoon jang lim han oh seo shin kwon hwang ahn song ryu jeon hong "
                              "ko moon yang son bae baek heo yoo nam shim noh ha kwak sung cha joo woo koo min jin na ji".split())}

TITLES = ["대리", "과장", "차장", "부장", "팀장", "주임", "사원", "선임", "책임", "수석", "매니저", "실장", "본부장", "지점장"]
STAFF_ROLES = ["상담사", "상담원", "심사역", "담당자", "처리 담당", "접수 담당", "창구 직원"]
TEAMS = ["고객보호팀", "여신심사팀", "카드운영팀", "채권관리팀", "디지털전략팀", "정보보호팀", "리스크관리팀", "인사팀", "총무팀",
         "소비자보호부", "자산관리센터", "법인영업팀", "해외송금센터", "콜센터 2팀", "준법감시팀", "상속지원센터"]
BANKS = ["온누리은행", "하늘빛은행", "새벽은행", "한빛저축은행", "누리농협", "바다수협", "푸른은행", "별빛증권"]
BRAND = "새봄카드"

# 순우리말 상호·상품명 미끼 (라벨 없음) — 이름과 같은 단어가 상호·상품으로 쓰이는 경우
ORG_TAIL = ["상사", "상회", "오토", "푸드", "마트", "빌딩", "약국", "식당", "카페", "테크", "솔루션", "물산"]
PRODUCT_TAIL = ["플러스 카드", "체크카드", "적금", "정기예금", "포인트 카드", "멤버십", "대출"]

SIDO = [("서울특별시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"), ("광주광역시", "광주"),
        ("대전광역시", "대전"), ("울산광역시", "울산"), ("경기도", "경기"), ("강원특별자치도", "강원"), ("충청북도", "충북"),
        ("충청남도", "충남"), ("전북특별자치도", "전북"), ("경상남도", "경남"), ("제주특별자치도", "제주")]
GU = ["중구", "동구", "서구", "남구", "북구", "강남구", "송파구", "마포구", "수성구", "해운대구", "연수구", "유성구", "달서구",
      "수원시 팔달구", "성남시 분당구", "용인시 수지구", "고양시 일산동구", "청주시 상당구", "천안시 동남구", "창원시 의창구",
      "춘천시", "전주시 완산구", "제주시", "김해시", "원주시"]
ROAD = ["새벽로", "마루길", "도담길", "여울로", "별빛길", "미리내로", "솔내길", "은하수로", "가온로", "푸른길", "한울로", "누리로"]
DONG = ["범어동", "둔산동", "창전동", "죽전동", "중동", "연산동", "봉명동", "역삼동", "정자동", "효자동", "우동", "명륜동"]
BLDG = ["아파트", "빌라", "오피스텔", "타워", "맨션", "하우스"]


def has_batchim(word):
    ch = word[-1]
    if not ("가" <= ch <= "힣"):
        return ch in "0136789LMNR"
    return (ord(ch) - 0xAC00) % 28 != 0


def josa(word, pair):
    """pair: '이가', '은는', '을를', '과와', '아야', '이랑랑', '이에요예요'"""
    table = {"이가": ("이", "가"), "은는": ("은", "는"), "을를": ("을", "를"), "과와": ("과", "와"), "아야": ("아", "야"),
             "이랑": ("이랑", "랑"), "이에요": ("이에요", "예요"), "으로": ("으로", "로"), "이한테": ("이한테", "한테")}
    a, b = table[pair]
    return a if has_batchim(word) else b


# ================================================================ 문서 조립기
class Doc:
    def __init__(self):
        self.parts, self.ents, self.n, self.hard = [], [], 0, set()

    def t(self, s):
        self.parts.append(s)
        self.n += len(s)
        return self

    def e(self, label, s, form=None, pid=None):
        ent = {"label": label, "text": s, "start": self.n, "end": self.n + len(s)}
        if form:
            ent["form"] = form
        if pid:
            ent["person_id"] = pid
        self.ents.append(ent)
        return self.t(s)

    @property
    def text(self):
        return "".join(self.parts)


# ================================================================ 가상 인물과 값
class Person:
    _id = 0
    EXCLUDE = set()     # 평가 세트에 나오는 이름 (--exclude-names-from) — 이름 외우기로 점수가 부풀지 않게

    def __init__(self, rng, role=None):
        for _ in range(50):
            self._make(rng, role)
            if self.full not in Person.EXCLUDE and self.given not in Person.EXCLUDE:
                break

    def _make(self, rng, role=None):
        Person._id += 1
        self.pid = f"SP{Person._id:06d}"
        r = rng.random()
        self.sur = rng.choice(COMPOUND) if r < 0.04 else rng.choices(SURNAMES, SUR_W)[0]
        r = rng.random()
        if r < 0.10:
            self.given = rng.choice(SINGLE)                       # 외자
        elif r < 0.28:
            self.given = rng.choice(NATIVE)                       # 순우리말
        else:
            g1 = rng.choice(G1)
            self.given = g1 + rng.choice([c for c in G2 if c != g1])
        self.role = role or rng.choice(["customer", "customer", "staff"])
        self.title = rng.choice(TITLES)
        self.team = rng.choice(TEAMS)
        self.rng = rng

    @property
    def full(self):
        return self.sur + self.given


def phone(rng, mobile=True):
    if mobile or rng.random() < 0.8:
        a, b = f"{rng.randint(0, 9999):04d}", f"{rng.randint(0, 9999):04d}"
        style = rng.random()
        if style < 0.45:
            return f"010-{a}-{b}", None
        if style < 0.62:
            return f"010{a}{b}", "phone_format_variant"
        if style < 0.74:
            return f"010.{a}.{b}", "phone_format_variant"
        if style < 0.86:
            return f"010 {a} {b}", "phone_format_variant"
        return f"+82 10-{a}-{b}", "phone_format_variant"
    area = rng.choice(["02", "031", "051", "053", "042", "062"])
    return f"{area}-{rng.randint(200, 999)}-{rng.randint(0, 9999):04d}", None


def rrn(rng):
    y, m, d = rng.randint(50, 99) if rng.random() < 0.8 else rng.randint(0, 9), rng.randint(1, 12), rng.randint(1, 28)
    g = rng.choice("12") if y >= 50 else rng.choice("34")
    front, back = f"{y:02d}{m:02d}{d:02d}", f"{g}{rng.randint(0, 999999):06d}"
    style = rng.random()
    if style < 0.5:
        return f"{front}-{back}", None
    if style < 0.75:
        return f"{front} {back}", "rrn_format_variant"
    return front + back, "rrn_format_variant"


def card(rng):
    ds = [f"{rng.randint(1000, 9999)}" for _ in range(4)]
    style = rng.random()
    if style < 0.5:
        return "-".join(ds), None
    if style < 0.75:
        return " ".join(ds), "card_format_variant"
    return "".join(ds), "card_format_variant"


def account(rng):
    fmt = rng.choice(["3-2-6", "3-6-2", "4-3-6", "4-2-7", "2-6-6", "3-3-6"])
    parts = ["".join(str(rng.randint(0, 9)) for _ in range(int(k))) for k in fmt.split("-")]
    if rng.random() < 0.25:
        return "".join(parts), "account_format_variant"
    return "-".join(parts), None


def email(rng, p):
    base = ROMAN.get(p.sur, "kim")
    local = rng.choice([f"{base}{rng.randint(1, 99)}", f"{base}.{rng.choice('abcdhjkmsy')}{rng.choice('abcdhjkmsy')}",
                        f"{rng.choice('abcdhjkmsy')}{rng.choice('abcdhjkmsy')}.{base}{rng.randint(1, 9)}"])
    return f"{local}@{rng.choice(['example.com', 'example.net', 'example.org', 'mail.example.co.kr'])}"


def address(rng):
    full, short = rng.choice(SIDO)
    sido = full if rng.random() < 0.6 else short
    gu = rng.choice(GU)
    if rng.random() < 0.7:
        a = f"{sido} {gu} {rng.choice(ROAD)} {rng.randint(1, 300)}"
        if rng.random() < 0.3:
            a = f"{sido} {gu} {rng.choice(ROAD)} {rng.randint(1, 50)}번길 {rng.randint(1, 60)}"
    else:
        a = f"{sido} {gu} {rng.choice(DONG)} {rng.randint(1, 999)}-{rng.randint(1, 30)}"
    r = rng.random()
    if r < 0.35:
        a += f" {rng.choice(NATIVE + ['새봄', '푸른', '중앙'])}{rng.choice(BLDG)} {rng.randint(101, 120)}동 {rng.randint(101, 2504)}호"
    elif r < 0.6:
        a += f", {rng.randint(101, 1504)}호"
    elif r < 0.7:
        a += f", {rng.randint(2, 15)}층"
    if rng.random() < 0.15:
        a += f" ({rng.choice(DONG)})"
    hard = "address_format_variant" if sido == short or "번길" in a or "-" in a else None
    return a, hard


# ================================================================ 이름 쓰기 (정답은 이름만)
def name(doc, p, mode="auto", after=""):
    """p 를 문서에 쓰고 라벨을 단다. mode: full / spaced / given / auto. after: 바로 붙일 말(조사·호칭)."""
    rng = p.rng
    if mode == "auto":
        r = rng.random()
        mode = "spaced" if r < 0.12 else "full"
    if mode == "spaced":
        s, form = f"{p.sur} {p.given}", "spaced"
        doc.hard.add("spaced_name")
    elif mode == "given":
        s, form = p.given, "given"
        doc.hard.add("given_only")
    else:
        s, form = p.full, "full"
    if len(p.given) == 1:
        doc.hard.add("single_char_name")
    if len(p.sur) == 2:
        doc.hard.add("compound_surname")
    doc.e("person", s, form, p.pid)
    if after and after[0] in "이가은는을를의과와":
        doc.hard.add("josa_attached")
    return doc.t(after)


def call_given(doc, p, rng, kind="address"):
    """성 없이 이름만: kind='address' 부르는 말('선영 씨', '선영님', '선영아'),
    kind='subj' 주어('선영 씨가', '선영님께서', '선영이는'), kind='obj' ('선영이랑', '선영한테', '선영님께')"""
    g = p.given
    if len(g) == 1:                      # 외자는 이름만 부를 때 성까지 붙여 부르는 게 자연스러움
        tail = rng.choice([" 씨", "님", " 님"])
        tail += {"address": "", "subj": rng.choice(["이", "께서", "은" if tail.endswith("님") else "는"]), "obj": "에게"}[kind]
        return name(doc, p, "full", tail)
    if kind == "address":
        tail = rng.choice([" 씨", "씨", "님", " 님", josa(g, "아야")])
    elif kind == "obj":      # ~와 같이 / ~에게
        tail = rng.choice([josa(g, "이랑"), josa(g, "이한테"), " 씨한테", "님께", " 씨에게", "님에게"])
    else:                    # 주어 자리
        tail = rng.choice([" 씨가", "님이", "님께서", " 씨는", "님은", ("이는" if has_batchim(g) else "는"),
                           ("이가" if has_batchim(g) else "가")])
    return name(doc, p, "given", tail)


# ================================================================ 오탐 미끼 (라벨 없음)
def decoy(rng):
    k = rng.random()
    if k < 0.18:
        return f"{BRAND} 고객센터 {rng.choice(['1588', '1599', '1644', '1800'])}-{rng.randint(0, 9999):04d}"
    if k < 0.32:
        return f"{rng.choice(['사건 번호', '접수번호', '주문번호', '문서번호', '민원번호', '송장번호'])}: {rng.randint(2024, 2026)}-{rng.randint(100000, 999999)}"
    if k < 0.48:
        w = rng.choice(NATIVE)
        return rng.choice([f"{w}{rng.choice(ORG_TAIL)}에서 결제된 건", f"'{w} {rng.choice(PRODUCT_TAIL)}' 상품 안내",
                           f"가맹점명 '{w}{rng.choice(ORG_TAIL)}'", f"{w}{rng.choice(ORG_TAIL)} 대표번호 {rng.choice(['1577', '1666'])}-{rng.randint(0, 9999):04d}"])
    if k < 0.62:
        return f"{rng.choice(SURNAMES[:20])} {rng.choice(TITLES)}님께 보고"                 # 성 + 직급 (라벨 없음)
    if k < 0.74:
        return f"결제 금액 {rng.randint(1, 990) * 1000:,}원"
    if k < 0.86:
        return f"처리 기한 {rng.randint(2025, 2027)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    return f"카드 뒤 4자리 {rng.randint(1000, 9999)}"


DOC_TYPES = ["카드 분실·정지 신고", "대출 한도 상담", "민원 처리 결과", "고객 정보 변경", "이상거래 확인", "연체 안내",
             "상속인 금융거래 조회", "보험금 청구 접수", "자동이체 변경", "해외송금 접수", "계좌 개설 상담", "가맹점 정보 변경",
             "법인카드 사용 소명", "채무조정 상담", "휴면예금 안내", "명의도용 신고", "투자상품 가입 상담", "퇴직연금 이전",
             "전세자금대출 심사", "카드 발급 심사", "업무 협조 메일", "사내 메신저", "회의록", "고객 칭찬 접수"]

ACTIONS = {
    "카드 분실·정지 신고": ["카드 분실 신고를 접수했습니다", "분실된 카드는 즉시 사용 정지 처리했습니다", "재발급 카드는 등록 주소로 배송됩니다"],
    "대출 한도 상담": ["추가 대출 한도를 문의하셨습니다", "소득 증빙 서류가 추가로 필요합니다", "심사 결과는 문자로 안내드립니다"],
    "민원 처리 결과": ["접수하신 민원의 처리 결과를 안내드립니다", "수수료 부과 건은 환급 처리했습니다", "불편을 드려 죄송합니다"],
    "고객 정보 변경": ["연락처 변경 요청을 접수했습니다", "주소 변경은 오늘 중 반영됩니다", "변경 내역은 앱에서 확인하실 수 있습니다"],
    "이상거래 확인": ["해외 결제 승인 건이 확인되어 연락드렸습니다", "본인 사용이 아니면 즉시 정지하겠습니다", "피해 금액은 조사 후 보상 여부를 안내드립니다"],
}
GENERIC_ACT = ["요청하신 내용을 확인했습니다", "필요 서류를 안내드렸습니다", "처리 결과는 다시 연락드리겠습니다",
               "추가 확인이 필요해 보류했습니다", "관련 부서에 전달했습니다", "오늘 중으로 처리될 예정입니다"]


def acts(rng, dt, n=2):
    pool = ACTIONS.get(dt, []) + GENERIC_ACT
    return rng.sample(pool, k=min(n, len(pool)))


# ================================================================ 템플릿들
def t_form(rng, dt):
    """'항목: 값' 서식"""
    d, c, s = Doc(), Person(rng, "customer"), Person(rng, "staff")
    d.t(f"[{dt}] 접수 내역\n\n")
    d.t(rng.choice(["신청인", "고객명", "성명", "예금주", "명의자", "청구인"]) + ": ")
    name(d, c, rng.choice(["full", "full", "spaced"]), "\n")
    if rng.random() < 0.5:
        v, h = rrn(rng); d.t("주민등록번호: "); d.e("resident_registration_number", v); d.t("\n"); h and d.hard.add(h)
    v, h = phone(rng); d.t(rng.choice(["연락처", "휴대전화", "전화번호"]) + ": "); d.e("phone_number", v); d.t("\n"); h and d.hard.add(h)
    if rng.random() < 0.6:
        v, h = address(rng); d.t(rng.choice(["주소", "자택 주소", "배송지", "변경 주소"]) + ": "); d.e("address", v); d.t("\n"); h and d.hard.add(h)
    if rng.random() < 0.5:
        v, h = account(rng); d.t(f"{rng.choice(['출금 계좌', '환급 계좌', '입금 계좌'])}: {rng.choice(BANKS)} "); d.e("account_number", v); d.t("\n"); h and d.hard.add(h)
    if rng.random() < 0.35:
        v, h = card(rng); d.t("카드번호: "); d.e("card_number", v); d.t("\n"); h and d.hard.add(h)
    if rng.random() < 0.4:
        d.t("이메일: "); d.e("email", email(rng, c)); d.t("\n")
    d.t("\n처리 내용: " + " ".join(a + "." for a in acts(rng, dt)) + "\n")
    d.t(f"담당: {s.team} "); name(d, s, "full", f" {s.title}\n")
    if rng.random() < 0.4:
        d.t(decoy(rng) + "\n")
    return d, "항목: 값 서식"


def t_chat(rng, dt):
    """사내 메신저 / 단체 대화"""
    d = Doc()
    ppl = [Person(rng, "staff") for _ in range(rng.randint(2, 3))]
    cust = Person(rng, "customer")
    d.t(f"[{rng.choice(TEAMS)} 대화방]\n")
    for i in range(rng.randint(3, 6)):
        sp = rng.choice(ppl)
        name(d, sp, rng.choice(["full", "given"]) if rng.random() < 0.7 else "full", ": ")
        k = rng.random()
        other = rng.choice([p for p in ppl if p is not sp])
        if k < 0.3:
            call_given(d, other, rng); d.t(rng.choice([", 이 건 확인 부탁해요", " 오늘 회의 자료 공유 가능해요?", ", 고객 회신 끝났어요?"]))
        elif k < 0.55:
            name(d, cust, "auto", " 고객" + rng.choice(["님 건 ", " 건 ", "님 "]))
            d.t(rng.choice(["서류가 아직 안 왔어요", "오늘 오후에 다시 연락 주신대요", "처리 완료했습니다"]))
            if rng.random() < 0.4:
                v, h = phone(rng); d.t(" (연락처 "); d.e("phone_number", v); d.t(")"); h and d.hard.add(h)
        elif k < 0.7:
            d.t(decoy(rng) + " 확인했어요")
        elif k < 0.85:
            d.t(rng.choice(["네 알겠습니다", "잠시만요", "점심 이후에 볼게요", "감사합니다!"]))
        else:
            d.t("저는 "); call_given(d, other, rng, "obj"); d.t(" 같이 처리할게요")
        d.t("\n")
    return d, "메신저 대화"


def t_call(rng, dt):
    """통화 녹취 요약"""
    d, c, s = Doc(), Person(rng, "customer"), Person(rng, "staff")
    d.t(f"{dt} 통화 기록\n\n")
    d.t("상담사: 안녕하세요, " + BRAND + " " + rng.choice(STAFF_ROLES) + " ")
    name(d, s, rng.choice(["full", "full", "given"]), josa(s.given, "이에요") + ". 무엇을 도와드릴까요?\n")
    d.t("고객: " + rng.choice(["저 ", "네, 저는 ", "안녕하세요. "]))
    name(d, c, rng.choice(["full", "spaced", "full"]),
         rng.choice(["인데요", "입니다", ("이라고" if has_batchim(c.given) else "라고") + " 하는데요"]))
    d.t(". " + rng.choice(["카드가 결제가 안 돼서요", "대출 건으로 문의드려요", "주소를 바꾸고 싶어서요", "모르는 결제가 있어서요"]) + ".\n")
    d.t("상담사: "); name(d, c, "full", " 고객님, 본인 확인을 위해 휴대전화 번호 말씀해 주시겠어요?\n")
    v, h = phone(rng); d.t("고객: "); d.e("phone_number", v); d.t(rng.choice(["이요.", "입니다.", " 맞아요."]) + "\n"); h and d.hard.add(h)
    if rng.random() < 0.4:
        v, h = rrn(rng); d.t("상담사: 주민등록번호도 확인하겠습니다.\n고객: "); d.e("resident_registration_number", v); d.t("요.\n"); h and d.hard.add(h)
    d.t("상담사: 확인됐습니다. " + " ".join(a + "." for a in acts(rng, dt)) + "\n")
    if rng.random() < 0.5:
        fam = Person(rng, "customer"); fam.sur = c.sur
        d.t("고객: 그리고 서류는 제 동생 "); name(d, fam, "given", josa(fam.given, "이한테") if len(fam.given) > 1 else "에게"); d.t(" 맡길게요.\n")
    d.t("\n요약: "); name(d, c, rng.choice(["full", "spaced", "given"]), " 고객 ")
    d.t(rng.choice(["문의 응대 완료", "추가 서류 대기", "재연락 예정"]) + ". 응대 ")
    name(d, s, "full", f" {rng.choice(STAFF_ROLES)}.\n")
    return d, "통화 녹취 요약"


def t_mail(rng, dt):
    """업무 메일 / 고객 안내 메일"""
    d, to, me = Doc(), Person(rng), Person(rng, "staff")
    d.t(f"제목: [{BRAND}] {dt} 관련 안내\n\n")
    greet = rng.random()
    if greet < 0.4:
        name(d, to, "full", rng.choice([" 고객님, 안녕하세요.\n\n", "님 안녕하세요.\n\n", " 님께\n\n"]))
    elif greet < 0.7 and to.role == "staff":
        name(d, to, "full", f" {to.title}님,\n\n")
    else:
        call_given(d, to, rng); d.t(rng.choice([", 안녕하세요.\n\n", "\n\n"]))
    d.t(" ".join(a + "." for a in acts(rng, dt, 3)) + "\n")
    if rng.random() < 0.5:
        other = Person(rng, "staff")
        d.t("관련 문의는 "); name(d, other, "auto", f" {other.title}" + rng.choice(["에게 ", "님께 ", "한테 "])); d.t("하시면 됩니다.\n")
    if rng.random() < 0.4:
        v, h = account(rng); d.t(f"환급 계좌({rng.choice(BANKS)} "); d.e("account_number", v); d.t(")로 입금될 예정입니다.\n"); h and d.hard.add(h)
    if rng.random() < 0.3:
        v, h = address(rng); d.t(rng.choice(["안내문은 ", "재발급 카드는 ", "서류 원본은 "])); d.e("address", v)
        d.t(rng.choice(["로 발송했습니다.\n", " 주소로 보내드렸습니다.\n"])); h and d.hard.add(h)
    if rng.random() < 0.4:
        d.t(decoy(rng) + "\n")
    d.t("\n")
    name(d, me, rng.choice(["full", "full", "given"]), rng.choice([" 드림\n", " 올림\n", "\n"]))
    d.t(f"{me.team} | ")
    v, h = phone(rng, mobile=rng.random() < 0.5); d.e("phone_number", v); d.t(" | "); d.e("email", email(rng, me)); d.t("\n"); h and d.hard.add(h)
    return d, "이메일"


def t_table(rng, dt):
    """표가 섞인 보고서"""
    d = Doc()
    d.t(f"{dt} 현황 보고 ({rng.randint(2025, 2026)}-{rng.randint(1, 12):02d})\n\n")
    d.t("| 성명 | 소속 | 연락처 | 비고 |\n| --- | --- | --- | --- |\n")
    rows = [Person(rng) for _ in range(rng.randint(2, 5))]
    for p in rows:
        d.t("| "); name(d, p, rng.choice(["full", "full", "spaced"]), " | ")
        d.t((p.team if p.role == "staff" else rng.choice(["개인", "고객", f"{rng.choice(NATIVE)}{rng.choice(ORG_TAIL)}"])) + " | ")
        if rng.random() < 0.7:
            v, h = phone(rng); d.e("phone_number", v); h and d.hard.add(h)
        else:
            d.e("email", email(rng, p))
        d.t(" | " + rng.choice(["확인 완료", "서류 대기", "재연락", "-"]) + " |\n")
    d.t("\n주요 내용: ")
    p = rng.choice(rows)
    name(d, p, rng.choice(["full", "given", "spaced"]), rng.choice([josa(p.given, "은는") + " ", "에게 ", josa(p.given, "이랑") + " "]))
    d.t(rng.choice(["다음 주까지 서류를 보완하기로 함.", "추가 확인이 필요함.", "처리를 완료함."]) + "\n")
    if rng.random() < 0.5:
        d.t("참고: " + decoy(rng) + "\n")
    return d, "표가 섞인 보고서"


MEMO_ACT = ["카드 재발급을 요청함", "한도 상향을 문의함", "연락처 변경을 요청함", "결제 취소 건을 문의함",
            "서류 제출 방법을 문의함", "자동이체 계좌 변경을 요청함", "해외 결제 승인 건 확인을 요청함", "연체 이자를 문의함"]


def t_memo(rng, dt):
    """짧은 메모·상담 기록 (문장형)"""
    d, c, s = Doc(), Person(rng, "customer"), Person(rng, "staff")
    d.t(f"{dt} 메모\n")
    name(d, c, rng.choice(["full", "full", "spaced", "given"]), rng.choice([" 고객이 ", " 고객님이 ", " 님께서 ", josa(c.given, "이가") + " "]))
    d.t(rng.choice(["오전에 방문해 ", "전화로 ", "앱 문의로 ", ""]) + rng.choice(MEMO_ACT) + ". ")
    r = rng.random()
    if r < 0.35:
        v, h = phone(rng); d.t("연락처는 "); d.e("phone_number", v); d.t("로 확인. "); h and d.hard.add(h)
    elif r < 0.55:
        v, h = card(rng); d.t("대상 카드 "); d.e("card_number", v); d.t(". "); h and d.hard.add(h)
    elif r < 0.7:
        v, h = account(rng); d.t(f"{rng.choice(BANKS)} "); d.e("account_number", v); d.t(" 계좌로 처리. "); h and d.hard.add(h)
    elif r < 0.85:
        v, h = address(rng); d.t("방문 주소 "); d.e("address", v); d.t(" 확인. "); h and d.hard.add(h)
    d.t("담당 ")
    name(d, s, rng.choice(["full", "full", "given"]), rng.choice([f" {s.title}", f" {rng.choice(STAFF_ROLES)}", ""]))
    d.t(" " + rng.choice(["다시 연락하기로 함.", "서류 수령 후 처리 예정.", "처리 완료."]) + "\n")
    if rng.random() < 0.3:
        d.t(decoy(rng) + "\n")
    return d, "짧은 메모"


PROSE_A = ["{A} 고객이 {topic} 건으로 지점을 방문했다.", "{A} 고객으로부터 {topic} 관련 문의가 접수되었다.",
           "오늘 {A} 고객과 {topic} 상담을 진행했다.", "{A} 님이 {topic} 서류를 제출했다."]
PROSE_B = ["{a_ref} 서류가 부족하다고 하셔서 {B_full} {B_title}에게 확인을 요청했다.",
           "{a_ref} 이전에도 비슷한 문의를 한 적이 있다고 했다.",
           "통화 중 {a_ref} 가족과 상의 후 다시 연락하겠다고 했다.",
           "{B_full} {B_title}이 심사 기준을 설명했고, {a_ref} 동의했다."]
PROSE_C = ["이후 {b_call}, 고객 회신 받으면 공유 부탁드립니다.", "{b_ref} 내일 오전까지 결과를 정리하기로 했다.",
           "다음 연락은 {b_ref} 맡기로 했다.", "{a_call}, 추가 서류는 앱으로 올려 주시면 됩니다.",
           "서류 원본은 {b_obj} 전달했다.", "남은 확인 사항은 {b_obj} 넘겼다."]


def _fill(d, rng, tpl, A, B):
    """'{A}' 같은 자리에 이름을 넣으며 라벨 기록"""
    for piece in re.split(r"(\{\w+\})", tpl):
        if piece == "{A}":
            name(d, A, rng.choice(["full", "full", "spaced"]))
        elif piece == "{B_full}":
            name(d, B, "full")
        elif piece == "{B_title}":
            d.t(B.title)
        elif piece == "{a_ref}":
            call_given(d, A, rng, "subj")
        elif piece == "{b_ref}":
            call_given(d, B, rng, "subj")
        elif piece == "{b_obj}":
            call_given(d, B, rng, "obj")
        elif piece == "{a_call}":
            call_given(d, A, rng, "address")
        elif piece == "{b_call}":
            call_given(d, B, rng, "address")
        elif piece == "{topic}":
            d.t(rng.choice(["대출 연장", "카드 재발급", "명의 변경", "한도 조정", "보험금 청구", "자동이체 해지", "상속 조회"]))
        else:
            d.t(piece)


def t_prose(rng, dt):
    """문장형 상담·업무 기록: 처음엔 전체 이름, 이후엔 이름만 (가장 어려운 유형)"""
    d, A, B = Doc(), Person(rng, "customer"), Person(rng, "staff")
    d.t(f"{dt} 기록\n\n" if dt.endswith("상담") else f"{dt} 상담 기록\n\n")
    _fill(d, rng, rng.choice(PROSE_A), A, B); d.t(" ")
    _fill(d, rng, rng.choice(PROSE_B), A, B); d.t("\n")
    if rng.random() < 0.5:
        v, h = phone(rng); d.t("연락처: "); d.e("phone_number", v); d.t("\n"); h and d.hard.add(h)
    _fill(d, rng, rng.choice(PROSE_C), A, B); d.t("\n")
    if rng.random() < 0.35:
        d.t(decoy(rng) + "\n")
    return d, "문장형 기록"


def t_no_pii(rng, dt):
    """개인정보 없는 문서 (미끼만)"""
    d = Doc()
    d.t(f"[공지] {dt} 업무 안내\n\n")
    for _ in range(rng.randint(2, 4)):
        d.t(rng.choice(GENERIC_ACT) + ". " + decoy(rng) + ".\n")
    d.t(f"문의: {BRAND} 고객센터\n")
    d.hard.add("no_pii")
    return d, "공지·안내"


TEMPLATES = [(t_form, 16), (t_chat, 14), (t_call, 14), (t_mail, 15), (t_table, 10), (t_memo, 11), (t_prose, 12), (t_no_pii, 8)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--exclude-names-from", nargs="*", default=[],
                    help="이 파일들에 나오는 이름은 만들지 않음 (평가 세트 지정 → 이름 외우기로 점수가 부풀지 않게)")
    a = ap.parse_args()
    for f in a.exclude_names_from:
        for line in open(f, encoding="utf-8"):
            if line.strip():
                Person.EXCLUDE |= {e["text"].replace(" ", "") for e in json.loads(line)["entities"] if e["label"] == "person"}
    if Person.EXCLUDE:
        print(f"제외할 이름 {len(Person.EXCLUDE)}개")
    rng = random.Random(a.seed)
    fns, ws = zip(*TEMPLATES)
    with open(a.out, "w", encoding="utf-8") as f:
        for i in range(a.n):
            dt = rng.choice(DOC_TYPES)
            fn = rng.choices(fns, ws)[0]
            d, tmpl = fn(rng, dt)
            for e in d.ents:
                assert d.text[e["start"]:e["end"]] == e["text"]
            f.write(json.dumps({"id": f"SYN{i:05d}", "split": "synth_train", "doc_type": dt, "template": tmpl,
                                "set": "합성 · 학습용", "text": d.text, "entities": d.ents,
                                "hard_cases": sorted(d.hard)}, ensure_ascii=False) + "\n")
    print(f"저장: {a.out} ({a.n}문서)")


if __name__ == "__main__":
    main()
