"""Client for the visualRAG KB service (distillation + ingestion).

Used by the galaxy-voting labeling UI to:
  * batch /distill archive rounds into teaching-case drafts (pre-ingest queue),
  * commit an expert-confirmed sample to the live KB via /ingest,
  * poll /health for the KB-link status badge.

Path resolution is DB-driven: the caller passes the resolved ``container_path``
(source) + ``galaxy_id`` + ``timestamp_dir``; this module locates the GALFIT
archive and its grandparent object dir (mask/sigma), packages a self-contained
zip (the same contract the MCP ``visualrag_client`` uses, plus the comparison
PNG + component-analysis report), and POSTs it. Everything is best-effort: any
failure returns None so the labeling UI degrades gracefully.
"""
from __future__ import annotations

import glob
import io
import logging
import os
import re
import zipfile

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180  # /distill + /ingest do a DINOv2 forward (+ VLM call on /distill)

# Feedme file-reference lines rewritten to basenames so the staged archive is
# self-contained (the KB's resolve_aux_path matches mask/sigma by basename).
_FEEDME_LINE_RE = re.compile(r"^([ABCDFG])\)\s+(\S+)(.*)$", re.MULTILINE)


def _normalize_feedme_paths(text: str) -> str:
    def repl(m: re.Match) -> str:
        return f"{m.group(1)}) {os.path.basename(m.group(2))}{m.group(3)}"
    return _FEEDME_LINE_RE.sub(repl, text)


def archive_paths(container_path: str, galaxy_id: str, timestamp_dir: str) -> tuple[str, str]:
    """Resolve (archive_dir, object_dir) for a single-band source.

    ``archive_dir`` holds *_galfit.fits + feedme + galfit.NN + comparison PNG +
    component_analysis.md; ``object_dir`` (the galaxy dir, = grandparent of the
    timestamp) holds mask_*.fits / sigma_*.fits.
    """
    archive = os.path.join(container_path, galaxy_id, "archives", timestamp_dir)
    obj = os.path.join(container_path, galaxy_id)
    return archive, obj


