"""Machine-local cloud selection. Never part of a document or library copy."""
import json
import hashlib
import os
import tempfile
import threading
import uuid
from pathlib import Path

from .cloud_storage import CloudStorageBackend, UnavailableCloudStorage

_lock = threading.RLock()
SERVICE = "TextFold cloud storage v1"


def _keyring():
    try:
        import keyring
        backend = keyring.get_keyring()
        if backend.priority <= 0:
            raise RuntimeError("No keyring backend")
        return backend
    except Exception:
        raise OSError("System keyring is unavailable; install/unlock a supported keyring backend") from None


def _password(config):
    try:
        password = _keyring().get_password(SERVICE, config["credential"])
    except Exception:
        raise OSError("Could not read the cloud password from the system keyring") from None
    if not password:
        raise ValueError("Re-enter the cloud password in Settings")
    return password


def load(home):
    path = Path(home) / "cloud-storage.json"
    if not path.exists():
        return {}
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
        raise ValueError("Invalid cloud storage configuration")
    return config


def _client(config):
    client = CloudStorageBackend(config.get("url", ""), config.get("username", ""), _password(config))
    client.configuration_revision = config.get("revision")
    return client


def _workspace(config, home, runtime=None):
    base = Path(config.get("cache_path") or Path(runtime or home) / "cloud-cache").expanduser().resolve()
    namespace = hashlib.sha256(json.dumps([config.get("url"), config.get("username")], ensure_ascii=False).encode("utf-8")).hexdigest()[:32]
    return base / namespace


def _write_config(home, config):
    folder = Path(home)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="cloud-storage.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(config, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, folder / "cloud-storage.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _retire_credentials(previous, current):
    def references(config):
        source = config.get("switch_from", {}).get("config", {})
        return {ref for ref in (config.get("credential"), source.get("credential")) if ref}
    unused = references(previous) - references(current)
    if unused:
        try:
            ring = _keyring()
        except OSError:
            return  # A locked keyring must not make a safely saved selection fail.
        for credential in unused:
            _remove(ring, credential)


def configured(home, runtime=None):
    with _lock:
        config = load(home)
        previous = config.get("switch_from")
        if previous:
            from .sqlite_storage import SQLiteBackend
            # This runs before accepting work. The old process could have saved
            # more edits after Settings was closed, or during its network probe.
            source = SQLiteBackend(previous["cache"], tracking=True)
            state = source.status()
            old = config
            if state["pending"] or state["conflicts"]:
                config = {**previous["config"], "cache_path": str(source.root.parent),
                          "switch_error": "Storage switch canceled: local changes or conflicts remain in the previous workspace. Synchronize or export them before switching."}
            else:
                config = {k: v for k, v in config.items() if k != "switch_from"}
            _write_config(home, config)
            _retire_credentials(old, config)
    if not config.get("enabled"):
        return None
    try:
        client = _client(config)
    except (ValueError, OSError) as error:
        client = UnavailableCloudStorage(config, str(error))
    from .offline_storage import OfflineBackend
    backend = OfflineBackend(_workspace(config, home, runtime), client)
    backend.selection = dict(config)
    backend.selection_runtime = runtime
    return backend


def status(home, active):
    config = load(home)
    from .offline_storage import OfflineBackend
    cloud = isinstance(active, (CloudStorageBackend, OfflineBackend))
    return {"enabled": bool(config.get("enabled")), "url": config.get("url", ""),
            "username": config.get("username", ""), "hasPassword": bool(config.get("credential")),
            "active": "cloud" if cloud else getattr(active, "name", "local"),
            "error": config.get("switch_error") or getattr(active, "configuration_error", ""),
            "cachePath": config.get("cache_path", ""),
            "sync": active.status() if isinstance(active, OfflineBackend) else None,
            "restartRequired": bool(config.get("enabled")) != cloud or
                (cloud and config.get("revision") != getattr(active, "configuration_revision", None))}


def _candidate(home, body):
    config = load(home)
    url, username = body.get("url"), body.get("username")
    url = url.rstrip("/") if isinstance(url, str) else url
    password = body.get("password")
    # An omitted/blank password can keep only the same endpoint's credentials.
    if password in (None, "") and url == config.get("url") and username == config.get("username") and config.get("credential"):
        password = _password(config)
    client = CloudStorageBackend(url, username, password, timeout=15)
    return client, password


def test(home, body):
    client, _ = _candidate(home, body)
    client.check()
    client.sync_health()
    return {"ok": True}


def save(home, body, active):
    with _lock:
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Choose local or cloud storage")
        config = load(home)
        previous = dict(config)
        from .offline_storage import OfflineBackend
        source = getattr(active, "selection", config.get("switch_from", {}).get("config", config))
        if isinstance(active, OfflineBackend) and (active.status()["pending"] or active.status()["conflicts"]):
            same = enabled and isinstance(body.get("url"), str) and body["url"].rstrip("/") == source.get("url") and body.get("username") == source.get("username")
            same = same and body.get("cache_path", config.get("cache_path", "")) == source.get("cache_path", "")
            if not same:
                raise ValueError("Local changes or conflicts remain; synchronize and resolve them before changing libraries. Export is available as a recovery backup.")
        credential = None
        ring = None
        if enabled:
            client, password = _candidate(home, body)
            client.check()
            client.sync_health()
            cache_path = body.get("cache_path", config.get("cache_path", ""))
            if not isinstance(cache_path, str) or (cache_path and not Path(cache_path).expanduser().is_absolute()):
                raise ValueError("The cache folder must be an absolute path")
            ring, credential = _keyring(), str(uuid.uuid4())
            try:
                ring.set_password(SERVICE, credential, password)
                if ring.get_password(SERVICE, credential) != password:
                    raise RuntimeError("Credential verification failed")
            except Exception:
                _remove(ring, credential)
                raise OSError("Could not save the cloud password in the system keyring") from None
            config.update(url=client.url, username=body["username"], credential=credential,
                          cache_path=str(Path(cache_path).expanduser().resolve()) if cache_path else "")
        config.update(enabled=enabled, revision=str(uuid.uuid4()))
        config.pop("switch_from", None)
        config.pop("switch_error", None)
        switching = isinstance(active, OfflineBackend) and (not enabled or
            _workspace(config, home, getattr(active, "selection_runtime", None)) != active.cache.root)
        if switching:
            config["switch_from"] = {"config": {k: v for k, v in source.items() if k not in ("switch_from", "switch_error")},
                                     "cache": str(active.cache.root)}
        try:
            _write_config(home, config)
        except Exception:
            if credential:
                _remove(ring, credential)
            raise
        _retire_credentials(previous, config)
        if isinstance(active, OfflineBackend) and not switching:
            active.selection = dict(config)
        return status(home, active)


def _remove(ring, credential):
    try:
        ring.delete_password(SERVICE, credential)
    except Exception:
        pass  # A stale credential is safer than undoing a committed selection.
