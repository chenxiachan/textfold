"""Notification hints and idle scheduling, using only disposable workspaces."""
import json
import os
import tempfile
import threading
import queue
import unittest
from unittest import mock

from textfold.change_notifications import PROTOCOL, ChangeNotifications, endpoint, message
from textfold.cloud_storage import CloudStorageBackend, StorageError, StorageAuthenticationError, StorageIdentityError
from textfold.documents import Document, Source, LibraryState
from textfold.offline_storage import OfflineBackend
from textfold.sync_schedule import SyncSchedule
from textfold.sync_records import library_records


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class Schedule(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.schedule = SyncSchedule(self.clock, lambda a, b: 1)

    def test_healthy_idle_hour_has_twelve_fallback_checks_instead_of_thirty_second_polls(self):
        generation = self.schedule.take()
        self.schedule.finish(generation, healthy=True)
        checks = 0
        for tick in range(120):
            self.clock.advance(30)
            generation = self.schedule.take()
            if generation is not None:
                checks += 1
                self.schedule.finish(generation, healthy=True)
        self.assertEqual(checks, 12)

    def test_end_of_sync_requests_are_not_cleared_and_bursts_share_one_pass(self):
        generation = self.schedule.take()
        for _ in range(100):
            self.schedule.request('changed')
        self.schedule.finish(generation, healthy=True)
        self.clock.advance(.15)
        following = self.schedule.take()
        self.assertGreater(following, generation)
        self.schedule.finish(following, healthy=True)
        self.assertIsNone(self.schedule.take())

    def test_failure_backoff_survives_local_edits_but_online_can_wake_it(self):
        for expected in (1, 2, 4, 8, 16, 32, 60):
            generation = self.schedule.take()
            self.assertIsNotNone(generation)
            self.schedule.finish(generation, error=True)
            self.schedule.request('local')
            self.clock.advance(expected - .01)
            self.assertIsNone(self.schedule.take())
            self.clock.advance(.01)
        self.schedule.finish(self.schedule.take(), error=True, blocked=True)
        self.schedule.request('online')
        self.clock.advance(.15)
        self.assertIsNotNone(self.schedule.take())

    def test_healthy_focus_does_not_wake_remote_sync_and_close_rejects_old_callbacks(self):
        self.schedule.finish(self.schedule.take(), healthy=True)
        self.schedule.request('focus', healthy=True)
        self.assertIsNone(self.schedule.take())
        self.schedule.close()
        self.schedule.request('ready')
        self.clock.advance(600)
        self.assertIsNone(self.schedule.take())

    def test_polling_fallback_slows_down_without_changes(self):
        intervals = []
        for _ in range(6):
            self.schedule.finish(self.schedule.take())
            intervals.append(self.schedule.due - self.clock())
            self.clock.advance(intervals[-1])
        self.assertEqual(intervals, [30, 60, 120, 240, 300, 300])

    def test_focus_does_not_debounce_a_manual_retry_or_network_recovery(self):
        for reason in ('manual', 'online'):
            schedule = SyncSchedule(self.clock, lambda a, b: 1)
            schedule.finish(schedule.take(), error=True, blocked=True)
            schedule.request('focus')
            schedule.request(reason)
            self.clock.advance(.15)
            self.assertIsNotNone(schedule.take())


class WireProtocol(unittest.TestCase):
    def test_handshake_uses_bounded_frames_header_authentication_and_disabled_wire_logging(self):
        remote = CloudStorageBackend('https://storage.invalid', 'reader', 'fixture-password')
        with mock.patch('websockets.sync.client.connect') as connect:
            remote.open_notifications('wss://storage.invalid/v2/events')
        args = connect.call_args.kwargs
        self.assertIn('Authorization', args['additional_headers'])
        self.assertNotIn('fixture-password', connect.call_args.args[0])
        self.assertTrue(args['logger'].disabled)
        self.assertEqual((args['ping_interval'], args['max_size'], args['max_queue']), (60, 4096, 8))
        self.assertIsNone(args['proxy'])

    def test_capability_paths_are_same_origin_and_never_carry_credentials(self):
        for path in ('https://other.invalid/events', '//other.invalid/events', '/events?password=x', '/events#x', 'ws://other.invalid', '\\other.invalid', '/\nevents'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                endpoint('https://storage.invalid', {'protocol': PROTOCOL, 'path': path})
        self.assertEqual(endpoint('https://storage.invalid', {'protocol': PROTOCOL, 'path': '/prefix/v2/events'}), 'wss://storage.invalid/prefix/v2/events')
        self.assertEqual(endpoint('http://127.0.0.1:8787', {'protocol': PROTOCOL, 'path': 'v2/events'}), 'ws://127.0.0.1:8787/v2/events')
        self.assertIsNone(endpoint('https://storage.invalid', None))
        remote = CloudStorageBackend('https://storage.invalid', 'reader', 'fixture-password')
        with self.assertRaises(ValueError):
            remote.open_notifications('wss://other.invalid/v2/events')

    def test_messages_validate_identity_size_protocol_and_cursor_without_acknowledging_anything(self):
        self.assertEqual(message(json.dumps({'type': 'changed', 'library_id': 'fixture', 'cursor': 4}), 'fixture')['cursor'], 4)
        for cursor in (-1, True, 1.2, '4', 9007199254740992):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                message(json.dumps({'type': 'changed', 'library_id': 'fixture', 'cursor': cursor}), 'fixture')
        with self.assertRaises(StorageIdentityError):
            message(json.dumps({'type': 'ready', 'library_id': 'wrong', 'protocol': PROTOCOL}), 'fixture', ready=True)
        for raw in ('x' * 4097, '[]', b'{}', '{}'):
            with self.subTest(raw=type(raw)), self.assertRaises(ValueError):
                message(raw, 'fixture')


class EmptyRemote:
    url = 'https://storage.invalid'
    def __init__(self):
        self.enabled = True
        self.health = {'library_id': 'fixture', 'cursor': 0, 'sync_enabled': True}
        self.calls = []
    def sync_health(self):
        self.calls.append('health')
        return {**self.health, 'sync_enabled': self.enabled}
    def sync_index(self):
        self.calls.append('index')
        return []
    def sync_changes(self, cursor):
        self.calls.append('changes')
        return {'changes': [], 'cursor': cursor, 'more': False}
    def sync_snapshot(self, identity):
        self.calls.append('snapshot:' + identity)
        return {'records': {}, 'revision': None, 'state': 'active', 'placement': {}, 'info': {}}


class IdleSync(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.remote = EmptyRemote()
        self.backend = OfflineBackend(temp.name, self.remote)

    def test_unchanged_fallback_is_exactly_one_remote_request(self):
        self.assertFalse(self.backend.sync()['error'])
        self.remote.calls.clear()
        self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.remote.calls, ['health'])

    def test_virtual_idle_hour_counts_twelve_health_requests_and_no_snapshot_or_change_reads(self):
        clock = Clock()
        self.backend.sync()
        schedule = SyncSchedule(clock, lambda a, b: 1)
        schedule.finish(schedule.take(), healthy=True)
        self.remote.calls.clear()
        for _ in range(120):
            clock.advance(30)
            generation = schedule.take()
            if generation is not None:
                self.backend.sync()
                schedule.finish(generation, healthy=True)
        self.assertEqual(self.remote.calls, ['health'] * 12)

    def test_legacy_writes_and_missing_library_baseline_never_use_fast_return(self):
        self.backend.sync()
        self.remote.calls.clear()
        self.remote.enabled = False
        self.backend.sync()
        self.assertIn('index', self.remote.calls)
        self.assertIn('snapshot:@library', self.remote.calls)
        self.remote.enabled = True
        with self.backend.cache.transaction() as db:
            db.execute("DELETE FROM records WHERE document_id='@library'")
            db.execute("DELETE FROM documents WHERE id='@library'")
        self.remote.calls.clear()
        self.backend.sync()
        self.assertIn('snapshot:@library', self.remote.calls)

    def test_notification_does_not_advance_cursor_or_clear_durable_outbox(self):
        self.backend.sync()
        self.backend.save(Document('md-offline', meta={'id': 'md-offline'}, source=Source(html='Original')))
        self.backend._notification('changed', 100)
        with self.backend.cache.transaction() as db:
            self.assertEqual(db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()[0], '0')
        self.assertEqual(self.backend.status()['pending'], 1)
        before = self.backend._schedule.generation
        self.backend._notification('changed', 0)
        self.assertEqual(self.backend._schedule.generation, before)


class LegacyRemote(EmptyRemote):
    def __init__(self):
        super().__init__()
        self.enabled = False
        self.rename('Old')

    def rename(self, name):
        self.library = {'records': library_records(LibraryState(catalog={'folders': [{'id': 'f-legacy', 'name': name}]})),
                        'revision': name, 'state': 'active', 'placement': {}, 'info': {}}

    def activate(self):
        self.rename('Legacy update')
        self.enabled, self.health['cursor'] = True, 1

    def sync_changes(self, cursor):
        self.calls.append('changes')
        return {'changes': [{'document_id': 'md-other'}] if cursor < self.health['cursor'] else [],
                'cursor': self.health['cursor'], 'more': False}

    def sync_snapshot(self, identity):
        result = super().sync_snapshot(identity)
        return self.library if identity == '@library' else {**result, 'revision': 'other'}


class LegacyBaselines(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.remote = LegacyRemote()
        self.backend = self.reopen()
        self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.name(), 'Old')

    def reopen(self):
        backend = OfflineBackend(self.temp.name, self.remote)
        self.addCleanup(backend.close)
        return backend

    def name(self):
        return self.backend.get_library().catalog['folders'][0]['name']

    def test_transition_refreshes_legacy_library_before_enabling_idle_fast_return(self):
        self.remote.activate()
        self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.name(), 'Legacy update')
        self.remote.calls.clear()
        self.backend.sync()
        self.assertEqual(self.remote.calls, ['health'])

    def test_reopen_after_activation_refreshes_once_and_persists_the_fenced_baseline(self):
        self.backend.close()
        self.remote.activate()
        self.backend = self.reopen()
        self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.name(), 'Legacy update')
        self.backend.close()
        self.backend = self.reopen()
        self.remote.calls.clear()
        self.backend.sync()
        self.assertEqual(self.remote.calls, ['health'])

    def test_existing_v2_cache_with_matching_cursor_still_requires_a_verified_baseline(self):
        # Older builds persisted loaded @library and a feed cursor, but no
        # proof that the library was read after legacy writes were fenced.
        self.remote.activate()
        with self.backend.cache.transaction() as db:
            db.execute("UPDATE sync_state SET value='1' WHERE key='cursor'")
        self.backend.close()
        self.backend = self.reopen()
        self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.name(), 'Legacy update')

    def test_interrupted_baseline_is_retried_without_clearing_offline_documents(self):
        self.backend.save(Document('md-offline', meta={'id': 'md-offline'}, source=Source(html='Keep these original bytes')))
        self.remote.activate()
        snapshot = self.remote.sync_snapshot
        def unavailable(identity):
            if identity == '@library':
                raise StorageError('fixture: library read interrupted')
            return snapshot(identity)
        # Keep a real outbox item without attempting a fake remote upload.
        with mock.patch.object(self.backend, '_push'), mock.patch.object(self.remote, 'sync_snapshot', side_effect=unavailable):
            self.assertTrue(self.backend.sync()['error'])
        with self.backend.cache.transaction() as db:
            self.assertIsNone(db.execute("SELECT value FROM sync_state WHERE key='v2_baseline'").fetchone())
        self.assertEqual(self.backend.get('md-offline').source.html, 'Keep these original bytes')
        self.assertEqual(self.backend.status()['pending'], 1)
        self.backend.close()
        self.backend = self.reopen()
        with mock.patch.object(self.backend, '_push'):
            self.assertFalse(self.backend.sync()['error'])
        self.assertEqual(self.name(), 'Legacy update')
        self.assertEqual(self.backend.status()['pending'], 1)


