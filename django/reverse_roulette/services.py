"""Core service layer for the Reverse Roulette → Cohiva integration.

Pull pipeline:

1. :func:`fetch_pending_links` queries Supabase REST for ``cohiva_bet_links``
   rows in state ``pending`` whose blocks are deep enough.
2. :func:`process_link` is invoked per row.  It:

   - upserts a :class:`reverse_roulette.models.RouletteDonation`,
   - normalizes the phone to E.164,
   - tries to match it against existing :class:`geno.Member` rows
     across all four phone CharFields *and* an optional historic-phone
     ``MemberAttributeType``,
   - creates a new :class:`geno.Member` if no match,
   - records a :class:`geno.Share` of the configured ShareType,
   - PATCHes the Supabase row with the resulting status.

Idempotency is ensured by ``transaction_hash`` being unique on both sides.
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

try:
    import phonenumbers
except ImportError:  # pragma: no cover — surfaced at runtime if missing
    phonenumbers = None  # type: ignore[assignment]

from .models import RouletteDonation

logger = logging.getLogger(__name__)


# ─── Configuration helpers ────────────────────────────────────────────────────

def _required(name: str) -> str:
    value = getattr(settings, name, None)
    if not value:
        raise RuntimeError(
            f"Setting {name} is required for the Reverse Roulette integration "
            "but is not configured."
        )
    return value


def _supabase_base() -> str:
    return _required("REVERSE_ROULETTE_SUPABASE_URL").rstrip("/")


def _supabase_headers() -> dict:
    key = _required("REVERSE_ROULETTE_SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _token_decimals(contract_address: str) -> int:
    """Decimals for the bet token; configurable per contract address."""
    mapping = getattr(settings, "REVERSE_ROULETTE_TOKEN_DECIMALS", {}) or {}
    key = (contract_address or "").lower()
    if key in mapping:
        return int(mapping[key])
    # Default: native RON (18 decimals).
    return int(getattr(settings, "REVERSE_ROULETTE_DEFAULT_DECIMALS", 18))


def _min_confirmations() -> int:
    return int(getattr(settings, "REVERSE_ROULETTE_MIN_CONFIRMATIONS", 12))


# ─── Phone normalization & matching ───────────────────────────────────────────

def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Return the E.164 form of *raw* or ``None`` if it can't be parsed."""
    if not raw:
        return None
    if phonenumbers is None:
        # Fallback: keep digits and a leading + if present.
        cleaned = "".join(ch for ch in str(raw) if ch.isdigit() or ch == "+")
        if cleaned.startswith("+") and len(cleaned) >= 8:
            return cleaned
        return None
    try:
        parsed = phonenumbers.parse(str(raw), None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _normalized_phone_candidates(member) -> Iterable[str]:
    addr = getattr(member, "name", None)
    if addr is None:
        return ()
    candidates = (
        getattr(addr, "telephone", "") or "",
        getattr(addr, "mobile", "") or "",
        getattr(addr, "telephoneOffice", "") or "",
        getattr(addr, "telephoneOffice2", "") or "",
    )
    return tuple(c for c in (normalize_phone(c) for c in candidates) if c)


def find_members_by_phone(phone_e164: str):
    """Return the (possibly empty) list of distinct members whose any phone
    field — or historic-phone attribute — matches *phone_e164*.
    """
    if not phone_e164:
        return []

    from geno.models import Member, MemberAttribute  # local import: avoid app-loading order issues

    matches = set()

    # Iterate members that have any phone set at all.  Bounded by total
    # member count which is small (cooperative scale).
    qs = Member.objects.exclude(
        name__telephone="",
        name__mobile="",
        name__telephoneOffice="",
        name__telephoneOffice2="",
    ).select_related("name")
    for member in qs.iterator():
        if phone_e164 in _normalized_phone_candidates(member):
            matches.add(member.pk)

    historic_attr_id = getattr(settings, "REVERSE_ROULETTE_HISTORIC_PHONE_ATTR_ID", None)
    if historic_attr_id:
        for ma in MemberAttribute.objects.filter(
            attribute_type_id=historic_attr_id
        ).select_related("member"):
            if normalize_phone(ma.value) == phone_e164:
                matches.add(ma.member_id)

    if not matches:
        return []
    return list(Member.objects.filter(pk__in=matches).select_related("name"))


# ─── Member creation ──────────────────────────────────────────────────────────

@dataclass
class NewMemberSpec:
    phone_e164: str
    placeholder_last_name: str = "ReverseRoulette"
    placeholder_first_name: str = "Donor"


def create_member_with_phone(spec: NewMemberSpec):
    """Create a brand-new :class:`geno.Member` carrying just the donor's
    mobile phone.  The cooperative admin is expected to fill in the rest
    of the address later.
    """
    from geno.models import Address, Member

    address = Address.objects.create(
        name=spec.placeholder_last_name,
        first_name=spec.placeholder_first_name,
        mobile=spec.phone_e164,
    )
    return Member.objects.create(
        name=address,
        date_join=timezone.localdate(),
        notes=(
            "Auto-created via Reverse Roulette donation flow. "
            "Please complete address details."
        ),
    )


# ─── Share booking ────────────────────────────────────────────────────────────

def _share_type():
    from geno.models import ShareType

    pk = getattr(settings, "REVERSE_ROULETTE_SHARETYPE_ID", None)
    if not pk:
        raise RuntimeError(
            "REVERSE_ROULETTE_SHARETYPE_ID is not configured. Create a "
            "dedicated ShareType (e.g. 'ReverseRoulette donation') and set "
            "its primary key in settings."
        )
    return ShareType.objects.get(pk=pk)


def create_share(member, donation_amount: Decimal, tx_hash: str, when: _dt.datetime):
    """Create a :class:`geno.Share` row of the configured ShareType linked
    to *member*'s address.  Booked as already paid (state='bezahlt').
    """
    from geno.models import Share

    share_type = _share_type()
    # Share.value is DecimalField(max_digits=10, decimal_places=2); round
    # the on-chain donation accordingly (currency-style).
    rounded_value = donation_amount.quantize(Decimal("0.01"))
    share = Share.objects.create(
        name=member.name,  # Address row
        share_type=share_type,
        state="bezahlt",
        date=when.date() if hasattr(when, "date") else when,
        quantity=Decimal(1),
        value=rounded_value,
    )
    # Optionally post to accounting if explicitly enabled.
    if getattr(settings, "REVERSE_ROULETTE_ENABLE_ACCOUNTING_BOOKING", False):
        try:
            _post_to_accounting(share, member, donation_amount, tx_hash, when)
        except Exception as exc:  # pragma: no cover — accounting backend specific
            logger.exception("Accounting posting failed for tx %s: %s", tx_hash, exc)
    return share


def _post_to_accounting(share, member, amount, tx_hash, when):
    """Hook for posting the donation to the GnuCash/CashCtrl accounting
    backend.  Disabled by default because the income/equity account
    mapping needs cooperative-bookkeeper input and varies per deployment.

    To enable: set ``REVERSE_ROULETTE_ENABLE_ACCOUNTING_BOOKING=True`` in
    your settings AND override this function in a small custom Django app
    (or replace at import time) with the deployment-specific posting
    logic against ``finance.accounting.manager.AccountingManager``.
    """
    raise NotImplementedError(
        "Reverse Roulette accounting posting is enabled but no implementation "
        "is wired up. Override reverse_roulette.services._post_to_accounting "
        "with deployment-specific income/equity account mapping, or set "
        "REVERSE_ROULETTE_ENABLE_ACCOUNTING_BOOKING back to False."
    )


# ─── Supabase I/O ─────────────────────────────────────────────────────────────

def fetch_pending_links(latest_block: Optional[int] = None) -> list[dict]:
    """Pull pending ``cohiva_bet_links`` rows, optionally gated by
    confirmations relative to *latest_block*.
    """
    base = _supabase_base()
    headers = _supabase_headers()
    params = {
        "cohiva_status": "eq.pending",
        "select": "*",
        "order": "block_timestamp.asc",
        "limit": "200",
    }
    if latest_block is not None:
        max_block = max(int(latest_block) - _min_confirmations(), 0)
        params["block_number"] = f"lte.{max_block}"

    resp = requests.get(
        f"{base}/rest/v1/cohiva_bet_links",
        headers=headers,
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() or []


def update_link_status(
    tx_hash: str,
    *,
    status: str,
    member_id: Optional[int] = None,
    share_id: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """PATCH a ``cohiva_bet_links`` row in Supabase with the outcome."""
    payload = {
        "cohiva_status": status,
        "cohiva_member_id": member_id,
        "cohiva_share_id": share_id,
        "cohiva_error": error,
        "cohiva_synced_at": timezone.now().isoformat(),
    }
    resp = requests.patch(
        f"{_supabase_base()}/rest/v1/cohiva_bet_links",
        headers=_supabase_headers(),
        params={"transaction_hash": f"eq.{tx_hash}"},
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        logger.warning(
            "Failed to PATCH cohiva_bet_links %s: %s %s",
            tx_hash,
            resp.status_code,
            resp.text,
        )


# ─── Orchestration ────────────────────────────────────────────────────────────

def _to_decimal_amount(raw: str, contract_address: str) -> Decimal:
    decimals = _token_decimals(contract_address)
    return (Decimal(str(raw)) / (Decimal(10) ** decimals)).quantize(
        Decimal("0.000000000000000001")
    )


def _parse_block_timestamp(value) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    s = str(value).replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return timezone.now()


def upsert_donation_from_link(link: dict) -> RouletteDonation:
    """Create or refresh a :class:`RouletteDonation` row from a Supabase
    ``cohiva_bet_links`` payload.  Pre-existing successful rows are not
    altered.
    """
    tx_hash = str(link["transaction_hash"]).lower()
    contract_address = str(link.get("contract_address") or "")
    raw_charity = str(link.get("charity_amount") or "0")
    donation_amount = _to_decimal_amount(raw_charity, contract_address)
    block_ts = _parse_block_timestamp(link.get("block_timestamp"))

    defaults = dict(
        charity_address=str(link.get("charity_address") or ""),
        contract_address=contract_address,
        player_address=str(link.get("player_address") or ""),
        sponsor_address=str(link.get("sponsor_address") or ""),
        fundraiser_address=str(link.get("fundraiser_address") or ""),
        bet_source=str(link.get("bet_source") or RouletteDonation.BET_DIRECT),
        bet_amount_raw=Decimal(str(link.get("bet_amount") or "0")),
        charity_amount_raw=Decimal(raw_charity),
        donation_amount=donation_amount,
        won=bool(link.get("won")),
        block_number=int(link.get("block_number") or 0),
        block_timestamp=block_ts,
        phone_e164=normalize_phone(link.get("phone_e164")) or "",
        raw_event=link,
    )
    obj, _ = RouletteDonation.objects.update_or_create(
        transaction_hash=tx_hash, defaults=defaults
    )
    return obj


@transaction.atomic
def process_donation(donation: RouletteDonation) -> RouletteDonation:
    """Resolve member + create share for a single donation.  Idempotent:
    rows in non-``pending`` state are returned untouched.
    """
    if donation.status != RouletteDonation.STATUS_PENDING:
        return donation
    if donation.donation_amount <= 0:
        donation.status = RouletteDonation.STATUS_SKIPPED
        donation.error_message = "Zero donation amount"
        donation.processed_at = timezone.now()
        donation.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        return donation

    try:
        if not donation.phone_e164:
            donation.status = RouletteDonation.STATUS_UNATTRIBUTED
            donation.error_message = "No phone number recorded for this donation"
            donation.processed_at = timezone.now()
            donation.save(
                update_fields=["status", "error_message", "processed_at", "updated_at"]
            )
            return donation

        candidates = find_members_by_phone(donation.phone_e164)
        if len(candidates) > 1:
            donation.status = RouletteDonation.STATUS_AMBIGUOUS
            donation.error_message = (
                f"Phone matched {len(candidates)} members "
                f"(IDs: {sorted(m.pk for m in candidates)})"
            )
            donation.processed_at = timezone.now()
            donation.save(
                update_fields=["status", "error_message", "processed_at", "updated_at"]
            )
            return donation

        if candidates:
            member = candidates[0]
            new_status = RouletteDonation.STATUS_MATCHED
        else:
            member = create_member_with_phone(NewMemberSpec(phone_e164=donation.phone_e164))
            new_status = RouletteDonation.STATUS_CREATED

        share = create_share(
            member=member,
            donation_amount=donation.donation_amount,
            tx_hash=donation.transaction_hash,
            when=donation.block_timestamp,
        )
        donation.member = member
        donation.share = share
        donation.status = new_status
        donation.error_message = ""
        donation.processed_at = timezone.now()
        donation.save(
            update_fields=[
                "member",
                "share",
                "status",
                "error_message",
                "processed_at",
                "updated_at",
            ]
        )
        return donation
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to process donation %s", donation.transaction_hash)
        donation.status = RouletteDonation.STATUS_ERROR
        donation.error_message = str(exc)[:2000]
        donation.processed_at = timezone.now()
        donation.save(
            update_fields=["status", "error_message", "processed_at", "updated_at"]
        )
        raise


def sync_once(dry_run: bool = False) -> dict:
    """Pull pending links, process each, push status back.  Returns counts.

    Safe to call repeatedly; uniqueness on ``transaction_hash`` guarantees
    no double-booking.
    """
    counters = {"pulled": 0, "processed": 0, "errors": 0}
    links = fetch_pending_links()
    counters["pulled"] = len(links)

    for link in links:
        tx_hash = str(link.get("transaction_hash") or "").lower()
        if not tx_hash:
            continue
        try:
            donation = upsert_donation_from_link(link)
            if dry_run:
                logger.info(
                    "[DRY-RUN] would process %s phone=%s amount=%s",
                    donation.transaction_hash,
                    donation.phone_e164,
                    donation.donation_amount,
                )
                continue
            try:
                processed = process_donation(donation)
            except Exception:
                counters["errors"] += 1
                update_link_status(
                    tx_hash,
                    status=RouletteDonation.STATUS_ERROR,
                    error="processing failed; see Cohiva logs",
                )
                continue
            update_link_status(
                tx_hash,
                status=processed.status,
                member_id=processed.member_id,
                share_id=processed.share_id,
                error=processed.error_message or None,
            )
            counters["processed"] += 1
        except Exception:
            logger.exception("Unhandled error for link %s", link.get("transaction_hash"))
            counters["errors"] += 1

    return counters
