"""A watcher that reads a job's members must survive a short-lived member (FFmpeg, conhost) that is
read differently between two looks; stop and port ownership still fail closed."""
import os
import unittest
from unittest.mock import MagicMock, Mock, patch

import psutil

from services.shared import process_lifecycle


@unittest.skipUnless(os.name == 'nt', 'Windows job objects')
class JobRecordsTests(unittest.TestCase):
    def job(self, captures):
        from services.shared import windows_job
        job = windows_job.Job.__new__(windows_job.Job)
        job.pids = lambda: list(captures)
        job.contains = lambda handle: True

        def capture(pid):
            value = captures[pid]
            if isinstance(value, Exception):
                raise value
            return value

        reference = MagicMock()
        reference.__enter__.return_value = reference
        patches = [patch.object(windows_job, 'ProcessReference', Mock(return_value=reference, capture=capture)),
                   patch.object(windows_job.psutil, 'Process', return_value=Mock(username=lambda: 'me'))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return job

    def members(self):
        return {1: {'pid': 1, 'user': 'me'},
                2: process_lifecycle.LifecycleError('Process identity mismatch; no signal sent'),
                3: psutil.AccessDenied(3),
                4: psutil.NoSuchProcess(4)}

    def test_a_watching_look_skips_members_it_cannot_read(self):
        job = self.job(self.members())
        self.assertEqual(job.records(strict=False), [{'pid': 1, 'user': 'me'}])

    def test_a_strict_look_fails_on_an_identity_mismatch(self):
        job = self.job({1: {'pid': 1, 'user': 'me'},
                        2: process_lifecycle.LifecycleError('Process identity mismatch; no signal sent')})
        with self.assertRaises(process_lifecycle.LifecycleError):
            job.records()

    def test_a_strict_look_fails_on_an_unreadable_member(self):
        job = self.job({3: psutil.AccessDenied(3)})
        with self.assertRaises(psutil.Error):
            job.records(strict=True)

    def test_another_users_member_is_never_skipped_by_a_watching_look_as_if_ours(self):
        job = self.job({1: {'pid': 1, 'user': 'someone-else'}})
        # The mismatch is raised inside the try: a watcher leaves it out, and never lists it as ours.
        self.assertEqual(job.records(strict=False), [])
        with self.assertRaises(process_lifecycle.LifecycleError):
            job.records(strict=True)


class RefreshChildrenShortLivedTests(unittest.TestCase):
    def runtime(self, children):
        runtime = process_lifecycle.Runtime.__new__(process_lifecycle.Runtime)
        runtime.children = children
        runtime.save = Mock()
        return runtime

    def test_a_job_member_that_cannot_be_read_does_not_stop_the_watch(self):
        job = MagicMock()
        job.__enter__.return_value = job
        job.records.return_value = [{'pid': 2, 'started': 1.0}]
        adapter = Mock(Job=Mock(return_value=job))
        runtime = self.runtime({'router': {'job': {'name': 'j'}, 'process': {'pid': 1}}})
        with patch.object(process_lifecycle, 'job_adapter', return_value=adapter):
            runtime.refresh_children()
        job.records.assert_called_once_with(strict=False)
        self.assertEqual(runtime.children['router']['descendants'], [{'pid': 2, 'started': 1.0}])

    def test_a_descendant_that_ends_or_reads_differently_is_skipped_without_a_job(self):
        reference = MagicMock()
        reference.__enter__.return_value = reference
        reference.alive.return_value = True
        reference.process.children.return_value = [Mock(pid=5), Mock(pid=6), Mock(pid=7)]
        good = {'pid': 7, 'started': 3.0, 'user': 'me'}
        outcomes = {5: process_lifecycle.LifecycleError('Process identity mismatch'),
                    6: psutil.NoSuchProcess(6), 7: good}

        def capture(pid):
            if isinstance(outcomes[pid], Exception):
                raise outcomes[pid]
            return outcomes[pid]

        runtime = self.runtime({'redis': {'process': {'pid': 1, 'user': 'me'}, 'descendants': []}})
        with patch.object(process_lifecycle, 'ProcessReference', Mock(return_value=reference, capture=capture)):
            runtime.refresh_children()
        self.assertEqual(runtime.children['redis']['descendants'], [good])
