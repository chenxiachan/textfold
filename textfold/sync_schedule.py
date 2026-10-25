"""Coalesce synchronization requests without losing events during a running pass."""
import random
import threading
import time


class SyncSchedule:
    def __init__(self, clock=time.monotonic, jitter=random.uniform):
        self.clock, self.jitter = clock, jitter
        self.condition = threading.Condition()
        self.generation, self.handled = 1, 0
        self.due, self.retry_after = 0, 0
        self.failures, self.idle_delay = 0, 15
        self.closed = False
        self.last_external = {}

    def request(self, reason='local', healthy=False):
        with self.condition:
            if self.closed or (reason == 'focus' and healthy):
                return
            now = self.clock()
            if reason in ('focus', 'online', 'manual'):
                if now - self.last_external.get(reason, float('-inf')) < 2:
                    return
                self.last_external[reason] = now
                if reason in ('online', 'manual'):
                    self.retry_after = 0
            if self.generation == self.handled:
                self.due = min(self.due, now + 0.15)
            self.generation += 1
            self.condition.notify_all()

    def take(self):
        with self.condition:
            if self.closed or self.clock() < max(self.due, self.retry_after):
                return None
            return self.generation

    def finish(self, generation, *, error=False, blocked=False, healthy=False, changed=False, pending=False):
        with self.condition:
            self.handled = generation
            now = self.clock()
            if error:
                self.failures += 1
                delay = 300 if blocked else min(60, 2 ** min(self.failures - 1, 6))
                self.retry_after = now + delay * self.jitter(0.9, 1.0)
            else:
                self.failures, self.retry_after = 0, 0
                self.idle_delay = 30 if changed or pending else min(300, self.idle_delay * 2)
                delay = 300 if healthy else self.idle_delay
            self.due = now + delay * self.jitter(0.9, 1.0)
            if self.generation != generation:
                self.due = now + 0.15
            self.condition.notify_all()

    def wait(self):
        with self.condition:
            delay = max(0, max(self.due, self.retry_after) - self.clock())
            if not self.closed and delay:
                self.condition.wait(delay)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