def package_material(archive_dir: str, obj_dir: str) -> bytes:
    """Gather the archive's raw material + comparison PNG + report into a flat zip.

    Aux files (mask/sigma) are often symlinks into a shared examples tree; if a
    symlink target is unreadable (e.g. not bind-mounted into the container) the
    file is skipped with a warning rather than aborting the whole package.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        feedmes = glob.glob(os.path.join(archive_dir, "*.feedme"))
        if feedmes:
            with open(feedmes[0], encoding="utf-8", errors="replace") as f:
                zf.writestr(os.path.basename(feedmes[0]), _normalize_feedme_paths(f.read()))
        # GALFIT model cube + comparison PNG + summary: accept BOTH naming
        # conventions — JWST <obj>_galfit.fits / <obj>_galfit_comparison.png AND
        # gadotti galfit.fit / galfit_comparison.png. The old *_galfit.* suffix
        # globs silently dropped the gadotti files, so the zip arrived without the
        # FITS and /ingest raised FileNotFoundError. galfit_summary.md is staged
        # too so the server can read the runner-declared output name (rename-safe)
        # rather than guess the filename. Dedup by basename.
        gathered: dict[str, str] = {}
        for pat in ("*_galfit.fits", "*_galfit.fit", "galfit.fits", "galfit.fit",
                    "galfit.[0-9]*", "*galfit_comparison*.png",
                    "*galfit_summary.md", "galfit_summary.md",
                    "*component_analysis*.md"):
            for f in glob.glob(os.path.join(archive_dir, pat)):
                gathered[f] = os.path.basename(f)
        for f, name in gathered.items():
            try:
                zf.write(f, name)
            except OSError as e:
                log.warning("skip unreadable %s: %s", f, e)
        for pat in ("mask_*.fits", "sigma_*.fits"):
            for f in glob.glob(os.path.join(obj_dir, pat)):
                try:
                    zf.write(f, os.path.basename(f))
                except OSError as e:
                    log.warning("skip unreadable aux %s: %s", f, e)
    return buf.getvalue()


def _service_url() -> str:
    return os.environ.get("VISUALRAG_SERVICE_URL", "").rstrip("/")


def enabled() -> bool:
    return bool(_service_url()) and os.environ.get("VISUALRAG_ENABLED", "1") != "0"


def health() -> dict | None:
    """KB /health, or None when disabled / unreachable."""
    if not enabled():
        return None
    try:
        r = requests.get(f"{_service_url()}/health", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /health failed: %s", e)
        return None


def distill(container_path: str, galaxy_id: str, timestamp_dir: str,
            library: str, hint: str | None = None) -> tuple[dict | None, str | None]:
    """/distill one round -> ({image_description, reasoning} | None, error_msg | None).

    The error_msg carries the service's specific failure reason (e.g. "no
    component_analysis report", "VLM did not return valid JSON") so the UI can
    show it instead of a generic "蒸馏失败". Returns (None, msg) on failure,
    (distillation, None) on success.
    """
    if not enabled():
        return None, "KB 服务未配置(VISUALRAG_SERVICE_URL 为空)"
    archive, obj = archive_paths(container_path, galaxy_id, timestamp_dir)
    try:
        z = package_material(archive, obj)
        r = requests.post(
            f"{_service_url()}/distill",
            files={"archive": ("archive.zip", z, "application/zip")},
            data={"library": library, **({"hint": hint} if hint else {})},
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /distill request failed (%s/%s): %s", galaxy_id, timestamp_dir, e)
        return None, f"请求 KB 服务失败: {e}"
    if r.status_code != 200:
        # surface the server's specific error (503 missing file / 422 bad VLM json / ...)
        try:
            detail = (r.json() or {}).get("error") or r.text.strip()[:300]
        except ValueError:
            detail = r.text.strip()[:300]
        log.warning("visualRAG /distill %s/%s -> HTTP %s: %s",
                    galaxy_id, timestamp_dir, r.status_code, detail)
        return None, f"KB 服务返回 {r.status_code}: {detail}"
    body = r.json()
    if body.get("status") == "ok":
        return body.get("distillation"), None
    return None, body.get("error") or "KB 服务返回未知错误"


def ingest(container_path: str, galaxy_id: str, timestamp_dir: str,
           payload: dict) -> tuple[dict | None, str | None]:
    """Commit one sample to the live KB via /ingest.

    ``payload`` = {sample_id, obj_id, library, final_labels(list),
    image_description(markdown str), reasoning(markdown str), archive_path?}.
    Returns ``(committed {sample_id, library, size, image}, None)`` on success or
    ``(None, error_msg)`` on failure. The error_msg carries the service's specific
    reason (e.g. "No *_galfit.fits ...", "sample_id already exists") so the UI can
    show it instead of a generic "入库失败".
    """
    if not enabled():
        return None, "KB 服务未配置(VISUALRAG_SERVICE_URL 为空)"
    archive, obj = archive_paths(container_path, galaxy_id, timestamp_dir)
    try:
        import json
        z = package_material(archive, obj)
        data = {
            "sample_id": payload["sample_id"],
            "obj_id": payload.get("obj_id", ""),
            "library": payload["library"],
            "final_labels": json.dumps(payload.get("final_labels") or []),
            "component_signature": json.dumps(payload.get("component_signature") or []),
            "image_description": json.dumps(payload.get("image_description") or ""),
            "reasoning": json.dumps(payload.get("reasoning") or ""),
        }
        if payload.get("archive_path"):
            data["archive_path"] = payload["archive_path"]
        r = requests.post(
            f"{_service_url()}/ingest",
            files={"archive": ("archive.zip", z, "application/zip")},
            data=data,
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /ingest request failed (%s/%s): %s", galaxy_id, timestamp_dir, e)
        return None, f"请求 KB 服务失败: {e}"
    if r.status_code != 200:
        try:
            detail = (r.json() or {}).get("error") or r.text.strip()[:300]
        except ValueError:
            detail = r.text.strip()[:300]
        log.warning("visualRAG /ingest %s/%s -> HTTP %s: %s",
                    galaxy_id, timestamp_dir, r.status_code, detail)
        return None, f"KB 服务返回 {r.status_code}: {detail}"
    body = r.json()
    if body.get("status") == "ok":
        return body, None
    return None, body.get("error") or "KB 服务返回未知错误"


def query(container_path: str, galaxy_id: str, timestamp_dir: str,
          top_k: int = 1, strategy: str = "both") -> tuple[dict | None, str | None]:
    """/query one round against the live KB -> (response_json | None, error_msg | None).

    Packages the SAME self-contained archive zip /distill + /ingest use, then
    POSTs it to the service's retrieval endpoint. ``top_k`` is per-library on the
    server: baseline = perfect-library top-1, positive = problem-library top-1,
    hard_negatives = problem-library remainder — so top_k=1 typically returns
    baseline + positive (2 cases); the UI renders whatever non-empty cases come
    back (1–3). Returns the parsed QueryResponse ({status, query, baseline,
    positive, hard_negatives, perfect, warnings}) on success, (None, msg) on
    failure.
    """
    if not enabled():
        return None, "KB 服务未配置(VISUALRAG_SERVICE_URL 为空)"
    archive, obj = archive_paths(container_path, galaxy_id, timestamp_dir)
    try:
        z = package_material(archive, obj)
        r = requests.post(
            f"{_service_url()}/query",
            files={"archive": ("archive.zip", z, "application/zip")},
            data={"top_k": str(top_k), "strategy": strategy},
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /query request failed (%s/%s): %s", galaxy_id, timestamp_dir, e)
        return None, f"请求 KB 服务失败: {e}"
    if r.status_code != 200:
        try:
            detail = (r.json() or {}).get("error") or r.text.strip()[:300]
        except ValueError:
            detail = r.text.strip()[:300]
        log.warning("visualRAG /query %s/%s -> HTTP %s: %s",
                    galaxy_id, timestamp_dir, r.status_code, detail)
        return None, f"KB 服务返回 {r.status_code}: {detail}"
    body = r.json()
    if body.get("status") in ("ok", "empty"):
        return body, None
    return None, (body.get("warnings") or [body.get("error") or "KB 服务返回未知错误"])[0]


# ── live-KB management (read / update-metadata / delete) ───────────────────
#
# These browse + maintain ALREADY-ingested entries (vs /distill + /ingest above,
# which create them). All best-effort: any failure returns None so the
# management page degrades gracefully. No payload packaging — pure HTTP to the
# service's /kb/entries routes.

# Metadata fields an expert may edit (must match the server's editable set).
EDITABLE_FIELDS = ("final_labels", "image_description", "reasoning", "obj_id",
                   "component_signature")


def list_entries(library=None, q=None, limit=50, offset=0):
    """GET /kb/entries -> {library, total, offset, limit, entries[...]} or None."""
    if not enabled():
        return None
    try:
        params = {"limit": limit, "offset": offset}
        if library:
            params["library"] = library
        if q:
            params["q"] = q
        r = requests.get(f"{_service_url()}/kb/entries", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /kb/entries failed: %s", e)
        return None


def get_entry(library, sample_id):
    """GET /kb/entries/{lib}/{id} -> full record, or None if absent/unreachable."""
    if not enabled():
        return None
    try:
        r = requests.get(f"{_service_url()}/kb/entries/{library}/{sample_id}",
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /kb/entries get failed: %s", e)
        return None


def update_entry(library, sample_id, patch):
    """PATCH /kb/entries/{lib}/{id} (JSON; only EDITABLE_FIELDS) -> updated
    record, or None on failure. ``patch`` may carry extra keys; they're filtered."""
    if not enabled():
        return None
    body = {k: v for k, v in (patch or {}).items()
            if k in EDITABLE_FIELDS and v is not None}
    try:
        r = requests.patch(f"{_service_url()}/kb/entries/{library}/{sample_id}",
                           json=body, timeout=DEFAULT_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /kb/entries patch failed: %s", e)
        return None


def delete_entry(library, sample_id):
    """DELETE /kb/entries/{lib}/{id} -> {status, sample_id, library, size}, or None."""
    if not enabled():
        return None
    try:
        r = requests.delete(f"{_service_url()}/kb/entries/{library}/{sample_id}",
                            timeout=DEFAULT_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG /kb/entries delete failed: %s", e)
        return None


def image_url(library, sample_id):
    """The service URL of an entry's comparison PNG (for server-side fetch in the
    proxy route; the browser reaches the view app, not the host service)."""
    if not enabled() or not sample_id:
        return None
    return f"{_service_url()}/images/{library}/{sample_id}"
