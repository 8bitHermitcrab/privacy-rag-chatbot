"""데이터 버전(키 교체) 이름 규칙·SQL 변환·키 지문 (DB 없이 검사). DB 생성 검사는 test_schema_bootstrap 에 있습니다."""
import unittest
from unittest.mock import patch

from starter import config, db, data_version as dv


class DataVersionTest(unittest.TestCase):
    def tearDown(self):
        config.use_data_version('v1')

    def test_schema_names(self):
        self.assertEqual(config.data_schemas('v1'), ('rag', 'vault'))
        self.assertEqual(config.data_schemas('v2'), ('rag_v2', 'vault_v2'))
        for bad in ('v0', 'V2', 'v2;drop', '', 'v1000', None):
            with self.assertRaises(ValueError):
                config.data_schemas(bad)

    def test_sql_rewrite_only_data_tables(self):
        q = ("SELECT * FROM rag.chunks k JOIN rag.document_info d ON d.document_id=k.doc_id; "
             "SELECT vault.token_map.originals FROM vault.token_map, vault.person p, vault.person_mention m, "
             "auth.restore_log, vault.set_person_updated_at(); SELECT to_regclass('vault.person_mention')")
        config.use_data_version('v1')
        self.assertEqual(db.data_sql(q), q)
        config.use_data_version('v2')
        out = db.data_sql(q)
        for name in ('rag_v2.chunks', 'vault_v2.token_map.originals', 'vault_v2.person p', 'vault_v2.person_mention m',
                     "to_regclass('vault_v2.person_mention')"):
            self.assertIn(name, out)
        for name in ('rag.document_info', 'auth.restore_log', 'vault.set_person_updated_at()'):
            self.assertIn(name, out)
        self.assertNotIn('rag.chunks', out)
        self.assertEqual(db.data_sql(q, 'v3').count('rag_v3.chunks'), 1)

    def test_fingerprint(self):
        a, b = dv.fingerprint('key-one'), dv.fingerprint('key-two')
        self.assertRegex(a, r'^[0-9a-f]{16}$')
        self.assertNotEqual(a, b)
        self.assertEqual(a, dv.fingerprint('key-one'))
        self.assertNotIn('key-one', a)
        with self.assertRaises(dv.DataVersionError):
            dv.fingerprint('')

    def test_key_name_rules(self):
        with patch.dict('os.environ', {'NEW_HMAC_KEY': 'abc'}):
            self.assertEqual(dv.key_from('NEW_HMAC_KEY'), 'abc')
        for bad in ('', 'NEW-KEY', 'A;B'):
            with self.assertRaises(dv.DataVersionError):
                dv.key_from(bad)


if __name__ == '__main__':
    unittest.main()
