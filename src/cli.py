"""Command-line application entry point."""
from __future__ import annotations

from .config import build_parser


def validate_mode(parser, repo: str, test_mode: bool) -> None:
    """Reject incompatible local-repository and URL mode combinations."""
    parsed_repo_is_url = "://" in repo or repo.startswith("file:")
    if test_mode and parsed_repo_is_url:
        parser.error(
            "при --test-mode true параметр "
            "--repo должен быть "
            "локальным каталогом"
        )
    if not test_mode and not parsed_repo_is_url:
        parser.error(
            "локальный каталог в --repo требует "
            "--test-mode true"
        )


def print_configuration(args) -> None:
    """Print every user-configurable option as a key-value pair."""
    values = {
        "package": args.package,
        "repo": args.repo,
        "test_mode": args.test_mode,
        "output": args.output,
        "ascii": args.ascii,
        "filter": args.filter if args.filter is not None else "",
    }
    for key, value in values.items():
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, validate configuration, and print its values."""
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_mode(parser, args.repo, args.test_mode)
    print_configuration(args)
    print("status=configuration_validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
