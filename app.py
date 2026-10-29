"""가명처리 기반 검색 챗봇 데모 (실전 2 기본형)

    streamlit run app.py

- 질의도 문서와 같은 탐지기·HMAC 키로 가명처리 → LLM·임베딩 API에는 가명처리된 텍스트만 전송
- 답변은 토큰이 들어간 그대로 표시 (실명 복원·권한·출력 재검사는 종합 단계)
- 검색 방식은 .env 의 SEARCH_MODE (기본 v3)
"""
import os

os.environ.setdefault("SEARCH_MODE", "v3")

import streamlit as st

from starter import config
from starter.chat import answer
from starter.search import SEARCH_MODE

st.set_page_config(page_title="가명처리 검색 챗봇", layout="wide")
st.title("가명처리 기반 검색 챗봇")
st.caption(f"검색 {SEARCH_MODE} · 임베딩 {config.EMBED_MODEL} · LLM {config.LLM_MODEL} — "
           "API에는 가명처리된 텍스트만 전송되며, 답변의 토큰은 복원하지 않습니다.")

with st.sidebar:
    k = st.slider("참고 문서 수", 1, 10, 3)
    st.markdown("**예시 질의**")
    examples = ["연락처가 010-0791-2924인 건 찾아줘",
                "보이스피싱 피해 접수 기록 알려줘",
                "윤 선영 고객 대출 상담 내역 알려줘"]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.pending = ex

if "history" not in st.session_state:
    st.session_state.history = []

for turn in st.session_state.history:
    st.chat_message("user").write(turn["query"])
    st.chat_message("assistant").write(turn["answer"])

q = st.chat_input("질문을 입력하세요") or st.session_state.pop("pending", None)
if q:
    st.chat_message("user").write(q)
    with st.chat_message("assistant"):
        with st.spinner("검색·답변 생성 중…"):
            res = answer(q, k)
        text = res["answer"] or "(LLM_API_KEY 없음 — 검색 결과만 표시)"
        st.write(text)
        with st.expander("처리 과정 보기"):
            st.markdown(f"**가명처리된 질의:** `{res['masked_query']}`")
            st.markdown(f"**질의 토큰:** {', '.join(f'`{t}`' for t in res['query_tokens']) or '없음 (의미 검색만)'}")
            for i, d in enumerate(res["docs"], 1):
                hit = "🔑 토큰 일치" if set(d["tokens"]) & set(res["query_tokens"]) else ""
                st.markdown(f"**{i}. {d['doc_id']}** · score {d['score']:.3f} {hit}")
                st.code(d["masked_text"][:500], language=None)
    st.session_state.history.append({"query": q, "answer": text})
