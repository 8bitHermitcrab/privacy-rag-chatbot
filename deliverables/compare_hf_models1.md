W1106 17:19:13.444000 66262 .venv/lib/python3.12/site-packages/torch/distributed/elastic/multiprocessing/redirects.py:29] NOTE: Redirects are currently not supported in Windows or MacOs.
| 설정 | ms/문서 | 마스킹 재현율 | 노출 | 이름 경계·라벨 | 오탐 | person 노출 | phone_number 노출 | email 노출 | address 노출 | resident_registration_number 노출 | account_number 노출 | card_number 노출 | organization 노출 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 현재 파이프라인 (KLUE-small+규칙+보강) | 89.5 | 0.999 | 1 | 4 | 18 | 1/560 | 0/147 | 0/43 | 0/52 | 0/43 | 0/41 | 0/26 | 0/0 |
| koelectra-pii 단독 (14M) | 9.4 | 0.985 | 14 | 20 | 182 | 3/560 | 5/147 | 0/43 | 0/52 | 0/43 | 6/41 | 0/26 | 0/0 |
| 현재 + koelectra-pii | 98.9 | 1.000 | 0 | 4 | 190 | 0/560 | 0/147 | 0/43 | 0/52 | 0/43 | 0/41 | 0/26 | 0/0 |
| e5-pii 단독 (277M) | 169.7 | 0.971 | 26 | 5 | 54 | 24/560 | 2/147 | 0/43 | 0/52 | 0/43 | 0/41 | 0/26 | 0/0 |
| 현재 + e5-pii | 259.3 | 0.999 | 1 | 4 | 59 | 1/560 | 0/147 | 0/43 | 0/52 | 0/43 | 0/41 | 0/26 | 0/0 |
| openmed-pii 단독 (396M) | 208.7 | 0.704 | 270 | 153 | 640 | 206/560 | 32/147 | 0/43 | 0/52 | 20/43 | 5/41 | 7/26 | 0/0 |
| 현재 + openmed-pii | 298.3 | 0.999 | 1 | 4 | 596 | 1/560 | 0/147 | 0/43 | 0/52 | 0/43 | 0/41 | 0/26 | 0/0 |

이름 미탐 유형
- 현재 파이프라인: {'성 없이 이름만': 1}
- koelectra-pii: {'띄어 쓴 외자': 1, '외자': 2}
- koelectra-pii 합집합: {}
- e5-pii: {'성 없이 이름만': 12, '외자': 4, '그 외': 2, '띄어 쓴 외자': 1, '띄어 쓴 이름': 3, '세 글자': 2}
- e5-pii 합집합: {'성 없이 이름만': 1}
- openmed-pii: {'성 없이 이름만': 29, '세 글자': 115, '외자': 22, '띄어 쓴 이름': 26, '그 외': 13, '띄어 쓴 외자': 1}
- openmed-pii 합집합: {'성 없이 이름만': 1}
※ openmed-pii: 대응 안 된 라벨 {'JOBDEPARTMENT': 792, 'PREFIX': 137, 'DATE': 203, 'MASKEDNUMBER': 312, 'ACCOUNTNAME': 138, 'JOBTITLE': 220, 'DATEOFBIRTH': 55, 'AMOUNT': 92, 'CURRENCY': 3, 'AGE': 15, 'TIME': 18, 'CURRENCYSYMBOL': 23, 'OCCUPATION': 2, 'USERNAME': 2, 'VRM': 2, 'GENDER': 3} → MODELS 대응표에 추가 후 다시 실행
