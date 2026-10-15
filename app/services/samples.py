"""Sample lifecycle: create, edit, select, approve (PRD §7.3–7.5)."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_text, encrypt_text
from ..db import hours_from_now, utcnow
from ..errors import AppError
from ..models import GenerationJob, Sample, SampleItem, SessionModel
from ..schemas import ItemIn
from . import items as it
from .demo_items import DEMO_ITEMS

ACTIVE_STATUSES = ("queued", "validating", "generating", "checking")


def get_owned_sample(db: Session, sess: SessionModel, sample_id: str) -> Sample:
    """404 for missing or foreign objects: never reveal that another session's object exists."""
    s = db.get(Sample, sample_id)
    if s is None or s.session_id != sess.id:
        raise AppError("NOT_FOUND")
    if s.expires_at <= utcnow():
        raise AppError("REPORT_EXPIRED", "This sample has expired and was removed.")
    return s


def _has_active_job(db: Session, sample_id: str) -> bool:
    return db.scalar(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.sample_id == sample_id, GenerationJob.status.in_(ACTIVE_STATUSES))) > 0


def _all_items(db: Session, sample: Sample) -> list[SampleItem]:
    return list(db.scalars(select(SampleItem).where(SampleItem.sample_id == sample.id).order_by(SampleItem.position)))


def _existing_keys(db: Session, sample: Sample) -> set[tuple[str, str]]:
    return {(i.item_type, i.normalized_text_hash) for i in _all_items(db, sample)}


def _add_items(db: Session, sample: Sample, new_items: list[tuple[str, str]]) -> tuple[list[dict], int]:
    """Validate and insert. Returns (rejected, duplicates_skipped). Invalid items never block valid ones."""
    rejected: list[dict] = []
    dupes = 0
    existing = _all_items(db, sample)
    keys = {(i.item_type, i.normalized_text_hash) for i in existing}
    position = max((i.position for i in existing), default=-1) + 1
    total = len(existing)
    for idx, (item_type, raw) in enumerate(new_items):
        text, reason = it.validate_item(item_type, raw)
        if reason:
            rejected.append({"index": idx, "reason": reason})
            continue
        key = (item_type, it.dedupe_key(item_type, text))
        if key in keys:
            dupes += 1
            continue
        if total >= settings.max_submitted_items:
            raise AppError("INPUT_TOO_LARGE", f"You can submit at most {settings.max_submitted_items} items.")
        keys.add(key)
        db.add(SampleItem(sample_id=sample.id, item_type=item_type, text_ciphertext=encrypt_text(text),
                          normalized_text_hash=key[1], position=position,
                          is_sensitive_flagged=it.sensitive_category(text) is not None))
        position += 1
        total += 1
    db.flush()
    return rejected, dupes


def refresh(db: Session, sample: Sample) -> None:
    """Recompute the deterministic model-bound selection and counts; any change voids approval."""
    rows = _all_items(db, sample)
    cands = [it.Candidate(r.id, r.item_type, decrypt_text(r.text_ciphertext), r.is_sensitive_flagged, r.position)
             for r in rows]
    chosen = {c.id: n for n, c in enumerate(it.select_for_model(cands))}
    for r in rows:
        r.is_selected = r.id in chosen
        r.selection_order = chosen.get(r.id)
    sample.submitted_item_count = len(rows)
    sample.eligible_item_count = sum(1 for r in rows if not r.is_sensitive_flagged)
    sample.status, sample.approved_sample_hash, sample.approved_at = "draft", None, None
    db.flush()


def create_sample(db: Session, sess: SessionModel, source: str, items_in: list[ItemIn]) -> tuple[Sample, list[dict], int]:
    sample = Sample(session_id=sess.id, source_type=source, expires_at=hours_from_now(settings.sample_ttl_hours))
    db.add(sample)
    db.flush()
    new = [(i.item_type, i.text) for i in items_in]
    if source == "demo" and not new:
        new = list(DEMO_ITEMS)
    rejected, dupes = _add_items(db, sample, new)
    refresh(db, sample)
    return sample, rejected, dupes


