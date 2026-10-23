"""Tests for CLI configuration parsing and validation."""
import contextlib
import io
import tempfile
import unittest

from dependency_visualizer.cli import main
from dependency_visualizer.config import build_parser


class ConfigTests(unittest.TestCase):
    """Verify successful configuration output and invalid arguments."""

    def test_valid_local_test_repository_prints_all_values(self):
        """A valid invocation prints each configurable value."""
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            arguments = [
                "--package", "demo", "--repo", directory,
                "--test-mode", "true", "--output", "graph.svg",
                "--ascii", "--filter", "dep",
            ]
            with contextlib.redirect_stdout(output):
                code = main(arguments)

            self.assertEqual(code, 0)
            expected_values = (
                "package=demo", "test_mode=True", "output=graph.svg",
                "ascii=True", "filter=dep",
                "status=configuration_validated",
            )
            for expected in expected_values:
                self.assertIn(expected, output.getvalue())

    def test_missing_required_argument(self):
        """Missing repository arguments cause argparse to exit with code 2."""
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(["--package", "demo"])
        self.assertEqual(error.exception.code, 2)

    def test_invalid_boolean(self):
        """Unknown boolean spellings are rejected."""
        arguments = [
            "--package", "demo", "--repo", "https://example.org",
            "--test-mode", "sometimes",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_invalid_package_name(self):
        """Package names containing spaces are rejected."""
        arguments = [
            "--package", "invalid name", "--repo", "https://example.org",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_invalid_repository_url(self):
        """Unsupported URL schemes are rejected."""
        arguments = [
            "--package", "demo", "--repo", "ftp://example.org/repo",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_output_parent_must_exist(self):
        """An output path with a missing parent directory is rejected."""
        arguments = [
            "--package", "demo", "--repo", "https://example.org",
            "--output", "missing-directory/graph.svg",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_missing_local_repository(self):
        """A path that does not exist is rejected."""
        arguments = [
            "--package", "demo", "--repo", "./no-such-repo",
            "--test-mode", "true",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_invalid_output_extension(self):
        """Unsupported output extensions are rejected."""
        arguments = [
            "--package", "demo", "--repo", "https://example.org",
            "--output", "graph.exe",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_empty_filter(self):
        """An explicitly empty filter is rejected."""
        arguments = [
            "--package", "demo", "--repo", "https://example.org",
            "--filter", "",
        ]
        with self.assertRaises(SystemExit) as error:
            build_parser().parse_args(arguments)
        self.assertEqual(error.exception.code, 2)

    def test_local_repo_requires_test_mode(self):
        """Local paths require the test mode flag."""
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as error:
                    main(["--package", "demo", "--repo", directory])
            self.assertEqual(error.exception.code, 2)
            self.assertIn(
                "требует --test-mode true", stderr.getvalue()
            )

    def test_url_rejected_in_test_mode(self):
        """URLs cannot be paired with the local test mode."""
        stderr = io.StringIO()
        arguments = [
            "--package", "demo", "--repo", "https://example.org",
            "--test-mode", "true",
        ]
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as error:
                main(arguments)
        self.assertEqual(error.exception.code, 2)
        self.assertIn(
            "должен быть локальным каталогом",
            stderr.getvalue()
        )


if __name__ == "__main__":
    unittest.main()
