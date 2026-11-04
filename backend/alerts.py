"""Internal change alerts. No messaging connector or trading capability."""
import json
from datetime import timedelta

from .core import digest, dumps, uid
from .timeutil import instant, iso


def evaluate_alerts(store, as_of=None):
    now = iso(as_of)
    emitted = 0
    for rule in store.rows("SELECT * FROM alert_rules WHERE enabled=1 ORDER BY rule_id"):
        config = json.loads(rule["config_json"])
        state_key = "alert_state:" + rule["rule_id"]
        state = store.setting(state_key, {})
        candidates = []
        if rule["kind"] == "new_event":
            for row in store.rows("SELECT v.* FROM events e JOIN event_versions v ON v.event_id=e.event_id AND v.version=e.latest_version WHERE v.created_at>=? AND v.created_at<=? ORDER BY v.created_at,v.event_id", (rule["created_at"], now)):
                event = json.loads(row["payload_json"])
                if config.get("asset_id") and config["asset_id"] not in event["asset_ids"]:
                    continue
                candidates.append((event["event_id"], str(event["event_version"]), config.get("asset_id"), event["title"], "Novo registro ou revisão de evento. O vínculo econômico ainda requer investigação."))
        elif rule["kind"] == "source_failure":
            for source in store.rows("SELECT * FROM provider_state ORDER BY provider_id"):
                key = source["provider_id"]
                fingerprint = source["status"] + ":" + str(source["error_code"])
                if source["status"] in {"unavailable", "blocked", "failed"}:
                    candidates.append((key, fingerprint, None, "Fonte indisponível: " + key, "Motivo: " + str(source["error_code"] or "unknown")))
                else:
                    state[key] = fingerprint  # recovery rearms next failure
        elif rule["kind"] == "analysis_change":
            for row in store.rows("SELECT * FROM analyses WHERE created_at>=? AND created_at<=? ORDER BY created_at,analysis_id", (rule["created_at"], now)):
                if config.get("asset_id") and config["asset_id"] != row["asset_id"]:
                    continue
                analysis = json.loads(row["payload_json"])
                classification = analysis["opportunity"]["classification"]
                if row["status"] == "insufficient_data":
                    continue
                candidates.append((row["asset_id"], classification, row["asset_id"], "Hipótese atualizada", "Classificação para investigação: " + classification))
        for entity, fingerprint, asset_id, title, body in candidates:
            if state.get(entity) == fingerprint:
                continue
            previous = store.rows("SELECT created_at FROM alerts WHERE dedupe_key LIKE ? ORDER BY created_at DESC LIMIT 1", (f"{rule['rule_id']}:{entity}:%",))
            if previous and instant(now) - instant(previous[0]["created_at"]) < timedelta(minutes=config.get("cooldown_minutes", 60)):
                continue
            # Transition counter permits rearming A -> healthy -> A, while an
            # unchanged error observed repeatedly remains one transition.
            sequence = int(state.get("_sequence", 0)) + 1
            dedupe_key = f"{rule['rule_id']}:{entity}:{sequence}:{digest(fingerprint)[:16]}"
            with store.transaction() as con:
                con.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?,?,0,0)", (uid("alert"), dedupe_key, asset_id, rule["kind"], title, body, now))
                state[entity], state["_sequence"] = fingerprint, sequence
                con.execute("INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,version=version+1,updated_at=excluded.updated_at", (state_key, dumps(state), now))
            emitted += 1
        store.set_setting(state_key, state)
    return emitted
