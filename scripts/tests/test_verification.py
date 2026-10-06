import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verification', Path(__file__).parents[1] / 'verify.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VerificationTests(unittest.TestCase):
    def test_manifest_detects_edit_addition_and_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'backend/app').mkdir(parents=True)
            file=root/'backend/app/main.py'
            file.write_text('original')
            original=module.source_hashes(root)
            file.write_text('changed')
            self.assertNotEqual(original,module.source_hashes(root))
            file.write_text('original')
            (root/'backend/app/new.py').write_text('new')
            self.assertNotEqual(original,module.source_hashes(root))
            file.unlink()
            self.assertNotEqual(original,module.source_hashes(root))

    def test_secrets_and_generated_directories_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for rel in ('backend/.env','backend/storage/raw.jpg','backend/data/app.db',
                        'admin-web/node_modules/package/index.js','admin-web/dist/index.html'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('private')
            self.assertEqual({},module.source_hashes(root))

if __name__=='__main__':unittest.main()
