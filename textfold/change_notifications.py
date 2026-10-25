"""Optional generic change hints. This transport never acknowledges stored data."""
import json
import random
import threading
import time
import urllib.parse

from .cloud_storage import StorageAuthenticationError, StorageIdentityError

PROTOCOL = 'textfold/changes-ws-v1'
MAX_MESSAGE = 4096


def _close(connection):
    if connection:
        try:
            connection.close()
        except Exception:
            pass  # A broken transport must not keep shutdown/reconfiguration alive.


def endpoint(origin, capability):
    if capability is None:
        return None
    if not isinstance(capability, dict) or capability.get('protocol') != PROTOCOL:
        raise ValueError('Unsupported storage notification protocol; using polling')
    path = capability.get('path')
    if not isinstance(path, str) or not path or len(path) > 2048 or '\\' in path or any(ord(c) <= 32 for c in path):
        raise ValueError('Invalid storage notification endpoint; using polling')
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Notifications must use a same-origin path without credentials or query parameters')
    url = urllib.parse.urlsplit(urllib.parse.urljoin(origin + '/', path))
    base = urllib.parse.urlsplit(origin)
    if (url.scheme, url.netloc) != (base.scheme, base.netloc):
        raise ValueError('Notifications must use the configured storage origin')
    return urllib.parse.urlunsplit(('wss' if url.scheme == 'https' else 'ws', url.netloc, url.path, '', ''))


def message(raw, library, ready=False):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_MESSAGE:
        raise ValueError('Invalid storage notification message')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('type') != ('ready' if ready else 'changed'):
        raise ValueError('Unexpected storage notification message')
    if value.get('library_id') != library:
        raise StorageIdentityError('Notification library identity changed; keep this workspace and check storage settings')
    if ready:
        if value.get('protocol') != PROTOCOL:
            raise ValueError('Unsupported storage notification protocol')
    else:
        cursor = value.get('cursor')
        if type(cursor) is not int or not 0 <= cursor <= 9007199254740991:
            raise ValueError('Invalid storage notification cursor')
    return value


class ChangeNotifications:
    def __init__(self, remote, callback, clock=time.monotonic, jitter=random.uniform):
        self.remote, self.callback = remote, callback
        self.clock, self.jitter = clock, jitter
        self.condition = threading.Condition()
        self.thread, self.connection = None, None
        self.closed, self.allowed, self.blocked = False, False, False
        self.generation, self.retry_at, self.failures = 0, 0, 0
        self.key = None
        self.state, self.error = 'polling', ''

    def start(self):
        with self.condition:
            if self.closed or self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._run, name='textfold-storage-events', daemon=True)
            self.thread.start()

    def configure(self, health):
        error = ''
        try:
            url = endpoint(self.remote.url, health.get('notifications'))
        except ValueError as failure:
            url, error = None, str(failure)
        key = (url, health['library_id'])
        connection = None
        with self.condition:
            if self.closed:
                return
            if key != self.key:
                self.generation += 1
                self.key, self.retry_at, self.failures, self.blocked = key, 0, 0, False
                connection, self.connection = self.connection, None
                self.state, self.error = 'polling', error
            self.allowed = True
            self.condition.notify_all()
        _close(connection)

    def status(self):
        with self.condition:
            return {'state': self.state, 'error': self.error}

    def suspend(self):
        with self.condition:
            self.allowed = False
            self.generation += 1
            self.state = 'polling'
            connection, self.connection = self.connection, None
            self.condition.notify_all()
        _close(connection)

    def close(self):
        with self.condition:
            self.closed = True
        self.suspend()
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(12)

    def _run(self):
        while True:
            with self.condition:
                if self.closed:
                    return
                if not self.allowed or self.blocked or not self.key or not self.key[0]:
                    self.condition.wait()
                    continue
                delay = self.retry_at - self.clock()
                if delay > 0:
                    self.condition.wait(delay)
                    continue
                generation, (url, library) = self.generation, self.key
                self.state = 'connecting'
            connection, connected, error, blocked, delay = None, False, '', False, 60
            connected_at = self.clock()
            try:
                connection = self.remote.open_notifications(url)
                with self.condition:
                    if self.closed or generation != self.generation:
                        continue
                    self.connection = connection
                message(connection.recv(timeout=10), library, ready=True)
                with self.condition:
                    if self.closed or generation != self.generation:
                        continue
                    self.state, self.error = 'connected', ''
                connected_at = self.clock()
                connected = True
                self.callback('ready', None)
                highest = -1
                while True:
                    value = message(connection.recv(), library)
                    with self.condition:
                        if self.closed or generation != self.generation:
                            break
                    if value['cursor'] > highest:
                        highest = value['cursor']
                        self.callback('changed', highest)
            except StorageIdentityError as failure:
                error, blocked = str(failure), True
            except StorageAuthenticationError:
                error, delay = 'Storage notification authentication failed', 300
            except (ValueError, ImportError):
                error, delay = 'Storage notification protocol unavailable; using polling', 60
            except Exception:
                # Never log a transport exception: it may contain handshake
                # headers, a URL, or credentials supplied by an external server.
                error = 'Storage notification connection unavailable; using polling'
                if connected and self.clock() - connected_at >= 60:
                    self.failures = 0
                delay = min(60, 2 ** min(self.failures, 6))
            finally:
                _close(connection)
                with self.condition:
                    current = generation == self.generation and not self.closed
                    if generation == self.generation:
                        self.connection = None
                        self.state, self.error, self.blocked = 'polling', error, blocked
                        self.failures += 1
                        self.retry_at = self.clock() + delay * self.jitter(0.9, 1.0)
                    self.condition.notify_all()
                if connected and current:
                    self.callback('disconnected', None)
