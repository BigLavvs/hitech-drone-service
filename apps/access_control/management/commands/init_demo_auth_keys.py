from django.core.management.base import BaseCommand

from apps.access_control.demo_access import ensure_demo_keypair


class Command(BaseCommand):
    help = "Create or reuse gitignored development-only RSA keys for local assessment demo JWTs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Replace any existing demo keypair with a new one.",
        )

    def handle(self, *args, **options):
        private_key_path, public_key_path = ensure_demo_keypair(rotate=options["rotate"])
        self.stdout.write(self.style.SUCCESS(f"Demo private key: {private_key_path}"))
        self.stdout.write(self.style.SUCCESS(f"Demo public key:  {public_key_path}"))
