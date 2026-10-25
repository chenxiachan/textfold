# Generation storage overhead

A deterministic fixture drives the real import, generation, translation and
model-reply cache paths, with a stand-in model and no storage network. It has
16 sections and an 8 MiB original PDF; all libraries are temporary. This measures
storage overhead, not real model latency or end-to-end cloud upload time.

On the same Windows machine, before/after measurements were:

| Backend | Before | After |
| --- | ---: | ---: |
| Local directory | 0.326 s | 0.313 s |
| SQLite | 4.203 s | 1.667 s |
| Cloud with local SQLite working copy | 4.487 s | 1.637 s |

Whole-folder scans during cloud generation fell from 12 to 4, original-PDF reads
from 16 to 4, and total local bytes read from 194,114,892 to 92,728,345.
The cloud backend issued one sync wakeup on workspace exit instead of two stage
wakeups. These single-run timings illustrate the diagnosed overhead; they are
not a claim of local-directory parity. SQLite checkpoints still cost more than
writing individual local files, and actual results depend on document size,
disk, model and network.

The primary regression was repeated whole-document materialization for small
workspace reads and writes. Stage checkpoints also woke the background uploader
before the remaining model work finished. Small reads now use the working files;
checkpoints refresh only concurrent changes; an in-memory, document-scoped lease
defers remote publication. Durable local checkpoints and the transactional
outbox are preserved. Restart releases the lease and resumes the normal outbox;
no database migration or existing-data rewrite is required.

`python -m unittest discover -s tests -p test_generation_storage.py -v` covers
payload-free small reads/layer writes, deferred sync, nested leases, safe cache
cleanup, and stopped generation with durable completed replies. Local D1/R2
integration tests cover manual synchronization during a lease, unrelated-document
progress and generation starting while a remote snapshot is in flight.
