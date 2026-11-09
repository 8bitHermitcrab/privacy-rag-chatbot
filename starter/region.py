"""지역 토큰 (A 설계): 주소에서 '시도 + 시군구'만 뽑아 별도 토큰을 만듦 → 부분 주소 질의('서울시 구로구 고객')로도 검색

    .env:  SEARCH_REGION=1   (적재·검색 모두 이 값을 봄. 켠 뒤에는 다시 적재)

- 지역 토큰은 청크의 tokens 배열에만 넣고 가명 텍스트에는 넣지 않음 → LLM·화면에는 드러나지 않음
- 단위는 시도 + 첫 시군구 (예: '서울특별시 구로구 디지털로 300' → '서울구로구', '경기 수원시 영통구 …' → '경기수원시')
- 시도가 없으면 만들지 않음 ('중구'·'구로구'만 있는 경우: 여러 시에 같은 이름이 있을 수 있음)
- 시도 표기는 서울특별시·서울시·서울 → 서울 처럼 통일 (탐지 규칙·주소 토큰 정규화와는 별개)
- 세종은 시군구가 없어 '세종' 하나로 만듦
위험: 같은 시군구 고객끼리 같은 지역 토큰을 가짐 (DB 안에서 연결 가능). 상세 주소는 연결되지 않음.
"""
import hashlib
import hmac
import os
import re

from . import config  # noqa: F401  (.env 를 먼저 읽기)

REGION = os.getenv("SEARCH_REGION", "0") == "1"

# 표기 → 통일 이름 (긴 표기를 먼저 찾도록 정규식에서 길이 순으로 정렬)
_SIDO = {
    "서울": ("서울특별시", "서울시", "서울"),
    "부산": ("부산광역시", "부산시", "부산"),
    "대구": ("대구광역시", "대구시", "대구"),
    "인천": ("인천광역시", "인천시", "인천"),
    "광주": ("광주광역시", "광주"),          # '광주시'는 경기도에도 있어 넣지 않음 (아래 _GWANGJU 참고)
    "대전": ("대전광역시", "대전시", "대전"),
    "울산": ("울산광역시", "울산시", "울산"),
    "세종": ("세종특별자치시", "세종시", "세종"),
    "경기": ("경기도", "경기"),
    "강원": ("강원특별자치도", "강원도", "강원"),
    "충북": ("충청북도", "충북"),
    "충남": ("충청남도", "충남"),
    "전북": ("전북특별자치도", "전라북도", "전북"),
    "전남": ("전라남도", "전남"),
    "경북": ("경상북도", "경북"),
    "경남": ("경상남도", "경남"),
    "제주": ("제주특별자치도", "제주도", "제주"),
}
_CANON = {alias: short for short, aliases in _SIDO.items() for alias in aliases}
_ALT = "|".join(sorted(_CANON, key=len, reverse=True))
# 시도 + (띄어쓰기 있어도 없어도) + 시군구 1~3글자 + 시/군/구. 시군구 뒤에 조사가 붙어도 됨 ('구로구에')
_PAT = re.compile(rf"(?<![가-힣])({_ALT})\s*([가-힣]{{1,3}}?(?:시|군|구))?")
# '광주시 북구'처럼 뒤에 구가 오면 광주광역시로 봄 (경기 광주시에는 구가 없음)
_GWANGJU = re.compile(r"(?<![가-힣])광주시\s*([가-힣]{1,2}?구)")


def region_keys(text):
    """텍스트 안의 지역 키 목록 (중복 없이, 나온 순서대로). 예: ['서울구로구']"""
    found = []
    for m in _PAT.finditer(text):
        sido, sgg = _CANON[m.group(1)], m.group(2)
        if sgg and (sgg in _CANON or (m.group(1) == "광주" and sgg.startswith("시"))):
            sgg = None    # '서울 대구 지점'처럼 시도 이름이 시군구 자리에 온 경우, '광주시북구'(→ 아래 _GWANGJU)
        key = sido + sgg if sgg else ("세종" if sido == "세종" else None)
        if key and key not in found:
            found.append(key)
    for m in _GWANGJU.finditer(text):
        key = "광주" + m.group(1)
        if key not in found:
            found.append(key)
    return found


def region_token(key):
    from .pii_pipeline import _key
    h = hmac.new(_key(), f"region:{key}".encode(), hashlib.sha256).hexdigest()[:8]
    return f"<REGION_{h}>"


def region_tokens(text):
    return [region_token(k) for k in region_keys(text)]
