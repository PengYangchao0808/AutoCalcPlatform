# Fix: Remote Archive Download Fails with TypeError

## Root cause

In `src/acp/api/v1_routes.py:779`, the `ZipStream` constructor is called incorrectly:

```python
ZipStream(_files_iter())  # type: ignore[arg-type]
```

`ZipStream.__init__()` signature is `def __init__(self, *, compress_type=0, ...)` — the `*` means **no positional arguments** are accepted. Passing the generator as a positional arg raises:

```
TypeError: ZipStream.__init__() takes 1 positional argument but 2 were given
```

The `# type: ignore[arg-type]` comment was suppressing the type checker warning for this bug.

## Fix

Replace the incorrect constructor call pattern with the proper `ZipStream` API:

1. Create `ZipStream()` instance (no args)
2. Call `zs.add(iterable, arcname)` for each file — `add()` supports iterables (generators), so the lazy SFTP streaming works correctly
3. Pass the `ZipStream` instance directly to `StreamingResponse` (it's iterable, yielding ZIP bytes)

### File: `src/acp/api/v1_routes.py` (lines 772-782)

**Before:**
```python
    def _files_iter():
        for rel_path, _info in files:
            yield rel_path, fetcher.stream_file(record, rel_path)

    safe_job_id = job_id.replace('"', "").replace("\\", "")
    _log_remote_access(request, job_id, "", "archive")
    return StreamingResponse(
        ZipStream(_files_iter()),  # type: ignore[arg-type]
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_job_id}.zip"'},
    )
```

**After:**
```python
    zs = ZipStream()
    for rel_path, _info in files:
        zs.add(fetcher.stream_file(record, rel_path), rel_path)

    safe_job_id = job_id.replace('"', "").replace("\\", "")
    _log_remote_access(request, job_id, "", "archive")
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_job_id}.zip"'},
    )
```

## Verification

After applying the fix, restart the service:
```bash
sudo systemctl restart acp
```

Then test by downloading a remote job's archive from the web dashboard.
