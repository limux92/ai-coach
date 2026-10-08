"""Immutable evidence transitions. Production publication is a Firestore transaction."""
from .storage import fingerprint, utcnow

VERSION = "workout_evidence_v1"
FIELDS = ("id", "local_date", "start_date_utc", "sport", "parse_status", "parser_version",
          "source_deleted", "source_excluded", "source_payload_sha256", "original_artifact",
          "parsed_artifact", "physiology_protocols", "physiology_protocol_artifact_sha256")


def transition(old, new, *, known_at):
    evidence = {key: new.get(key) for key in FIELDS}
    evidence["expected_elapsed_seconds"] = (new.get("metrics") or {}).get("elapsed_time_s")
    digest = fingerprint({"version": VERSION, "evidence": evidence})
    if old.get("physiology_evidence_sha256") == digest and old.get("physiology_revision_id"):
        return None
    previous = old.get("physiology_revision_id")
    revision_id = "evidence_" + fingerprint([new["id"], previous, digest])
    return {"id": revision_id, "workout_id": new["id"], "previous_revision_id": previous,
            "sequence": old.get("physiology_revision_sequence", 0) + 1,
            "evidence_sha256": digest, "schema_version": VERSION, "known_at": known_at,
            "evidence": evidence}


def commit_workout(store, doc_id, data, *, merge=True):
    """Use atomic production method; memory adapters implement the same protocol.

    The fallback is for offline in-memory stores only; the production Store always
    implements commit_workout_evidence. Existing imported timestamps aren't reused.
    """
    if hasattr(store, "commit_workout_evidence"):
        return store.commit_workout_evidence(doc_id, data, merge=merge)
    old = store.get("workouts", doc_id) or {}
    new = merged_workout(old, data, doc_id, merge)
    revision = transition(old, new, known_at=utcnow())
    if revision:
        store.put("physiology_revisions", revision["id"], revision, merge=False)
        store.put("physiology_jobs", revision["id"], {"id": revision["id"], "pending": True}, merge=False)
        store.put("sync_state", "physiology", {"status": "pending"})
        new.update(physiology_revision_id=revision["id"], physiology_evidence_sha256=revision["evidence_sha256"],
                   physiology_revision_sequence=revision["sequence"])
    store.put("workouts", doc_id, new, merge=False)


def merged_workout(old, data, doc_id, merge):
    new = {**old, **data} if merge else dict(data)
    new["id"] = doc_id
    # Locally authored protocol claims bind to an exact parsed artifact. A later
    # upstream replacement cannot inherit their meaning for a different recording.
    for key in ("physiology_revision_id", "physiology_evidence_sha256", "physiology_revision_sequence", "physiology_protocols",
                "physiology_protocol_artifact_sha256"):
        if key not in new and key in old:
            new[key] = old[key]
    return new


def put_immutable(store, collection, doc_id, value):
    if hasattr(store, "create"):
        store.create(collection, doc_id, value)
    elif store.get(collection, doc_id) is None:
        store.put(collection, doc_id, value, merge=False)
    return store.get(collection, doc_id)


def select_revisions(revisions, *, known_before):
    from .physiology_samples import timestamp
    cutoff = timestamp(known_before)
    latest = {}
    for revision in revisions:
        known = timestamp(revision["known_at"])
        if known and known < cutoff:
            key = revision["workout_id"]
            if key not in latest or (known, revision.get("sequence", 0)) > (
                    timestamp(latest[key]["known_at"]), latest[key].get("sequence", 0)):
                latest[key] = revision
    return list(latest.values())
