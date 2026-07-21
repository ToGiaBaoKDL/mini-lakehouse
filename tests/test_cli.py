from mini_lakehouse.cli import build_parser


def test_curate_github_cli_accepts_one_archive_hour() -> None:
    arguments = build_parser().parse_args(
        [
            "curate",
            "github",
            "--hour",
            "2026-07-21T04:00:00Z",
        ]
    )

    assert arguments.command == "curate"
    assert arguments.product == "github"
    assert arguments.hour == "2026-07-21T04:00:00Z"
