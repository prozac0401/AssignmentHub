"""Transactional accounting for bytes reserved but not durably received yet."""

ACTIVE_SQL = "('uploading','paused','verifying','finalizing')"


def initialize_disk_reservations(db):
    # Rebuild on startup from durable offsets. Uncommitted tails are discarded by
    # recovery; counting those tails twice until then is safely conservative.
    db.execute("""CREATE TABLE IF NOT EXISTS disk_reservation (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        unwritten_bytes INTEGER NOT NULL CHECK(unwritten_bytes>=0)
    )""")
    db.execute(f"""INSERT INTO disk_reservation
        SELECT 1, COALESCE(SUM(f.size-f.offset),0)
        FROM files f JOIN uploads u ON u.id=f.upload_id WHERE u.status IN {ACTIVE_SQL}
        ON CONFLICT(singleton) DO UPDATE SET unwritten_bytes=excluded.unwritten_bytes""")
    # These triggers participate in the caller's transaction, including rollback
    # after failed chunk commits, cancellation, and finalization.
    for action, changes in (
        ("INSERT", [("NEW", 1)]),
        ("DELETE", [("OLD", -1)]),
        ("UPDATE OF size,offset,upload_id", [("OLD", -1), ("NEW", 1)]),
    ):
        operations = "\n".join(
            f"""UPDATE disk_reservation SET unwritten_bytes=unwritten_bytes
                + ({sign}) * ({row}.size-{row}.offset)
                WHERE singleton=1 AND EXISTS (SELECT 1 FROM uploads
                    WHERE id={row}.upload_id AND status IN {ACTIVE_SQL});"""
            for row, sign in changes
        )
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS disk_files_{action.split()[0].lower()}
            AFTER {action} ON files BEGIN {operations} END""")
    db.execute(f"""CREATE TRIGGER IF NOT EXISTS disk_upload_status
        AFTER UPDATE OF status ON uploads
        WHEN (OLD.status IN {ACTIVE_SQL}) != (NEW.status IN {ACTIVE_SQL})
        BEGIN
            UPDATE disk_reservation SET unwritten_bytes=unwritten_bytes
                + CASE WHEN NEW.status IN {ACTIVE_SQL} THEN 1 ELSE -1 END
                * (SELECT COALESCE(SUM(size-offset),0) FROM files WHERE upload_id=NEW.id)
                WHERE singleton=1;
        END""")
