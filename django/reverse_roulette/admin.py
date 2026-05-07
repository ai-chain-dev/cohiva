from django.contrib import admin

from .models import RouletteDonation
from .services import (
    NewMemberSpec,
    create_member_with_phone,
    create_share,
    update_link_status,
)


@admin.register(RouletteDonation)
class RouletteDonationAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_hash",
        "status",
        "donation_amount",
        "phone_e164",
        "member",
        "block_timestamp",
    )
    list_filter = ("status", "bet_source", "won")
    search_fields = (
        "transaction_hash",
        "phone_e164",
        "player_address",
        "sponsor_address",
        "fundraiser_address",
    )
    readonly_fields = (
        "transaction_hash",
        "charity_address",
        "contract_address",
        "player_address",
        "sponsor_address",
        "fundraiser_address",
        "bet_source",
        "bet_amount_raw",
        "charity_amount_raw",
        "donation_amount",
        "won",
        "block_number",
        "block_timestamp",
        "raw_event",
        "share",
        "created_at",
        "updated_at",
        "processed_at",
    )
    actions = ("attach_to_member_field",)

    @admin.action(description="Re-process: book share for the assigned member")
    def attach_to_member_field(self, request, queryset):
        booked = 0
        skipped = 0
        for donation in queryset:
            if donation.member_id and not donation.share_id and donation.donation_amount > 0:
                share = create_share(
                    member=donation.member,
                    donation_amount=donation.donation_amount,
                    tx_hash=donation.transaction_hash,
                    when=donation.block_timestamp,
                )
                donation.share = share
                donation.status = RouletteDonation.STATUS_MATCHED
                donation.error_message = ""
                donation.save(
                    update_fields=["share", "status", "error_message", "updated_at"]
                )
                update_link_status(
                    donation.transaction_hash,
                    status=donation.status,
                    member_id=donation.member_id,
                    share_id=share.pk,
                )
                booked += 1
            else:
                skipped += 1
        self.message_user(
            request, f"Booked {booked} share(s); skipped {skipped} record(s)."
        )
