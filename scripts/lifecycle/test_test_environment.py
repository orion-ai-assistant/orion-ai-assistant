import os
import ssl
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from test_environment import snapshot, temporary_lab


class LifecycleEnvironmentTests(unittest.TestCase):
    def test_roots_and_environment_are_temporary_and_restored(self):
        previous = os.environ.get('LOCALAPPDATA')
        with temporary_lab() as lab:
            root = lab.root
            self.assertEqual(len(lab.installations), 2)
            self.assertNotEqual(lab.installations[0]['id'], lab.installations[1]['id'])
            for item in lab.installations:
                for key in ('hub', 'router', 'profile'):
                    self.assertTrue(item[key].is_relative_to(root))
            self.assertEqual(os.environ['LOCALAPPDATA'], str(lab.installations[0]['profile']))
        self.assertFalse(root.exists())
        self.assertEqual(os.environ.get('LOCALAPPDATA'), previous)

    def test_legacy_locator_is_read_by_existing_hub_consumer(self):
        from services.shared.router_installation import registered_router_root
        with temporary_lab() as lab:
            before = snapshot(lab.root)
            for index, install in enumerate(lab.installations):
                with patch.dict(os.environ, lab.environment(index)):
                    self.assertEqual(registered_router_root(), str(install['router']))
            self.assertEqual(snapshot(lab.root), before)

    def test_mutating_one_installation_preserves_other_and_foreign(self):
        with temporary_lab() as lab:
            other, foreign = snapshot(lab.installations[1]['root']), snapshot(lab.foreign)
            target = lab.installations[0]['hub'] / 'services/llm/llama-cpp/models/sample.gguf'
            before = snapshot(lab.installations[0]['root'])
            target.write_bytes(b'controlled fixture mutation')
            self.assertNotEqual(snapshot(lab.installations[0]['root']), before)
            self.assertEqual(snapshot(lab.installations[1]['root']), other)
            self.assertEqual(snapshot(lab.foreign), foreign)

    def test_tls_samples_have_matching_keys_and_distinct_identities(self):
        with temporary_lab() as lab:
            certs = []
            for item in lab.installations:
                tls = item['router'] / 'persistent/tls'
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(tls / 'certificate.pem', tls / 'private-key.pem')
                certs.append((tls / 'certificate.pem').read_bytes())
            self.assertNotEqual(*certs)

    def test_process_descriptors_cover_foreign_collision_and_pid_reuse(self):
        with temporary_lab() as lab:
            first, second, foreign = lab.processes.values()
            self.assertNotEqual(first.installation_id, second.installation_id)
            self.assertEqual(first.port, foreign.port)
            reused = lab.reused_pid(first.pid)
            self.assertEqual(first.pid, reused.pid)
            self.assertNotEqual(first.started, reused.started)
            self.assertIsNone(reused.installation_id)

    def test_snapshot_detects_identity_removal_and_file_addition(self):
        with temporary_lab() as lab:
            root = lab.installations[0]['router']
            before = snapshot(root)
            (root / 'persistent/mdns-id').unlink()
            (root / 'unexpected').write_text('addition')
            after = snapshot(root)
            self.assertNotEqual(before, after)
            self.assertNotIn('persistent/mdns-id', after)
            self.assertIn('unexpected', after)

    def test_sample_payloads_and_both_tls_pairs_are_readable_without_mutation(self):
        with temporary_lab() as lab:
            before = snapshot(lab.root)
            for item in lab.installations:
                database = item['hub'] / 'services/hub/data/synthetic.sqlite3'
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                    self.assertEqual({row[0] for row in db.execute('select kind from samples')},
                                     {'account', 'settings', 'history', 'draft'})
                security = item['hub'] / 'files/security'
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(security / 'hub-cert.pem', security / 'hub-key.pem')
            self.assertEqual(snapshot(lab.root), before)


if __name__ == '__main__':
    unittest.main()
