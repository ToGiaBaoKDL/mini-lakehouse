"""Durable shadow delivery state. PostgreSQL owns claims, deadlines and retry clocks."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Literal, cast
from uuid import UUID, uuid4

from t0_trading.arbitration.selection import RealtimeSelection
from t0_trading.identity import sha256
from t0_trading.persistence import PostgresConnection

DeliveryStatus = Literal["SENT", "EXPIRED", "FAILED", "UNKNOWN", "PENDING"]


@dataclass(frozen=True)
class DeliveryClaim:
    selection_id: str
    token: UUID
    expires_at: datetime
    payload_json: str
    payload_sha256: str
    attempts: int

    def selection(self) -> RealtimeSelection:
        value = RealtimeSelection.model_validate_json(self.payload_json)
        if (
            value.arbitration.sha256 != self.selection_id
            or sha256(value.canonical_bytes()) != self.payload_sha256
        ):
            raise ValueError("shadow delivery checksum mismatch")
        return value


class ShadowDeliveryRepository:
    def __init__(self, connection: PostgresConnection) -> None:
        if not connection.autocommit:
            raise ValueError("delivery repository owns explicit transactions")
        self._connection = connection

    def _lock_control(self, *, skip_locked: bool = False) -> bool:
        row = self._connection.execute(
            "SELECT singleton FROM shadow_delivery_control WHERE singleton FOR UPDATE"
            + (" SKIP LOCKED" if skip_locked else ""),
            (),
        ).fetchone()
        if row is None:
            if (
                skip_locked
                and self._connection.execute(
                    "SELECT singleton FROM shadow_delivery_control WHERE singleton", ()
                ).fetchone()
                is not None
            ):
                return False
            raise RuntimeError("shadow delivery control is missing; apply PostgreSQL bootstrap")
        return True

    def invalidate(self, at: datetime) -> None:
        """Advance the durable disconnect watermark, even without another candidate."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("disconnect clock must be aware")
        with self._connection.transaction():
            self._lock_control()
            self._connection.execute(
                "UPDATE shadow_delivery_control SET invalidated_through = "
                "GREATEST(invalidated_through, %s) WHERE singleton",
                (at,),
            )
            self._connection.execute(
                "UPDATE shadow_deliveries SET status = 'EXPIRED', "
                "error_code = 'CAPTURE_DISCONNECTED', updated_at = clock_timestamp() "
                "WHERE status = 'PENDING' AND decision_at <= "
                "(SELECT invalidated_through FROM shadow_delivery_control WHERE singleton)",
                (),
            )

    def enqueue(self, selection: RealtimeSelection, *, lifetime: timedelta) -> str:
        if lifetime <= timedelta(0):
            raise ValueError("delivery lifetime must be positive")
        selection = RealtimeSelection.model_validate_json(selection.canonical_bytes())
        candidate = selection.candidate
        key = selection.arbitration.sha256
        digest = sha256(selection.canonical_bytes())
        expires = candidate.decision_at + lifetime
        with self._connection.transaction():
            self._lock_control()
            self._connection.execute(
                "INSERT INTO shadow_deliveries "
                "(selection_id, symbol, decision_at, expires_at, "
                "payload_json, payload_sha256, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s <= clock_timestamp() OR %s <= "
                "(SELECT invalidated_through FROM shadow_delivery_control WHERE singleton) "
                "THEN 'EXPIRED' "
                "WHEN %s > clock_timestamp() THEN 'FAILED' ELSE 'PENDING' END) "
                "ON CONFLICT DO NOTHING",
                (
                    key,
                    candidate.symbol,
                    candidate.decision_at,
                    expires,
                    selection.canonical_bytes().decode(),
                    digest,
                    expires,
                    candidate.decision_at,
                    candidate.decision_at,
                ),
            )
            row = self._connection.execute(
                "SELECT selection_id, payload_sha256, expires_at FROM shadow_deliveries "
                "WHERE symbol = %s AND decision_at = %s",
                (candidate.symbol, candidate.decision_at),
            ).fetchone()
            if row != (key, digest, expires):
                raise ValueError("conflicting durable shadow selection or deadline")
        return key

    def claim(self, *, lease: timedelta) -> DeliveryClaim | None:
        if lease <= timedelta(0):
            raise ValueError("delivery lease must be positive")
        with self._connection.transaction():
            # Single configured destination: serialize claims, not external network I/O.
            if not self._lock_control(skip_locked=True):
                return None
            # A prior process may have sent before losing its acknowledgement. Never resend it.
            self._connection.execute(
                "UPDATE shadow_deliveries SET status = 'UNKNOWN', lease_token = NULL, "
                "lease_until = NULL, error_code = 'LEASE_LOST', updated_at = clock_timestamp() "
                "WHERE status = 'SENDING' AND lease_until <= clock_timestamp()",
                (),
            )
            self._connection.execute(
                "UPDATE shadow_deliveries SET status = 'EXPIRED', updated_at = clock_timestamp() "
                "WHERE status = 'PENDING' AND expires_at <= clock_timestamp()",
                (),
            )
            token = uuid4()
            row = self._connection.execute(
                "WITH next AS (SELECT selection_id FROM shadow_deliveries "
                "WHERE status = 'PENDING' AND available_at <= clock_timestamp() "
                "AND decision_at <= clock_timestamp() AND expires_at > clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM shadow_deliveries WHERE status = 'SENDING') "
                "AND EXISTS (SELECT 1 FROM shadow_delivery_control WHERE singleton "
                "AND cooldown_until <= clock_timestamp() "
                "AND (invalidated_through IS NULL OR decision_at > invalidated_through)) "
                "ORDER BY decision_at, selection_id FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE shadow_deliveries d SET status = 'SENDING', attempts = attempts + 1, "
                "lease_token = %s, lease_until = clock_timestamp() + %s, "
                "updated_at = clock_timestamp() FROM next WHERE d.selection_id = next.selection_id "
                "RETURNING d.selection_id, d.expires_at, d.payload_json, "
                "d.payload_sha256, d.attempts",
                (token, lease),
            ).fetchone()
        if row is None:
            return None
        key, expires, payload, digest, attempts = row
        if not (
            isinstance(key, str)
            and isinstance(expires, datetime)
            and isinstance(payload, str)
            and isinstance(digest, str)
            and isinstance(attempts, int)
        ):
            raise ValueError("invalid delivery claim row")
        return DeliveryClaim(key, token, expires, payload, digest, attempts)

    def may_send(self, claim: DeliveryClaim, *, timeout: timedelta) -> bool:
        """Reserve the send window using DB time minus the query round-trip duration."""
        started = monotonic()
        row = self._connection.execute(
            "SELECT EXTRACT(EPOCH FROM LEAST(d.expires_at, d.lease_until) - clock_timestamp()) "
            "FROM shadow_deliveries d CROSS JOIN shadow_delivery_control c "
            "WHERE c.singleton AND d.selection_id = %s AND d.lease_token = %s "
            "AND d.status = 'SENDING' AND c.cooldown_until <= clock_timestamp() "
            "AND (c.invalidated_through IS NULL OR d.decision_at > c.invalidated_through)",
            (claim.selection_id, claim.token),
        ).fetchone()
        if row is None:
            return False
        remaining = row[0]
        if not isinstance(remaining, (Decimal, float, int)):
            raise ValueError("invalid database send window")
        return float(remaining) - (monotonic() - started) > timeout.total_seconds()

    def finish(
        self,
        claim: DeliveryClaim,
        status: DeliveryStatus,
        *,
        error_code: str | None = None,
        provider_message_id: str | None = None,
        retry_after: timedelta = timedelta(0),
    ) -> DeliveryStatus:
        if retry_after < timedelta(0):
            raise ValueError("retry delay cannot be negative")
        with self._connection.transaction():
            self._lock_control()
            if error_code == "RATE_LIMIT":
                self._connection.execute(
                    "UPDATE shadow_delivery_control SET cooldown_until = "
                    "GREATEST(cooldown_until, clock_timestamp() + %s) WHERE singleton",
                    (retry_after,),
                )
            return self._finish(claim, status, error_code, provider_message_id, retry_after)

    def _finish(
        self,
        claim: DeliveryClaim,
        status: DeliveryStatus,
        error_code: str | None,
        provider_message_id: str | None,
        retry_after: timedelta,
    ) -> DeliveryStatus:
        updated = self._connection.execute(
            "UPDATE shadow_deliveries SET status = CASE "
            "WHEN %s = 'PENDING' AND (expires_at <= clock_timestamp() + %s OR decision_at <= "
            "(SELECT invalidated_through FROM shadow_delivery_control WHERE singleton)) "
            "THEN 'EXPIRED' "
            "ELSE %s END, available_at = clock_timestamp() + %s, lease_token = NULL, "
            "lease_until = NULL, error_code = %s, provider_message_id = %s, "
            "updated_at = clock_timestamp() WHERE selection_id = %s AND lease_token = %s "
            "AND status = 'SENDING' AND lease_until > clock_timestamp() RETURNING status",
            (
                status,
                retry_after,
                status,
                retry_after,
                error_code,
                provider_message_id,
                claim.selection_id,
                claim.token,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("shadow delivery lease lost; do not repeat external send")
        row = updated.fetchone()
        if row is None or row[0] not in {"SENT", "EXPIRED", "FAILED", "UNKNOWN", "PENDING"}:
            raise ValueError("invalid delivery completion status")
        return cast(DeliveryStatus, row[0])