class FakeConnection:
    def __init__(self):
        self.messages = queue.Queue()
        self.closed = threading.Event()
    def recv(self, timeout=None):
        value = self.messages.get(timeout=timeout)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)
    def close(self):
        if not self.closed.is_set():
            self.closed.set()
            self.messages.put(OSError('closed'))


class ConnectionLifecycle(unittest.TestCase):
    def listener(self, remote, callback=lambda *args: None):
        listener = ChangeNotifications(remote, callback, clock=lambda: 0, jitter=lambda a, b: 1)
        self.addCleanup(listener.close)
        listener.configure({'library_id': 'fixture', 'notifications': {'protocol': PROTOCOL, 'path': '/v2/events'}})
        listener.start()
        return listener

    def test_authentication_failure_waits_five_minutes_instead_of_reconnecting_each_second(self):
        remote = mock.Mock(url='https://storage.invalid')
        remote.open_notifications.side_effect = StorageAuthenticationError('fixture detail must not leak')
        listener = self.listener(remote)
        with listener.condition:
            self.assertTrue(listener.condition.wait_for(lambda: listener.retry_at > 0, timeout=2))
        self.assertEqual(listener.retry_at, 300)
        self.assertEqual(remote.open_notifications.call_count, 1)
        self.assertNotIn('fixture detail', listener.status()['error'])

    def test_wrong_library_stops_reconnect_and_never_dispatches_ready(self):
        connection = FakeConnection()
        connection.messages.put({'type': 'ready', 'protocol': PROTOCOL, 'library_id': 'different-library'})
        remote = mock.Mock(url='https://storage.invalid')
        remote.open_notifications.return_value = connection
        callbacks = []
        listener = self.listener(remote, lambda *args: callbacks.append(args))
        with listener.condition:
            self.assertTrue(listener.condition.wait_for(lambda: listener.blocked, timeout=2))
        listener.configure({'library_id': 'fixture', 'notifications': {'protocol': PROTOCOL, 'path': '/v2/events'}})
        self.assertEqual(remote.open_notifications.call_count, 1)
        self.assertEqual(callbacks, [])

    def test_close_during_handshake_does_not_deliver_late_ready_or_start_another_thread(self):
        entered, release = threading.Event(), threading.Event()
        connection = FakeConnection()
        connection.messages.put({'type': 'ready', 'protocol': PROTOCOL, 'library_id': 'fixture'})
        remote = mock.Mock(url='https://storage.invalid')
        def connect(url):
            entered.set()
            self.assertTrue(release.wait(2))
            return connection
        remote.open_notifications.side_effect = connect
        callbacks = []
        listener = self.listener(remote, lambda *args: callbacks.append(args))
        self.assertTrue(entered.wait(2))
        closed = threading.Thread(target=listener.close)
        closed.start()
        release.set(); closed.join(3)
        listener.start()
        self.assertFalse(closed.is_alive())
        self.assertFalse(listener.thread.is_alive())
        self.assertEqual(callbacks, [])
        self.assertEqual(remote.open_notifications.call_count, 1)

    def test_start_is_idempotent_ready_catches_up_and_close_invalidates_late_callbacks(self):
        connection = FakeConnection()
        connection.messages.put({'type': 'ready', 'protocol': PROTOCOL, 'library_id': 'fixture'})
        connection.messages.put({'type': 'changed', 'library_id': 'fixture', 'cursor': 3})
        connection.messages.put({'type': 'changed', 'library_id': 'fixture', 'cursor': 2})
        connection.messages.put({'type': 'changed', 'library_id': 'fixture', 'cursor': 3})
        ready, changed = threading.Event(), threading.Event()
        calls = []
        def callback(reason, cursor):
            calls.append((reason, cursor))
            if reason == 'ready': ready.set()
            if reason == 'changed': changed.set()
        remote = mock.Mock(url='https://storage.invalid')
        remote.open_notifications.return_value = connection
        listener = ChangeNotifications(remote, callback)
        self.addCleanup(listener.close)
        listener.configure({'library_id': 'fixture', 'notifications': {'protocol': PROTOCOL, 'path': '/v2/events'}})
        listener.start(); listener.start()
        self.assertTrue(ready.wait(2)); self.assertTrue(changed.wait(2))
        listener.close(); listener.start()
        self.assertEqual(remote.open_notifications.call_count, 1)
        self.assertEqual([c for c in calls if c[0] == 'changed'], [('changed', 3)])
        self.assertFalse(listener.thread.is_alive())


