from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text

# One-time public key. The private key is not stored in this repository.
_RSA_N = 18367038836520755662861449134935391717921080703413112790041698712247281837423569213011242345894539156997441041238806216967549913187184086093618621665633523184907362980527686611678839759969942012405909552373357981919186116406436379682052873664901857361368038771037558126393465555276694812438849240501693034352166326676527430072371646029170928427438397720281505147048761954887254958671760552585665376267837578400079717588538685606247877478234053317846025586228046298533522091497972368156397571073469888479444387431428042056518664000721065671450240499083345100043408439412594115198220667867336755231049464416516841735443
_RSA_E = 65537
_RSA_BYTES = 256


def _clean(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(row):
    return {k: _clean(v) for k, v in dict(row).items()}


def _rsa_encrypt_key(key: bytes) -> bytes:
    ps_len = _RSA_BYTES - len(key) - 3
    ps = bytearray()
    while len(ps) < ps_len:
        chunk = os.urandom(ps_len - len(ps))
        ps.extend(b for b in chunk if b != 0)
    encoded = b"\x00\x02" + bytes(ps[:ps_len]) + b"\x00" + key
    m = int.from_bytes(encoded, "big")
    c = pow(m, _RSA_E, _RSA_N)
    return c.to_bytes(_RSA_BYTES, "big")


def _stream_xor(data: bytes, key: bytes) -> bytes:
    out = bytearray(len(data))
    pos = 0
    counter = 0
    while pos < len(data):
        block = hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        take = min(len(block), len(data) - pos)
        for i in range(take):
            out[pos + i] = data[pos + i] ^ block[i]
        pos += take
        counter += 1
    return bytes(out)


def _encrypt(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_clean).encode("utf-8")
    key = os.urandom(32)
    ciphertext = _stream_xor(raw, key)
    packet = {
        "v": 1,
        "ek": base64.b64encode(_rsa_encrypt_key(key)).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
        "mac": hmac.new(key, ciphertext, hashlib.sha256).hexdigest(),
    }
    return base64.b64encode(json.dumps(packet, separators=(",", ":")).encode("utf-8")).decode("ascii")


def build_encrypted_audit() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL missing")

    admin_ids = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_TELEGRAM_IDS", ""))]
    admin_filter = "TRUE" if not admin_ids else "u.telegram_id NOT IN (" + ",".join(str(x) for x in admin_ids) + ")"

    cte = f"""
    WITH paid AS (
        SELECT user_id,
               MIN(COALESCE(reviewed_at, created_at)) AS first_paid_at,
               SUM(GREATEST(COALESCE(paid_toman,0), COALESCE(amount_toman,0))) AS paid_toman
        FROM payment_receipts
        WHERE status='approved'
          AND GREATEST(COALESCE(paid_toman,0), COALESCE(amount_toman,0)) > 0
        GROUP BY user_id
    ), scope AS (
        SELECT u.*, p.first_paid_at, COALESCE(p.paid_toman,0) AS paid_toman
        FROM users u
        LEFT JOIN paid p ON p.user_id=u.id
        WHERE {admin_filter}
    )
    """

    out = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "admin_ids_excluded_count": len(admin_ids),
        "definition": {
            "non_admin": "telegram_id not in ADMIN_TELEGRAM_IDS",
            "ever_paid": "approved real-money receipt with positive amount",
            "never_paid": "no approved real-money receipt with positive amount",
            "free_phase": "activity before first real-money payment, or all activity for never-paid users",
        },
    }

    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        def one(name: str, sql: str):
            out[name] = _row(conn.execute(text(cte + sql)).mappings().one())

        def many(name: str, sql: str):
            out[name] = [_row(r) for r in conn.execute(text(cte + sql)).mappings().all()]

        one("user_base", """
            SELECT COUNT(*) AS total_non_admin_users,
                   COUNT(*) FILTER (WHERE onboarding_step='complete') AS onboarding_complete,
                   COUNT(*) FILTER (WHERE first_paid_at IS NULL) AS never_paid_users,
                   COUNT(*) FILTER (WHERE first_paid_at IS NOT NULL) AS ever_paid_users,
                   COUNT(*) FILTER (WHERE last_seen_at >= NOW() - INTERVAL '1 day') AS active_1d,
                   COUNT(*) FILTER (WHERE last_seen_at >= NOW() - INTERVAL '7 days') AS active_7d,
                   COUNT(*) FILTER (WHERE last_seen_at >= NOW() - INTERVAL '30 days') AS active_30d,
                   MIN(created_at) AS first_signup_at,
                   MAX(created_at) AS latest_signup_at,
                   MAX(last_seen_at) AS latest_seen_at
            FROM scope
        """)

        one("real_payments", """
            SELECT COUNT(*) FILTER (WHERE first_paid_at IS NOT NULL) AS paying_users,
                   COALESCE(SUM(paid_toman) FILTER (WHERE first_paid_at IS NOT NULL),0) AS approved_paid_toman
            FROM scope
        """)

        one("free_phase_messages", """
            SELECT COUNT(*) FILTER (WHERE m.role='user') AS user_messages,
                   COUNT(*) FILTER (WHERE m.role='assistant') AS assistant_messages,
                   COUNT(DISTINCT m.user_id) FILTER (WHERE m.role='user') AS users_who_messaged,
                   MIN(m.created_at) FILTER (WHERE m.role='user') AS first_user_message_at,
                   MAX(m.created_at) FILTER (WHERE m.role='user') AS latest_user_message_at
            FROM messages m JOIN scope s ON s.id=m.user_id
            WHERE m.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
        """)

        one("never_paid_messages", """
            SELECT COUNT(*) FILTER (WHERE m.role='user') AS user_messages,
                   COUNT(*) FILTER (WHERE m.role='assistant') AS assistant_messages,
                   COUNT(DISTINCT m.user_id) FILTER (WHERE m.role='user') AS users_who_messaged
            FROM messages m JOIN scope s ON s.id=m.user_id
            WHERE s.first_paid_at IS NULL
        """)

        one("free_phase_engagement", """
            SELECT COUNT(*) AS chatted_users,
                   ROUND(AVG(msg_count)::numeric,2) AS avg_user_messages,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY msg_count) AS median_user_messages,
                   PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY msg_count) AS p90_user_messages,
                   MAX(msg_count) AS max_user_messages,
                   COUNT(*) FILTER (WHERE msg_count>=5) AS users_5plus_messages,
                   COUNT(*) FILTER (WHERE msg_count>=20) AS users_20plus_messages,
                   COUNT(*) FILTER (WHERE msg_count>=50) AS users_50plus_messages,
                   COUNT(*) FILTER (WHERE msg_count>=100) AS users_100plus_messages
            FROM (
                SELECT m.user_id, COUNT(*) AS msg_count
                FROM messages m JOIN scope s ON s.id=m.user_id
                WHERE m.role='user'
                  AND m.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
                GROUP BY m.user_id
            ) x
        """)

        one("free_phase_returning", """
            SELECT COUNT(*) AS chatted_users,
                   COUNT(*) FILTER (WHERE active_days>=2) AS returned_on_another_day,
                   COUNT(*) FILTER (WHERE active_days>=3) AS active_3plus_days,
                   ROUND(AVG(active_days)::numeric,2) AS avg_active_days,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY active_days) AS median_active_days,
                   MAX(active_days) AS max_active_days
            FROM (
                SELECT m.user_id, COUNT(DISTINCT m.created_at::date) AS active_days
                FROM messages m JOIN scope s ON s.id=m.user_id
                WHERE m.role='user'
                  AND m.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
                GROUP BY m.user_id
            ) x
        """)

        many("free_phase_input_types", """
            SELECT COALESCE(m.input_type,'unknown') AS input_type,
                   COUNT(*) AS messages,
                   COUNT(DISTINCT m.user_id) AS users
            FROM messages m JOIN scope s ON s.id=m.user_id
            WHERE m.role='user'
              AND m.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
            GROUP BY COALESCE(m.input_type,'unknown') ORDER BY messages DESC
        """)

        many("relationship_stage_never_paid", """
            SELECT COALESCE(r.stage,'NO_STATE') AS stage, COUNT(*) AS users
            FROM scope s LEFT JOIN relationships r ON r.user_id=s.id
            WHERE s.first_paid_at IS NULL
            GROUP BY COALESCE(r.stage,'NO_STATE') ORDER BY users DESC
        """)

        many("partner_gender", """
            SELECT COALESCE(NULLIF(partner_gender,''),'unknown') AS partner_gender, COUNT(*) AS users
            FROM scope WHERE onboarding_step='complete'
            GROUP BY COALESCE(NULLIF(partner_gender,''),'unknown') ORDER BY users DESC
        """)

        one("memory_never_paid", """
            SELECT COUNT(mi.id) AS memory_items,
                   COUNT(DISTINCT mi.user_id) AS users_with_memory
            FROM memory_items mi JOIN scope s ON s.id=mi.user_id
            WHERE s.first_paid_at IS NULL
        """)

        many("memory_types_never_paid", """
            SELECT mi.type, COUNT(*) AS items, COUNT(DISTINCT mi.user_id) AS users
            FROM memory_items mi JOIN scope s ON s.id=mi.user_id
            WHERE s.first_paid_at IS NULL
            GROUP BY mi.type ORDER BY items DESC
        """)

        one("daily_usage_free_phase_approx", """
            SELECT COALESCE(SUM(d.llm_requests),0) AS llm_requests,
                   COALESCE(SUM(d.input_tokens),0) AS input_tokens,
                   COALESCE(SUM(d.output_tokens),0) AS output_tokens,
                   COALESCE(SUM(d.voice_tokens),0) AS voice_tokens,
                   COALESCE(SUM(d.daily_voice_sent),0) AS voice_outputs,
                   COALESCE(SUM(d.daily_stickers_sent),0) AS stickers_sent,
                   COALESCE(SUM(d.monthly_image_inputs_used),0) AS image_inputs_counter,
                   COALESCE(SUM(d.monthly_voice_inputs_used),0) AS voice_inputs_counter
            FROM daily_usage d JOIN scope s ON s.id=d.user_id
            WHERE d.date <= COALESCE(s.first_paid_at::date, DATE '9999-12-31')
        """)

        many("usage_charges_free_phase", """
            SELECT uc.feature, uc.provider, uc.model,
                   COUNT(*) AS requests,
                   COUNT(*) FILTER (WHERE uc.status='settled') AS settled,
                   COALESCE(SUM(uc.charged_coins),0) AS charged_coins,
                   COALESCE(SUM(uc.refunded_coins),0) AS refunded_coins,
                   COALESCE(SUM(uc.actual_cost_usd),0) AS provider_cost_usd
            FROM usage_charges uc JOIN scope s ON s.id=uc.user_id
            WHERE uc.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
            GROUP BY uc.feature, uc.provider, uc.model ORDER BY requests DESC
        """)

        one("usage_cost_free_phase", """
            SELECT COUNT(*) AS usage_charge_rows,
                   COALESCE(SUM(uc.charged_coins),0) AS charged_coins,
                   COALESCE(SUM(uc.refunded_coins),0) AS refunded_coins,
                   COALESCE(SUM(uc.actual_cost_usd),0) AS provider_cost_usd,
                   COALESCE(SUM(uc.actual_cost_usd * uc.exchange_rate_toman),0) AS provider_cost_toman_estimate
            FROM usage_charges uc JOIN scope s ON s.id=uc.user_id
            WHERE uc.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
        """)

        many("image_generation_free_phase", """
            SELECT COALESCE(ig.status,'unknown') AS status,
                   COALESCE(ig.content_mode,'unknown') AS content_mode,
                   COUNT(*) AS jobs,
                   COUNT(DISTINCT ig.user_id) AS users
            FROM image_generation_jobs ig JOIN scope s ON s.id=ig.user_id
            WHERE ig.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
            GROUP BY COALESCE(ig.status,'unknown'), COALESCE(ig.content_mode,'unknown')
            ORDER BY jobs DESC
        """)

        one("generated_voice_free_phase", """
            SELECT COUNT(*) AS outputs,
                   COUNT(*) FILTER (WHERE gv.sent_at IS NOT NULL) AS sent_outputs,
                   COUNT(DISTINCT gv.user_id) AS users
            FROM generated_voice_outputs gv JOIN scope s ON s.id=gv.user_id
            WHERE gv.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
        """)

        one("proactive_free_phase", """
            SELECT COUNT(*) AS created,
                   COUNT(*) FILTER (WHERE pm.sent_at IS NOT NULL OR pm.status='sent') AS sent,
                   COUNT(DISTINCT pm.user_id) FILTER (WHERE pm.sent_at IS NOT NULL OR pm.status='sent') AS users_reached
            FROM proactive_messages pm JOIN scope s ON s.id=pm.user_id
            WHERE pm.created_at < COALESCE(s.first_paid_at, TIMESTAMP '9999-12-31')
        """)

        many("addons_never_paid", """
            SELECT ua.addon_key, ua.source,
                   COUNT(*) AS activations,
                   COUNT(DISTINCT ua.user_id) AS users,
                   COALESCE(SUM(ua.price_paid_coins),0) AS coins_spent
            FROM user_addons ua JOIN scope s ON s.id=ua.user_id
            WHERE s.first_paid_at IS NULL
            GROUP BY ua.addon_key, ua.source ORDER BY activations DESC
        """)

        many("signups_by_day", """
            SELECT created_at::date AS day,
                   COUNT(*) AS signups,
                   COUNT(*) FILTER (WHERE first_paid_at IS NULL) AS never_paid_signups
            FROM scope GROUP BY created_at::date ORDER BY day
        """)

    return _encrypt(out)