def patch_sample(db: Session, sample: Sample, *, add: list[ItemIn], update: list, remove: list[str],
                 clear: bool) -> tuple[list[dict], int]:
    if _has_active_job(db, sample.id):
        raise AppError("GENERATION_IN_PROGRESS", "You can't change the sample while a roast is being generated.")
    if clear:
        db.execute(delete(SampleItem).where(SampleItem.sample_id == sample.id))
        db.flush()
    elif remove:
        db.execute(delete(SampleItem).where(SampleItem.sample_id == sample.id, SampleItem.id.in_(remove)))
        db.flush()
    rows = {r.id: r for r in _all_items(db, sample)}
    for u in update:
        row = rows.get(u.id)
        if row is None:
            raise AppError("NOT_FOUND", "Item not found.")
        text, reason = it.validate_item(row.item_type, u.text)
        if reason:
            raise AppError("INVALID_ITEM", reason)
        key = it.dedupe_key(row.item_type, text)
        if any(r.id != row.id and r.item_type == row.item_type and r.normalized_text_hash == key for r in rows.values()):
            raise AppError("INVALID_ITEM", "That item is already in your sample.")
        row.text_ciphertext = encrypt_text(text)
        row.normalized_text_hash = key
        row.is_sensitive_flagged = it.sensitive_category(text) is not None  # re-screen edited text
    db.flush()
    rejected, dupes = _add_items(db, sample, [(i.item_type, i.text) for i in add])
    refresh(db, sample)
    return rejected, dupes


def model_bound(db: Session, sample: Sample) -> list[tuple[str, str, str]]:
    """[(alias, item_type, text)] in selection order: exactly what a provider would receive."""
    rows = list(db.scalars(select(SampleItem).where(SampleItem.sample_id == sample.id, SampleItem.is_selected.is_(True))
                           .order_by(SampleItem.selection_order)))
    return [(it.alias_for(r.selection_order), r.item_type, decrypt_text(r.text_ciphertext)) for r in rows]


def compute_hash(db: Session, sample: Sample) -> str:
    return it.sample_hash(model_bound(db, sample))


def needed_more(sample: Sample) -> int:
    return max(0, settings.min_items - sample.eligible_item_count)


def approve(db: Session, sample: Sample) -> None:
    if sample.eligible_item_count < settings.min_items:
        n = needed_more(sample)
        raise AppError("INSUFFICIENT_ELIGIBLE_ITEMS", f"Add at least {n} more eligible item{'s' if n != 1 else ''} to continue.")
    sample.approved_sample_hash = compute_hash(db, sample)
    sample.approved_at = utcnow()
    sample.status = "approved"


def serialize(db: Session, sample: Sample) -> dict:
    rows = _all_items(db, sample)
    out_items = []
    for r in rows:
        text = decrypt_text(r.text_ciphertext)
        cat = it.sensitive_category(text) if r.is_sensitive_flagged else None
        out_items.append({
            "id": r.id, "item_type": r.item_type, "text": text,
            "is_sensitive_flagged": r.is_sensitive_flagged,
            "sensitive_category": it.CATEGORY_LABELS.get(cat) if cat else None,
            "is_selected": r.is_selected, "selection_order": r.selection_order,
            "alias": it.alias_for(r.selection_order) if r.selection_order is not None else None,
        })
    eligible = sample.eligible_item_count
    n_more = needed_more(sample)
    return {
        "id": sample.id, "status": sample.status, "source_type": sample.source_type,
        "submitted_item_count": sample.submitted_item_count, "eligible_item_count": eligible,
        "selected_count": sum(1 for r in rows if r.is_selected),
        "min_required": settings.min_items, "needed_more": n_more,
        "truncated": eligible > sum(1 for r in rows if r.is_selected),
        "items": out_items,
        "model_bound": [{"alias": a, "item_type": t, "text": x} for a, t, x in model_bound(db, sample)],
        "approved_sample_hash": sample.approved_sample_hash,
        "expires_at": sample.expires_at.isoformat() + "Z",
    }
