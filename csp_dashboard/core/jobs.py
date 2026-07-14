"""
Tiny in-memory background-job registry.

The CSP dashboard is a single local Flask process serving ONE CSP, so a plain
dict + a daemon thread is all we need to run a slow task (OCR extraction of a
scanned upload) without blocking the browser — no external queue/broker.

A job records live progress (done/total/message) that the UI polls, plus a
final result or error. Jobs live only in memory: if the app is restarted while
one is running it's simply gone, which is fine here — the CSP just re-uploads.

Used by the upload flow so a 10-15 page scan OCRs in the background behind a
progress bar instead of freezing the page for minutes.
"""
import threading
import uuid

_JOBS = {}
_LOCK = threading.Lock()


def _update(job_id, **fields):
    with _LOCK:
        _JOBS.setdefault(job_id, {}).update(fields)


def get(job_id):
    """Return a snapshot copy of a job's state, or None if unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def start(target, *args, **kwargs):
    """Run target(*args, progress=<cb>, **kwargs) in a daemon thread.

    `target` must accept a keyword `progress` — a callback progress(done:int,
    total:int, message:str) it calls to report advancement. The target's return
    value becomes the job's `result`; any exception becomes its `error`.
    Returns the new job_id immediately (does not block).
    """
    job_id = uuid.uuid4().hex
    _update(job_id, status="running", done=0, total=0,
            message="Starting…", result=None, error=None)

    def _progress(done, total, message=""):
        _update(job_id, done=int(done), total=int(total), message=str(message))

    def _run():
        try:
            result = target(*args, progress=_progress, **kwargs)
            _update(job_id, status="done", result=result, message="Done")
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            _update(job_id, status="error", error=str(e), message="Failed")

    threading.Thread(target=_run, daemon=True).start()
    return job_id