class MixedFailureRequests(unittest.TestCase):
    def test_short_connections_and_http_outage_have_a_bounded_combined_request_count(self):
        clock = Clock()
        remote = EmptyRemote()
        remote.health['notifications'] = {'protocol': PROTOCOL, 'path': '/v2/events'}
        http_times, socket_times, errors = [], [], []
        health = remote.sync_health
        def checked_health():
            http_times.append(clock())
            if 90 <= clock() < 210:
                errors.append(clock())
                raise StorageError('fixture: HTTP offline')
            return health()
        def short_connection(url):
            socket_times.append(clock())
            connection = FakeConnection()
            connection.messages.put({'type': 'ready', 'protocol': PROTOCOL, 'library_id': 'fixture'})
            connection.messages.put(OSError('fixture: dropped immediately after ready'))
            return connection
        remote.sync_health, remote.open_notifications = checked_health, short_connection
        with tempfile.TemporaryDirectory() as root:
            backend = OfflineBackend(root, remote, clock=clock, jitter=lambda a, b: 1)
            try:
                backend.start()
                for _ in range(2401):
                    # Wake both real background loops using the same virtual
                    # clock, rather than independently counting two schedulers.
                    with backend._notifications.condition:
                        backend._notifications.condition.notify_all()
                        self.assertTrue(backend._notifications.condition.wait_for(
                            lambda: not backend._notifications.allowed or backend._notifications.retry_at > clock(), 2))
                    with backend._schedule.condition:
                        backend._schedule.condition.notify_all()
                        self.assertTrue(backend._schedule.condition.wait_for(
                            lambda: max(backend._schedule.due, backend._schedule.retry_after) > clock(), 2))
                    clock.advance(.25)
                self.assertFalse(backend.status()['error'])
                self.assertGreaterEqual(len(socket_times), 8)
                self.assertLessEqual(len(socket_times), 22)
                http_requests = len(http_times) + sum(call != 'health' for call in remote.calls)
                self.assertLessEqual(http_requests, 65)
                self.assertLessEqual(http_requests + len(socket_times), 85)
                for previous, following, minimum in zip(socket_times, socket_times[1:], (1, 2, 4, 8, 16, 32)):
                    self.assertGreaterEqual(following - previous, minimum)
                self.assertTrue(errors)
                # A socket attempt can race the first failing HTTP request;
                # after that failure suspends notifications, retries stay gated.
                self.assertFalse(any(errors[0] + .25 < stamp < 210 for stamp in socket_times), socket_times)
                self.assertTrue(any(stamp >= 210 for stamp in socket_times))
            finally:
                backend.close()
            self.assertFalse(backend._thread.is_alive())
            self.assertFalse(backend._notifications.thread.is_alive())


