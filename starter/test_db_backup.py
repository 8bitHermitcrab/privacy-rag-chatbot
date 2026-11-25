"""DB 백업 파일 이름·보관 개수 정리 (Docker 없이 검사). 실제 백업·복원은 python -m starter.db_backup verify 로 확인."""
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from starter import db_backup as b


class BackupTest(unittest.TestCase):
    def test_prune_keeps_newest_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            names = [f"pii_rag_202611{d:02d}_120000.dump" for d in range(1, 6)]
            for n in names + ["pii_rag_20261106_120000.partial", "memo.txt"]:
                (folder / n).write_bytes(b"x")
            self.assertEqual(b.prune(folder, keep=2), names[:3])
            self.assertEqual([p.name for p in b.backups(folder)], names[3:])
            self.assertTrue((folder / "memo.txt").exists() and (folder / "pii_rag_20261106_120000.partial").exists())

    def test_relative_backup_dir_refused(self):
        with self.assertRaises(b.BackupError):
            b.backup_dir({"DB_BACKUP_DIR": "backups"})

    def test_verify_rejects_odd_names(self):
        self.assertFalse(b.NAME.fullmatch("../pii_rag_20261101_120000.dump"))
        self.assertTrue(b.NAME.fullmatch("pii_rag_20261101_120000.dump"))

    def test_copy_command(self):
        cmd = b.copy_command(PurePosixPath("/home/rag/.local/share/rag-server-admin/db_backups"), "backup@10.0.0.12:/var/backups/pii_rag/")
        self.assertEqual(cmd[0], "rsync")
        self.assertIn("--delete", cmd)
        self.assertIn("--include=pii_rag_*.dump", cmd)
        self.assertEqual(cmd[-2:], ["/home/rag/.local/share/rag-server-admin/db_backups/", "backup@10.0.0.12:/var/backups/pii_rag/"])
        self.assertIn("BatchMode=yes", " ".join(cmd))
        for bad in ("10.0.0.12:/var/backups", "backup@10.0.0.12:relative", "backup@host:/x; rm -rf /", ""):
            with self.assertRaises(b.BackupError):
                b.copy_command(Path("/tmp"), bad)


if __name__ == "__main__":
    unittest.main()
