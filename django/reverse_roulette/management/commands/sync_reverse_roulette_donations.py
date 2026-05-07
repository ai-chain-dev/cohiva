from django.core.management.base import BaseCommand

from reverse_roulette import services


class Command(BaseCommand):
    help = (
        "Pull pending Reverse Roulette donations from Supabase, match phones "
        "to members (creating new ones if needed), and book member shares."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and upsert RouletteDonation rows but do not create members or shares.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        result = services.sync_once(dry_run=dry_run)
        self.stdout.write(
            self.style.SUCCESS(
                f"sync complete (dry_run={dry_run}): "
                f"pulled={result['pulled']} processed={result['processed']} "
                f"errors={result['errors']}"
            )
        )
