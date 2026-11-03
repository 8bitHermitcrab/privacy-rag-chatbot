# 검색 평가 결과 — v3 (holdout 질의 88개)

결과 파일: `run_v3_recheck.jsonl` · 정답: `data/holdout_retrieval_queries_gold.jsonl`

Confuse@10 = 혼동 문서가 정의된 질의만, 상위 10개 중 혼동 문서가 차지한 비율의 평균 (낮을수록 좋음, 공식 채점과 같은 정의)

## 전체

| 모드 | n | Hit@1 | Hit@5 | R@5 | R@10 | MRR | Confuse@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v3 | 88 | 0.773 | 0.920 | 0.822 | 0.931 | 0.832 | 0.538 |

## 유형별

| 유형 | n | Hit@1 | Hit@5 | R@5 | R@10 | MRR | Confuse@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| address_exact | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | — |
| address_format_variant | 4 | 0.750 | 1.000 | 1.000 | 1.000 | 0.875 | — |
| address_partial | 4 | 0.000 | 0.000 | 0.000 | 0.062 | 0.047 | — |
| homonym_role | 2 | 0.500 | 1.000 | 0.375 | 0.750 | 0.625 | 0.400 |
| hybrid_homonym_semantic | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.617 |
| hybrid_paraphrase | 7 | 0.714 | 1.000 | 1.000 | 1.000 | 0.814 | 0.629 |
| hybrid_person_content | 6 | 0.833 | 0.833 | 0.712 | 0.900 | 0.852 | 0.567 |
| hybrid_status | 4 | 0.500 | 1.000 | 1.000 | 1.000 | 0.688 | 0.625 |
| hybrid_within_type | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.200 |
| name_topic | 8 | 0.625 | 0.875 | 0.812 | 1.000 | 0.707 | — |
| name_variant | 6 | 0.833 | 1.000 | 1.000 | 1.000 | 0.917 | — |
| person_all | 6 | 1.000 | 1.000 | 0.847 | 1.000 | 1.000 | — |
| role_agent | 6 | 0.333 | 1.000 | 0.759 | 1.000 | 0.597 | — |
| semantic_no_pii | 8 | 1.000 | 1.000 | 0.577 | 0.972 | 1.000 | — |
| value_exact | 7 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | — |
| value_format_variant | 6 | 0.833 | 0.833 | 0.759 | 0.833 | 0.833 | — |

## 질의별

질의 원문에는 개인정보가 있어 싣지 않음 (원문은 data/ 파일, 분석은 check_queries.py)

