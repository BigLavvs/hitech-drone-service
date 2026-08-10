from django.core.management.base import BaseCommand

from apps.access_control.demo_access import DEMO_USER_SPECS, issue_demo_token_for_spec


class Command(BaseCommand):
    help = "Issue a short-lived development-only RS256 JWT for a seeded demo role."

    def add_arguments(self, parser):
        parser.add_argument(
            "--role",
            choices=sorted(DEMO_USER_SPECS.keys()),
            required=True,
            help="Seeded demo role to issue a token for.",
        )

    def handle(self, *args, **options):
        spec = DEMO_USER_SPECS[options["role"]]
        token = issue_demo_token_for_spec(spec=spec)
        self.stdout.write(token)
