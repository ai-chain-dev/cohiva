"""Models for the Reverse Roulette → cooperative donation pipeline.

One :class:`RouletteDonation` row per on-chain TX hash that hit the
configured Cohiva charity address.  State machine:

    pending       — newly fetched from Supabase
    matched       — phone matched a single existing member; share created
    created       — new member created from phone; share created
    unattributed  — no phone or no match; awaiting manual attach
    ambiguous     — phone matched several members; awaiting manual review
    error         — processing failed (see ``error_message``)
    skipped       — explicitly ignored (e.g. cancelled / refunded TX)

Manual attach happens via the Django admin; once a member is set the
record is moved to ``matched`` and a :class:`geno.Share` is created.
"""
from __future__ import annotations

from django.db import models


class RouletteDonation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_MATCHED = "matched"
    STATUS_CREATED = "created"
    STATUS_UNATTRIBUTED = "unattributed"
    STATUS_AMBIGUOUS = "ambiguous"
    STATUS_ERROR = "error"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_MATCHED, "Matched (existing member)"),
        (STATUS_CREATED, "Created (new member)"),
        (STATUS_UNATTRIBUTED, "Unattributed"),
        (STATUS_AMBIGUOUS, "Ambiguous (multiple matches)"),
        (STATUS_ERROR, "Error"),
        (STATUS_SKIPPED, "Skipped"),
    )

    BET_DIRECT = "direct"
    BET_SPONSORED = "sponsored"
    BET_SOURCE_CHOICES = (
        (BET_DIRECT, "Direct"),
        (BET_SPONSORED, "Sponsored"),
    )

    transaction_hash = models.CharField(
        "Transaction Hash", max_length=66, unique=True, db_index=True
    )
    charity_address = models.CharField("Charity address", max_length=42)
    contract_address = models.CharField("Contract address", max_length=42)
    player_address = models.CharField("Player address", max_length=42)
    sponsor_address = models.CharField("Sponsor address", max_length=42, blank=True)
    fundraiser_address = models.CharField("Fundraiser address", max_length=42, blank=True)
    bet_source = models.CharField(
        "Bet source", max_length=16, choices=BET_SOURCE_CHOICES, default=BET_DIRECT
    )
    bet_amount_raw = models.DecimalField(
        "Bet amount (raw token units)", max_digits=78, decimal_places=0
    )
    charity_amount_raw = models.DecimalField(
        "Donation amount (raw token units)", max_digits=78, decimal_places=0
    )
    donation_amount = models.DecimalField(
        "Donation amount (decimal)", max_digits=38, decimal_places=18
    )
    won = models.BooleanField("Was win", default=False)
    block_number = models.BigIntegerField("Block number")
    block_timestamp = models.DateTimeField("Block timestamp")

    phone_e164 = models.CharField("Phone (E.164)", max_length=32, blank=True)
    member = models.ForeignKey(
        "geno.Member",
        verbose_name="Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reverse_roulette_donations",
    )
    share = models.ForeignKey(
        "geno.Share",
        verbose_name="Share",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reverse_roulette_donations",
    )

    status = models.CharField(
        "Status", max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    error_message = models.TextField("Error", blank=True)
    raw_event = models.JSONField("Raw event", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Reverse Roulette donation"
        verbose_name_plural = "Reverse Roulette donations"
        ordering = ["-block_timestamp"]
        indexes = [
            models.Index(fields=["status", "block_timestamp"]),
            models.Index(fields=["phone_e164"]),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_hash[:10]}… {self.donation_amount} → {self.member or self.phone_e164 or '?'}"