@unittest.skipUnless(os.environ.get('TEXTFOLD_TEST_STORAGE_URL'), 'run cloudflare/storage npm test')
class HttpNotifications(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.a, self.b = self.device('a'), self.device('b')
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        import uuid
        self.identity = 'md-' + uuid.uuid4().hex
        self.a.save(Document(self.identity, meta={'id': self.identity, 'title': 'Notification fixture'},
                             source=Source(html='Immutable original'), notes={'notes': [{'id': 'n-one', 'anchor': {'unit': 'p1'}, 'text': 'Original'}]}))
        self.assertFalse(self.a.sync()['error'])
        self.assertFalse(self.b.sync()['error'])
        self.b.get(self.identity)

    def device(self, name):
        remote = CloudStorageBackend(os.environ['TEXTFOLD_TEST_STORAGE_URL'], os.environ['TEXTFOLD_TEST_STORAGE_USER'],
                                     os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD'])
        return OfflineBackend(self.root / name, remote)

    def watch(self, backend, identity, predicate):
        applied = threading.Event()
        original = backend._accept
        def accept(id, snapshot, **kw):
            result = original(id, snapshot, **kw)
            if id == identity and predicate(snapshot):
                applied.set()
            return result
        patch = mock.patch.object(backend, '_accept', side_effect=accept)
        patch.start(); self.addCleanup(patch.stop)
        return applied

    def start_ready(self, backend):
        ready = threading.Event()
        original = backend._notifications.callback
        def notify(reason, cursor):
            original(reason, cursor)
            if reason == 'ready': ready.set()
        backend._notifications.callback = notify
        backend.start(); backend.start()
        self.assertTrue(ready.wait(5), backend.status())

    def idle(self, backend):
        with backend._schedule.condition:
            self.assertTrue(backend._schedule.condition.wait_for(
                lambda: backend._schedule.generation == backend._schedule.handled and not backend._sync_lock.locked(), 5), backend.status())

    def test_same_process_socket_reconnect_repairs_changes_missed_while_unsubscribed(self):
        self.start_ready(self.b)
        self.idle(self.b)
        thread = self.b._thread
        old_connection = self.b._notifications.connection
        disconnected, checked, connecting, release, reconnected = [threading.Event() for _ in range(5)]
        callback, finish, connect = self.b._notifications.callback, self.b._schedule.finish, self.b.remote.open_notifications
        def notify(reason, cursor):
            if reason == 'disconnected': disconnected.set()
            callback(reason, cursor)
            if reason == 'ready': reconnected.set()
        def finished(*args, **kwargs):
            result = finish(*args, **kwargs)
            if disconnected.is_set() and not reconnected.is_set(): checked.set()
            return result
        def reconnect(url):
            connecting.set()
            if not release.wait(10): raise TimeoutError('fixture: reconnect barrier')
            return connect(url)
        arrived = self.watch(self.b, self.identity, lambda s: s['records'].get('annotation/n-one', {}).get('text') == 'During socket outage')
        with mock.patch.object(self.b._notifications, 'callback', side_effect=notify), \
             mock.patch.object(self.b._schedule, 'finish', side_effect=finished), \
             mock.patch.object(self.b.remote, 'open_notifications', side_effect=reconnect) as opened:
            try:
                old_connection.close()
                self.assertTrue(disconnected.wait(5), self.b.status())
                self.assertTrue(checked.wait(5), self.b.status())
                self.assertTrue(connecting.wait(5), self.b.status())
                doc = self.a.get(self.identity)
                doc.notes['notes'][0]['text'] = 'During socket outage'
                self.a.save(doc)
                self.assertFalse(self.a.sync()['error'])
                self.assertEqual(self.b.get(self.identity).notes['notes'][0]['text'], 'Original')
                release.set()
                self.assertTrue(reconnected.wait(5), self.b.status())
                self.assertTrue(arrived.wait(5), self.b.status())
                self.assertEqual(opened.call_count, 1)
                self.assertIs(self.b._thread, thread)
                self.assertIsNot(self.b._notifications.connection, old_connection)
                self.assertEqual(self.b.get(self.identity).source.html, 'Immutable original')
            finally:
                release.set()

    def test_save_during_real_sync_thread_finish_is_uploaded_by_the_next_serial_pass(self):
        self.start_ready(self.b)
        self.idle(self.b)
        finishing, release = threading.Event(), threading.Event()
        finish, sync = self.b._schedule.finish, self.b.sync
        active, maximum = 0, 0
        active_lock = threading.Lock()
        def serial_sync():
            nonlocal active, maximum
            with active_lock:
                active += 1
                maximum = max(maximum, active)
            try: return sync()
            finally:
                with active_lock: active -= 1
        def paused_finish(*args, **kwargs):
            if not finishing.is_set():
                finishing.set()
                if not release.wait(10): raise TimeoutError('fixture: finish barrier')
            return finish(*args, **kwargs)
        applied = self.watch(self.b, self.identity, lambda s: s['records'].get('annotation/n-one', {}).get('text') == 'Saved at end of sync')
        with mock.patch.object(self.b._schedule, 'finish', side_effect=paused_finish), \
             mock.patch.object(self.b, 'sync', side_effect=serial_sync):
            try:
                self.b.request_sync('manual')
                self.assertTrue(finishing.wait(5), self.b.status())
                doc = self.b.get(self.identity)
                doc.notes['notes'][0]['text'] = 'Saved at end of sync'
                self.b.save(doc)
                self.assertEqual(self.b.status()['pending'], 1)
                release.set()
                self.assertTrue(applied.wait(5), self.b.status())
                self.idle(self.b)
                self.assertEqual(self.b.status()['pending'], 0)
                self.assertEqual(maximum, 1)
                self.assertEqual(self.a.remote.sync_snapshot(self.identity)['records']['annotation/n-one']['text'], 'Saved at end of sync')
                self.assertEqual(self.b.get(self.identity).source.html, 'Immutable original')
            finally:
                release.set()

    def test_other_device_edits_and_deletes_wake_the_single_http_sync_executor(self):
        changed = self.watch(self.b, self.identity, lambda s: s['records'].get('annotation/n-one', {}).get('text') == 'From device A')
        self.start_ready(self.b)
        doc = self.a.get(self.identity)
        doc.notes['notes'][0]['text'] = 'From device A'
        self.a.save(doc)
        self.assertFalse(self.a.sync()['error'])
        self.assertTrue(changed.wait(5), self.b.status())
        self.assertEqual(self.b.get(self.identity).notes['notes'][0]['text'], 'From device A')
        removed = self.watch(self.b, self.identity, lambda s: s['state'] == 'trash')
        self.a.trash(self.identity)
        self.assertFalse(self.a.sync()['error'])
        self.assertTrue(removed.wait(5), self.b.status())
        with self.assertRaises(FileNotFoundError): self.b.get(self.identity)

    def test_library_groups_arrive_without_unconditional_library_snapshot_polls(self):
        arrived = self.watch(self.b, '@library', lambda s: s['records'].get('catalog.folders/f-events', {}).get('name') == 'From notification')
        self.start_ready(self.b)
        state = self.a.get_library()
        state.catalog['folders'] = [{'id': 'f-events', 'name': 'From notification'}]
        self.a.save_library(state)
        self.assertFalse(self.a.sync()['error'])
        self.assertTrue(arrived.wait(5), self.b.status())
        self.assertEqual(self.b.get_library().catalog['folders'][0]['name'], 'From notification')

    def test_commit_between_health_check_and_websocket_registration_is_caught_by_ready(self):
        arrived = self.watch(self.b, self.identity, lambda s: s['records'].get('annotation/n-one', {}).get('text') == 'During handshake')
        connect = self.b.remote.open_notifications
        def race(url):
            doc = self.a.get(self.identity)
            doc.notes['notes'][0]['text'] = 'During handshake'
            self.a.save(doc)
            self.assertFalse(self.a.sync()['error'])
            return connect(url)
        with mock.patch.object(self.b.remote, 'open_notifications', side_effect=race):
            self.start_ready(self.b)
            self.assertTrue(arrived.wait(5), self.b.status())
        self.assertEqual(self.b.get(self.identity).notes['notes'][0]['text'], 'During handshake')

    def test_missed_notifications_and_offline_changes_recover_from_durable_log_on_restart(self):
        doc = self.b.get(self.identity)
        doc.notes['notes'].append({'id': 'n-offline', 'anchor': {'unit': 'p1'}, 'text': 'Saved while disconnected'})
        self.b.save(doc)
        self.b.close()
        changed = self.a.get(self.identity)
        changed.notes['notes'][0]['text'] = 'Missed remote hint'
        self.a.save(changed); self.assertFalse(self.a.sync()['error'])
        restarted = self.device('b')
        self.addCleanup(restarted.close)
        self.assertFalse(restarted.sync()['error'])
        result = restarted.get(self.identity)
        self.assertEqual({n['text'] for n in result.notes['notes']}, {'Saved while disconnected', 'Missed remote hint'})
        self.assertEqual(result.source.html, 'Immutable original')


@unittest.skipUnless(os.environ.get('TEXTFOLD_TEST_LEGACY_STORAGE_URL'), 'run cloudflare/storage npm test')
class LegacyHttpTransitions(unittest.TestCase):
    def transition(self, restart, dirty_library=False):
        import uuid
        with tempfile.TemporaryDirectory() as root:
            remote = CloudStorageBackend(os.environ['TEXTFOLD_TEST_LEGACY_STORAGE_URL'],
                                         os.environ['TEXTFOLD_TEST_STORAGE_USER'], os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD'])
            revision = str(uuid.uuid4())
            remote._request('PUT', 'library', {'revision': None, 'commit': revision,
                'catalog': {'folders': [{'id': 'f-old', 'name': 'Old'}]}, 'zotero': {}})
            remote.save(Document('md-legacy', meta={'id': 'md-legacy', 'title': 'Legacy original'},
                                 source=Source(html='Immutable legacy original'), notes={'notes': []}))
            backend = OfflineBackend(root, remote)
            self.addCleanup(backend.close)
            self.assertFalse(backend.sync()['error'])
            self.assertEqual(backend.get_library().catalog['folders'][0]['name'], 'Old')
            original = backend.get('md-legacy')
            original.notes['notes'].append({'id': 'n-pending', 'text': 'Keep offline edit', 'anchor': {'unit': 'p1'}})
            backend.save(original)
            if dirty_library:
                local_library = backend.get_library()
                local_library.catalog['folders'].append({'id': 'f-offline', 'name': 'Keep offline folder'})
                backend.save_library(local_library)
            if restart: backend.close()
            remote._request('PUT', 'library', {'revision': revision, 'commit': str(uuid.uuid4()),
                'catalog': {'folders': [{'id': 'f-new', 'name': 'Legacy update'}]}, 'zotero': {}})
            remote.sync_commit('md-other', {'operation_id': str(uuid.uuid4()), 'revision': None, 'state': 'active', 'placement': {},
                'changes': [{'key': 'meta', 'value': {'id': 'md-other', 'title': 'Activates v2'}, 'deleted': False},
                            {'key': 'source/html', 'value': 'Other original', 'deleted': False}]})
            if restart:
                backend = OfflineBackend(root, remote)
                self.addCleanup(backend.close)
            self.assertFalse(backend.sync()['error'])
            expected = {'f-new': 'Legacy update'}
            if dirty_library: expected['f-offline'] = 'Keep offline folder'
            self.assertEqual({f['id']: f['name'] for f in backend.get_library().catalog['folders']}, expected)
            self.assertEqual(backend.get('md-legacy').source.html, 'Immutable legacy original')
            self.assertEqual(remote.sync_snapshot('md-legacy')['records']['annotation/n-pending']['text'], 'Keep offline edit')
            self.assertEqual(backend.status()['pending'], 0)
            backend.close()
            backend = OfflineBackend(root, remote)
            self.addCleanup(backend.close)
            with mock.patch.object(remote, 'sync_snapshot', wraps=remote.sync_snapshot) as snapshots, \
                 mock.patch.object(remote, 'sync_index', wraps=remote.sync_index) as index:
                self.assertFalse(backend.sync()['error'])
                self.assertEqual({f['id']: f['name'] for f in backend.get_library().catalog['folders']}, expected)
                snapshots.assert_not_called()
                index.assert_not_called()

    def test_v1_library_changes_are_caught_at_v2_activation(self):
        self.transition(restart=False)

    def test_v1_library_changes_are_caught_after_cache_reopen(self):
        self.transition(restart=True)

    def test_transition_preserves_pending_library_changes(self):
        self.transition(restart=True, dirty_library=True)
