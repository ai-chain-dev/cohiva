"""Tests for the Reverse Roulette → Cohiva integration.

These tests do *not* contact Supabase or the chain.  They feed crafted
Supabase payloads into the service layer and verify member matching,
share creation and idempotency behaviour.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from geno.models import Address, Member, MemberAttribute, MemberAttributeType, ShareType

from reverse_roulette import services
from reverse_roulette.models import RouletteDonation


def _make_link(
    *,
    tx_hash: str = "0x" + "ab" * 32,
    phone: str = "+41791234567",
    amount: str = "1000000000000000000",  # 1 token, 18 decimals
    contract: str = "0x" + "11" * 20,
    bet_source: str = "direct",
):
    return {
        "transaction_hash": tx_hash,
        "charity_address": "0x" + "22" * 20,
        "contract_address": contract,
        "player_address": "0x" + "33" * 20,
        "sponsor_address": "",
        "fundraiser_address": "",
        "bet_source": bet_source,
        "bet_amount": amount,
        "charity_amount": amount,
        "won": False,
        "block_number": 100,
        "block_timestamp": "2025-01-15T12:00:00+00:00",
        "phone_e164": phone,
    }


class PhoneNormalizationTests(TestCase):
    def test_e164_stays_e164(self):
        self.assertEqual(services.normalize_phone("+41 79 123 45 67"), "+41791234567")

    def test_invalid_returns_none(self):
        self.assertIsNone(services.normalize_phone("not a number"))

    def test_empty_returns_none(self):
        self.assertIsNone(services.normalize_phone(""))
        self.assertIsNone(services.normalize_phone(None))


class FindMembersByPhoneTests(TestCase):
    def setUp(self):
        self.addr1 = Address.objects.create(
            name="Tester", first_name="One", mobile="+41 79 123 45 67"
        )
        self.member1 = Member.objects.create(
            name=self.addr1, date_join=_dt.date(2020, 1, 1)
        )

    def test_match_by_mobile(self):
        result = services.find_members_by_phone("+41791234567")
        self.assertEqual([m.pk for m in result], [self.member1.pk])

    def test_no_match_returns_empty(self):
        self.assertEqual(services.find_members_by_phone("+19999999999"), [])

    def test_match_by_telephone_office(self):
        addr = Address.objects.create(
            name="Office", first_name="Two", telephoneOffice="+41 44 555 66 77"
        )
        m = Member.objects.create(name=addr, date_join=_dt.date(2020, 1, 1))
        result = services.find_members_by_phone("+41445556677")
        self.assertEqual([x.pk for x in result], [m.pk])

    def test_ambiguous_returns_multiple(self):
        addr2 = Address.objects.create(
            name="Tester", first_name="Two", telephone="+41 79 123 45 67"
        )
        member2 = Member.objects.create(name=addr2, date_join=_dt.date(2020, 1, 1))
        result = services.find_members_by_phone("+41791234567")
        self.assertEqual(
            sorted(m.pk for m in result), sorted([self.member1.pk, member2.pk])
        )

    def test_match_by_historic_attribute(self):
        attr_type = MemberAttributeType.objects.create(
            name="Historische Telefonnummer", kind="text"
        )
        addr = Address.objects.create(name="Old", first_name="Phone")
        member = Member.objects.create(name=addr, date_join=_dt.date(2020, 1, 1))
        MemberAttribute.objects.create(
            member=member, attribute_type=attr_type, value="+41 22 999 88 77"
        )
        with override_settings(REVERSE_ROULETTE_HISTORIC_PHONE_ATTR_ID=attr_type.pk):
            result = services.find_members_by_phone("+41229998877")
        self.assertEqual([x.pk for x in result], [member.pk])


class _ServiceTestBase(TestCase):
    """Provides a configured ShareType and patches Supabase PATCH."""

    @classmethod
    def setUpTestData(cls):
        cls.share_type = ShareType.objects.create(
            name="ReverseRoulette",
            description="Auto-booked Reverse Roulette donations",
        )

    def setUp(self):
        # Patch the outbound Supabase PATCH so tests don't talk to the network.
        self._patcher = patch.object(services, "update_link_status")
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # Patch the inbound fetch likewise (sync_once not exercised here unless mocked).
        self._fetch_patcher = patch.object(services, "fetch_pending_links", return_value=[])
        self._fetch_patcher.start()
        self.addCleanup(self._fetch_patcher.stop)


@override_settings()
class ProcessDonationTests(_ServiceTestBase):
    def setUp(self):
        super().setUp()
        # Bind ShareType setting at runtime.
        self._settings_patcher = override_settings(
            REVERSE_ROULETTE_SHARETYPE_ID=self.share_type.pk
        )
        self._settings_patcher.enable()
        self.addCleanup(self._settings_patcher.disable)

    def test_creates_member_when_no_match(self):
        link = _make_link()
        donation = services.upsert_donation_from_link(link)
        services.process_donation(donation)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RouletteDonation.STATUS_CREATED)
        self.assertIsNotNone(donation.member_id)
        self.assertIsNotNone(donation.share_id)
        self.assertEqual(donation.member.name.mobile, "+41791234567")
        self.assertEqual(donation.share.value, Decimal("1.00"))

    def test_matches_existing_member(self):
        addr = Address.objects.create(name="X", first_name="Y", mobile="+41 79 123 45 67")
        existing = Member.objects.create(name=addr, date_join=_dt.date(2020, 1, 1))
        link = _make_link()
        donation = services.upsert_donation_from_link(link)
        services.process_donation(donation)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RouletteDonation.STATUS_MATCHED)
        self.assertEqual(donation.member_id, existing.pk)

    def test_unattributed_when_no_phone(self):
        link = _make_link(phone="")
        donation = services.upsert_donation_from_link(link)
        services.process_donation(donation)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RouletteDonation.STATUS_UNATTRIBUTED)
        self.assertIsNone(donation.member_id)
        self.assertIsNone(donation.share_id)

    def test_ambiguous_when_multiple_matches(self):
        for i in range(2):
            addr = Address.objects.create(
                name=f"Dup{i}", first_name="P", mobile="+41 79 123 45 67"
            )
            Member.objects.create(name=addr, date_join=_dt.date(2020, 1, 1))
        link = _make_link()
        donation = services.upsert_donation_from_link(link)
        services.process_donation(donation)
        donation.refresh_from_db()
        self.assertEqual(donation.status, RouletteDonation.STATUS_AMBIGUOUS)
        self.assertIsNone(donation.share_id)

    def test_idempotent_reprocess(self):
        link = _make_link()
        donation = services.upsert_donation_from_link(link)
        services.process_donation(donation)
        services.process_donation(donation)  # second call must be a no-op
        self.assertEqual(
            RouletteDonation.objects.filter(transaction_hash=link["transaction_hash"]).count(),
            1,
        )
        self.assertEqual(
            donation.member.reverse_roulette_donations.count(), 1
        )

    def test_replaying_link_does_not_duplicate(self):
        link = _make_link()
        d1 = services.upsert_donation_from_link(link)
        services.process_donation(d1)
        d2 = services.upsert_donation_from_link(link)
        self.assertEqual(d1.pk, d2.pk)


@override_settings()
class TokenDecimalsTests(_ServiceTestBase):
    def setUp(self):
        super().setUp()
        self._settings_patcher = override_settings(
            REVERSE_ROULETTE_SHARETYPE_ID=self.share_type.pk,
            REVERSE_ROULETTE_TOKEN_DECIMALS={
                "0x" + "11" * 20: 18,
                "0x" + "22" * 20: 6,
            },
        )
        self._settings_patcher.enable()
        self.addCleanup(self._settings_patcher.disable)

    def test_ron_18_decimals(self):
        link = _make_link(amount="2500000000000000000", contract="0x" + "11" * 20)
        donation = services.upsert_donation_from_link(link)
        self.assertEqual(donation.donation_amount, Decimal("2.500000000000000000"))

    def test_usdc_6_decimals(self):
        link = _make_link(
            tx_hash="0x" + "cd" * 32,
            amount="2500000",
            contract="0x" + "22" * 20,
        )
        donation = services.upsert_donation_from_link(link)
        self.assertEqual(donation.donation_amount, Decimal("2.500000000000000000"))
