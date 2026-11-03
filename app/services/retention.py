from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Observation, RawSnapshot


def purge_snapshots(session: Session, older_than: datetime) -> int:
    stale_ids = list(
        session.scalars(select(RawSnapshot.id).where(RawSnapshot.retrieved_at < older_than)).all()
    )
    if not stale_ids:
        return 0
    session.execute(
        update(Observation).where(Observation.snapshot_id.in_(stale_ids)).values(snapshot_id=None)
    )
    snapshots = list(session.scalars(select(RawSnapshot).where(RawSnapshot.id.in_(stale_ids))).all())
    for snapshot in snapshots:
        session.delete(snapshot)
    session.flush()
    return len(stale_ids)
