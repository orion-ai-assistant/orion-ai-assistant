"""Live TLS boundary tests plus deterministic process/DB provenance failures."""
from contextlib import ExitStack
import http.server
import json
import os
from pathlib import Path
import ssl
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_router_installation
from services.shared.router_installation import inspect_router_folder
from services.shared.router_verification import _local_identity, verify_running_router


class RouterVerificationTests(unittest.TestCase):
    def setUp(self):
        # Share only fixture construction; don't inherit its test methods.
        self.fixture = test_router_installation.RouterInstallationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.roots[0]
        self.candidate = inspect_router_folder(self.root)
        self.certificate = self.root / 'persistent/tls/certificate.pem'
        self.payload = dict(v=1, id=self.candidate.identity, fp=self.candidate.spki)
        test = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                test.requested_path = self.path
                self.send_response(test.http_status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(test.payload).encode())
            def log_message(self, *args):
                pass
        self.http_status = 200
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.root / 'persistent/tls/private-key.pem')
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.database = SimpleNamespace(ctl=self.root / 'runtime/pg_ctl.exe', data=self.root / '.pgdata-prod',
                                       port=15432, user='fixture', database='orion_router',
                                       ready=Mock(return_value=True), sql=Mock(return_value='true'))
        self.record = dict(status='ready', supervisor={'pid': 100},
                           children={'backend': dict(process={'pid': 101}, entrypoint=str(self.root / 'main.py'))},
                           database={field: getattr(self.database, field) for field in ('ctl', 'data', 'port', 'user', 'database')})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.read = self.stack.enter_context(patch('services.shared.router_verification.Runtime.read', return_value=self.record))
        self.living = self.stack.enter_context(patch('services.shared.router_verification.living', return_value=True))
        self.children = self.stack.enter_context(patch('services.shared.router_verification.child_running', return_value=True))
        self.listener = self.stack.enter_context(patch('services.shared.router_verification.owns_listener', return_value=True))
        self.reference = self.stack.enter_context(patch('services.shared.router_verification.ProcessReference'))
        self.reference.return_value.__enter__.return_value.process.cmdline.return_value = ['python', str(self.root / 'main.py')]

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def verify(self, **kwargs):
        return verify_running_router(self.candidate, port=self.port, certificate_path=self.certificate,
                                     database=self.database, **kwargs)

    def test_real_tls_pinned_identity_and_readonly_schema(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}), \
                patch('subprocess.Popen', side_effect=AssertionError('must not start code')), \
                patch('services.shared.router_verification.Runtime.__enter__', side_effect=AssertionError('must not write runtime')):
            evidence = self.verify()
        self.assertTrue(evidence.process_verified)
        self.assertTrue(evidence.tls_verified)
        self.assertTrue(evidence.database_verified)
        self.assertTrue(evidence.api_compatible)
        self.assertEqual(self.requested_path, '/api/v1/tls/identity')
        self.assertTrue(self.database.sql.call_args.args[0].startswith('SELECT '))
        self.assertNotIn(';', self.database.sql.call_args.args[0])
        self.assertNotIn('CREATE', self.database.sql.call_args.args[0])

    def test_actual_inspector_adapter_returns_ready_with_existing_pins(self):
        candidate = inspect_router_folder(self.root, expected_identity=self.candidate.identity,
            expected_spki=self.candidate.spki,
            live_verifier=lambda item: verify_running_router(item, port=self.port,
                certificate_path=self.certificate, database=self.database))
        self.assertEqual(candidate.status, 'ready')

    def test_no_state_or_no_live_owner_never_contacts_tls_or_database(self):
        for record, alive in ((None, True), (self.record, False)):
            self.read.return_value, self.living.return_value = record, alive
            self.assertIsNone(self.verify())
        self.assertFalse(hasattr(self, 'requested_path'))
        self.database.ready.assert_not_called()

    def test_foreign_listener_and_stopped_child_are_not_ready(self):
        self.listener.return_value = False
        self.assertIsNone(self.verify())
        self.listener.return_value = True
        self.children.return_value = False
        self.assertIsNone(self.verify())
        self.database.ready.assert_not_called()

    def test_forged_backend_label_and_wrong_command_fail(self):
        self.record['children']['backend']['entrypoint'] = str(self.root / 'other.py')
        with self.assertRaisesRegex(ValueError, 'entry point'):
            self.verify()
        self.record['children']['backend']['entrypoint'] = str(self.root / 'main.py')
        self.reference.return_value.__enter__.return_value.process.cmdline.return_value = ['python', 'foreign.py']
        with self.assertRaisesRegex(ValueError, 'process command'):
            self.verify()

    def test_wrong_database_provenance_blocks_before_database_or_tls_calls(self):
        self.record['database']['data'] = self.root / 'foreign-db'
        with self.assertRaisesRegex(ValueError, 'database differs'):
            self.verify()
        self.assertFalse(hasattr(self, 'requested_path'))
        self.database.ready.assert_not_called()

    def test_control_database_can_differ_but_application_schema_must_match(self):
        self.record['database']['database'] = 'postgres'
        self.database.database = 'orion_router'
        self.assertTrue(self.verify().database_verified)
        self.database.sql.return_value = 'false'
        self.assertFalse(self.verify().database_verified)

    def test_other_cluster_port_or_user_still_rejected(self):
        for field, value in (('port', 19999), ('user', 'foreign-user')):
            with self.subTest(field=field):
                original = self.record['database'][field]
                self.record['database'][field] = value
                with self.assertRaisesRegex(ValueError, 'database differs'):
                    self.verify()
                self.record['database'][field] = original
        self.database.ready.assert_not_called()

    def test_uuid_mismatch_and_api_version_are_separate(self):
        self.payload['id'] = self.fixture.lab.installations[1]['id']
        with self.assertRaisesRegex(ValueError, 'UUID/TLS'):
            self.verify()
        self.payload['id'] = self.candidate.identity
        self.payload['v'] = 2
        self.assertFalse(self.verify().api_compatible)

    def test_certificate_mismatch_rejected_and_redirect_not_followed(self):
        other_certificate = self.fixture.roots[1] / 'persistent/tls/certificate.pem'
        with self.assertRaisesRegex(ValueError, 'certificate changed'):
            _local_identity(self.port, other_certificate, self.candidate.spki)
        other = inspect_router_folder(self.fixture.roots[1])
        with self.assertRaisesRegex(ValueError, 'TLS certificate verification failed'):
            _local_identity(self.port, other_certificate, other.spki)
        self.http_status = 302
        with self.assertRaisesRegex(ValueError, 'API unavailable'):
            self.verify()

    def test_database_or_schema_failure_never_produces_ready(self):
        self.database.ready.return_value = False
        self.assertFalse(self.verify().database_verified)
        self.database.sql.assert_not_called()
        self.database.ready.return_value = True
        self.database.sql.return_value = 'false'
        self.assertFalse(self.verify().database_verified)

    def test_post_probe_process_change_invalidates_evidence(self):
        self.listener.side_effect = [True, False]
        self.assertFalse(self.verify().process_verified)


if __name__ == '__main__':
    unittest.main()
