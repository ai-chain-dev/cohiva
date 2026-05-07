from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("geno", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RouletteDonation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "transaction_hash",
                    models.CharField(
                        db_index=True,
                        max_length=66,
                        unique=True,
                        verbose_name="Transaction Hash",
                    ),
                ),
                ("charity_address", models.CharField(max_length=42, verbose_name="Charity address")),
                ("contract_address", models.CharField(max_length=42, verbose_name="Contract address")),
                ("player_address", models.CharField(max_length=42, verbose_name="Player address")),
                (
                    "sponsor_address",
                    models.CharField(blank=True, max_length=42, verbose_name="Sponsor address"),
                ),
                (
                    "fundraiser_address",
                    models.CharField(blank=True, max_length=42, verbose_name="Fundraiser address"),
                ),
                (
                    "bet_source",
                    models.CharField(
                        choices=[("direct", "Direct"), ("sponsored", "Sponsored")],
                        default="direct",
                        max_length=16,
                        verbose_name="Bet source",
                    ),
                ),
                (
                    "bet_amount_raw",
                    models.DecimalField(
                        decimal_places=0, max_digits=78, verbose_name="Bet amount (raw token units)"
                    ),
                ),
                (
                    "charity_amount_raw",
                    models.DecimalField(
                        decimal_places=0,
                        max_digits=78,
                        verbose_name="Donation amount (raw token units)",
                    ),
                ),
                (
                    "donation_amount",
                    models.DecimalField(
                        decimal_places=18, max_digits=38, verbose_name="Donation amount (decimal)"
                    ),
                ),
                ("won", models.BooleanField(default=False, verbose_name="Was win")),
                ("block_number", models.BigIntegerField(verbose_name="Block number")),
                ("block_timestamp", models.DateTimeField(verbose_name="Block timestamp")),
                (
                    "phone_e164",
                    models.CharField(blank=True, max_length=32, verbose_name="Phone (E.164)"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("matched", "Matched (existing member)"),
                            ("created", "Created (new member)"),
                            ("unattributed", "Unattributed"),
                            ("ambiguous", "Ambiguous (multiple matches)"),
                            ("error", "Error"),
                            ("skipped", "Skipped"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                ("error_message", models.TextField(blank=True, verbose_name="Error")),
                ("raw_event", models.JSONField(blank=True, null=True, verbose_name="Raw event")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "member",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="reverse_roulette_donations",
                        to="geno.member",
                        verbose_name="Member",
                    ),
                ),
                (
                    "share",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="reverse_roulette_donations",
                        to="geno.share",
                        verbose_name="Share",
                    ),
                ),
            ],
            options={
                "verbose_name": "Reverse Roulette donation",
                "verbose_name_plural": "Reverse Roulette donations",
                "ordering": ["-block_timestamp"],
            },
        ),
        migrations.AddIndex(
            model_name="roulettedonation",
            index=models.Index(
                fields=["status", "block_timestamp"],
                name="rr_donation_status_block_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="roulettedonation",
            index=models.Index(fields=["phone_e164"], name="rr_donation_phone_idx"),
        ),
    ]