| qid | 유형 | 정답 수 | 질의 토큰 수 | 첫 정답 순위 | Hit@5 |
| --- | --- | --- | --- | --- | --- |
| Q001 | name_topic | 1 | 0 | 1 | O |
| Q002 | name_topic | 1 | 1 | 1 | O |
| Q003 | name_topic | 1 | 1 | 3 | O |
| Q004 | name_topic | 2 | 1 | 1 | O |
| Q005 | name_topic | 2 | 1 | 5 | O |
| Q006 | name_topic | 1 | 1 | 8 | X |
| Q007 | name_topic | 1 | 1 | 1 | O |
| Q008 | name_topic | 1 | 1 | 1 | O |
| Q009 | name_variant | 1 | 1 | 1 | O |
| Q010 | name_variant | 1 | 1 | 2 | O |
| Q011 | name_variant | 1 | 1 | 1 | O |
| Q012 | name_variant | 1 | 1 | 1 | O |
| Q013 | name_variant | 1 | 1 | 1 | O |
| Q014 | name_variant | 1 | 1 | 1 | O |
| Q015 | role_agent | 1 | 0 | 4 | O |
| Q016 | role_agent | 9 | 1 | 1 | O |
| Q017 | role_agent | 2 | 1 | 1 | O |
| Q018 | role_agent | 7 | 1 | 2 | O |
| Q019 | role_agent | 1 | 0 | 3 | O |
| Q020 | role_agent | 7 | 1 | 2 | O |
| Q021 | person_all | 8 | 1 | 1 | O |
| Q022 | person_all | 5 | 1 | 1 | O |
| Q023 | person_all | 8 | 1 | 1 | O |
| Q024 | person_all | 6 | 1 | 1 | O |
| Q025 | person_all | 4 | 1 | 1 | O |
| Q026 | person_all | 3 | 1 | 1 | O |
| Q027 | value_exact | 1 | 1 | 1 | O |
| Q028 | value_exact | 1 | 1 | 1 | O |
| Q029 | value_exact | 1 | 1 | 1 | O |
| Q030 | value_exact | 2 | 1 | 1 | O |
| Q031 | value_exact | 1 | 1 | 1 | O |
| Q032 | value_exact | 3 | 1 | 1 | O |
| Q033 | value_exact | 1 | 1 | 1 | O |
| Q034 | value_format_variant | 1 | 1 | 1 | O |
| Q035 | value_format_variant | 1 | 0 | 없음 | X |
| Q036 | value_format_variant | 1 | 1 | 1 | O |
| Q037 | value_format_variant | 1 | 1 | 1 | O |
| Q038 | value_format_variant | 9 | 1 | 1 | O |
| Q039 | value_format_variant | 1 | 1 | 1 | O |
| Q040 | address_exact | 1 | 1 | 1 | O |
| Q041 | address_exact | 1 | 1 | 1 | O |
| Q042 | address_exact | 1 | 2 | 1 | O |
| Q043 | address_exact | 1 | 1 | 1 | O |
| Q044 | address_format_variant | 1 | 1 | 2 | O |
| Q045 | address_format_variant | 1 | 2 | 1 | O |
| Q046 | address_format_variant | 2 | 1 | 1 | O |
| Q047 | address_format_variant | 1 | 1 | 1 | O |
| Q048 | address_partial | 8 | 0 | 없음 | X |
| Q049 | address_partial | 2 | 2 | 없음 | X |
| Q050 | address_partial | 2 | 1 | 16 | X |
| Q051 | address_partial | 4 | 0 | 8 | X |
| Q052 | homonym_role | 8 | 1 | 1 | O |
| Q053 | homonym_role | 8 | 1 | 4 | O |
| Q054 | semantic_no_pii | 9 | 0 | 1 | O |
| Q055 | semantic_no_pii | 9 | 0 | 1 | O |
| Q056 | semantic_no_pii | 9 | 0 | 1 | O |
| Q057 | semantic_no_pii | 8 | 0 | 1 | O |
| Q058 | semantic_no_pii | 7 | 0 | 1 | O |
| Q059 | semantic_no_pii | 9 | 0 | 1 | O |
| Q060 | semantic_no_pii | 10 | 0 | 1 | O |
| Q061 | semantic_no_pii | 9 | 0 | 1 | O |
| Q062 | hybrid_person_content | 2 | 1 | 1 | O |
| Q063 | hybrid_person_content | 9 | 1 | 1 | O |
| Q064 | hybrid_person_content | 2 | 1 | 1 | O |
| Q065 | hybrid_person_content | 5 | 1 | 9 | X |
| Q066 | hybrid_person_content | 7 | 1 | 1 | O |
| Q067 | hybrid_person_content | 1 | 1 | 1 | O |
| Q068 | hybrid_paraphrase | 1 | 1 | 1 | O |
| Q069 | hybrid_paraphrase | 1 | 1 | 1 | O |
| Q070 | hybrid_paraphrase | 1 | 1 | 2 | O |
| Q071 | hybrid_paraphrase | 1 | 1 | 5 | O |
| Q072 | hybrid_paraphrase | 1 | 1 | 1 | O |
| Q073 | hybrid_paraphrase | 2 | 1 | 1 | O |
| Q074 | hybrid_paraphrase | 2 | 1 | 1 | O |
| Q075 | hybrid_within_type | 1 | 1 | 1 | O |
| Q076 | hybrid_within_type | 1 | 2 | 1 | O |
| Q077 | hybrid_within_type | 1 | 1 | 1 | O |
| Q078 | hybrid_within_type | 1 | 1 | 1 | O |
| Q079 | hybrid_status | 1 | 1 | 4 | O |
| Q080 | hybrid_status | 3 | 1 | 1 | O |
| Q081 | hybrid_status | 1 | 1 | 2 | O |
| Q082 | hybrid_status | 2 | 1 | 1 | O |
| Q083 | hybrid_homonym_semantic | 1 | 1 | 1 | O |
| Q084 | hybrid_homonym_semantic | 1 | 1 | 1 | O |
| Q085 | hybrid_homonym_semantic | 2 | 1 | 1 | O |
| Q086 | hybrid_homonym_semantic | 1 | 1 | 1 | O |
| Q087 | hybrid_homonym_semantic | 2 | 1 | 1 | O |
| Q088 | hybrid_homonym_semantic | 1 | 1 | 1 | O |

Hit@5 실패 7개: Q006(name_topic), Q035(value_format_variant), Q048(address_partial), Q049(address_partial), Q050(address_partial), Q051(address_partial), Q065(hybrid_person_content)
