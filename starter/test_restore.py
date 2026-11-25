"""채팅 복원 표시값 규칙 (DB 없이 검사)."""
import unittest

from starter import restore as r


class RestoreTest(unittest.TestCase):
    def test_partial_values(self):
        self.assertEqual(r.partial('person', '김현호', None), '김*호')
        self.assertEqual(r.partial('person', '이주', None), '이*')
        self.assertEqual(r.partial('phone', '01012345678', None), '010-****-5678')
        self.assertEqual(r.partial('phone', '0212345678', None), '02-****-5678')
        self.assertEqual(r.partial('email', 'hong.gil@example.com', None), 'ho***@example.com')
        self.assertEqual(r.partial('address', '서울강남구테헤란로123', '서울 강남구 테헤란로 123'), '서울 강남구 ***')
        self.assertEqual(r.partial('rrn', '9001011234567', None), '900101-*******')
        self.assertEqual(r.partial('account', '110123456789', None), '********6789')
        self.assertEqual(r.partial('card', '1234567812345678', None), '****-****-****-5678')

    def test_only_allowed_kinds_are_restored(self):
        text = '<PERSON_aaaa1111> <PHONE_bbbb2222> <EMAIL_cccc3333> <CARD_dddd4444> <PERSON_aaaa1111>'
        tokens = r.tokens_in(text)
        self.assertEqual(len(tokens), 4)
        asked = []
        def fetch(ts):
            asked.extend(ts)
            return {'<PERSON_aaaa1111>': ('김현호', ['김현호']), '<PHONE_bbbb2222>': ('01012345678', ['010-1234-5678']),
                    '<CARD_dddd4444>': ('1234567812345678', ['1234-5678-1234-5678'])}
        out = r.restore_map(tokens, {'person': 'FULL', 'phone': 'FULL', 'email': 'NONE', 'card': 'FULL'}, fetch)
        self.assertNotIn('<EMAIL_cccc3333>', asked)            # 허용 안 된 종류는 조회도 하지 않음
        self.assertEqual(out['<PERSON_aaaa1111>']['value'], '김현호')
        self.assertEqual(out['<PHONE_bbbb2222>']['value'], '010-1234-5678')
        self.assertEqual(out['<CARD_dddd4444>'], {'kind': 'card', 'level': 'PARTIAL', 'value': '****-****-****-5678'})
        self.assertEqual(r.summary(out), {'person': 'FULL', 'phone': 'FULL', 'card': 'PARTIAL'})

    def test_nothing_allowed_means_no_lookup(self):
        def fetch(ts):
            raise AssertionError('조회하면 안 됨')
        self.assertEqual(r.restore_map(['<PERSON_aaaa1111>'], {'person': 'NONE'}, fetch), {})
        self.assertEqual(r.restore_map(['<UNKNOWN_aaaa1111>'], {'person': 'FULL'}, fetch), {})


if __name__ == '__main__':
    unittest.main()
