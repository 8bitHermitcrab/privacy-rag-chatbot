"""검색 역할 단서 재정렬 테스트 (DB·임베딩 없음)."""
import unittest
from starter.search import query_roles, _rerank


class RoleCueTests(unittest.TestCase):
    def test_query_cues(self):
        self.assertEqual(query_roles('고객 <PERSON_a> 건만'), {'<PERSON_a>': 'CUSTOMER'})
        self.assertEqual(query_roles('<PERSON_a> 부장 회사 카드'), {'<PERSON_a>': 'STAFF'})
        self.assertEqual(query_roles('<PERSON_a> 대표님 가맹점'), {'<PERSON_a>': 'REP'})
        # '님'은 고객·직원 모두에게 쓰므로 단서가 아님
        self.assertEqual(query_roles('<PERSON_a> 님이 맡았던 보고서'), {})

    def test_only_clear_mismatch_moves_back(self):
        rows = [{'doc_id': 'A', 'masked_text': '고객센터 <PERSON_a> 대리 안내'},
                {'doc_id': 'B', 'masked_text': '<PERSON_a> 드림'},
                {'doc_id': 'C', 'masked_text': 'VOC | <PERSON_a>님 | 결제'}]
        ranked = [r['doc_id'] for r in _rerank(rows, {'<PERSON_a>': 'CUSTOMER'})]
        self.assertEqual(ranked, ['B', 'C', 'A'])   # 역할 불명(B)은 그대로, 직원으로만 나온 A만 뒤로
        self.assertEqual([r['doc_id'] for r in _rerank(rows, {})], ['A', 'B', 'C'])

    def test_table_department_is_staff(self):
        rows = [{'doc_id': 'A', 'masked_text': '사용자: 리스크관리팀 / 부장 / <PERSON_a>'}]
        self.assertEqual([r['doc_id'] for r in _rerank(rows, {'<PERSON_a>': 'CUSTOMER'})], ['A'])


if __name__ == '__main__':
    unittest.main()
