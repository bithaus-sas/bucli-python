"""``scripts/saas_bump.py``: the pin of each SaaS moves to the new tag, and
the pull request carries that version's notes."""

# Standard library
import importlib.util
from pathlib import Path

# Third-party
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "saas_bump.py"
_spec = importlib.util.spec_from_file_location("saas_bump", SCRIPT)
saas_bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(saas_bump)

REQUIREMENTS = """\
# Billing: Bucli's SDK.
django==5.2.11
{pin}
requests==2.32.3
"""

GIT = "bucli[django] @ git+https://github.com/bithaus-sas/bucli-python@{ref}"

CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.5.0] - 2026-11-12

### Added
- Eventos `product.created` y `product.updated`.

## [0.4.0] - 2026-11-11

### Added
- `invoice.updated`.
"""


OLD_COMMIT = "cd556e658c973424c797ee0fca6a6dc8f97258dd"


class TestThePin:
    def test_a_pypi_pin_moves_to_the_new_version(self):
        text, previous = saas_bump.bump_pin(REQUIREMENTS.format(pin="bucli[django]==0.5.0"), "v0.6.0")
        assert previous == "v0.5.0"
        assert "\nbucli[django]==0.6.0\n" in text
        # The comment is left as it was; nothing else moves.
        assert "# Billing: Bucli's SDK." in text and "django==5.2.11" in text

    def test_a_git_pin_by_tag_moves_to_pypi(self):
        text, previous = saas_bump.bump_pin(REQUIREMENTS.format(pin=GIT.format(ref="v0.4.0")), "v0.6.0")
        assert previous == "v0.4.0"
        assert "\nbucli[django]==0.6.0\n" in text and "git+" not in text

    def test_a_git_pin_by_commit_moves_to_pypi(self):
        """Mailko pinned a commit: on PyPI a version cannot move, so it pins the version."""
        text, previous = saas_bump.bump_pin(REQUIREMENTS.format(pin=GIT.format(ref=OLD_COMMIT)), "v0.6.0")
        assert previous == OLD_COMMIT
        assert "\nbucli[django]==0.6.0\n" in text

    def test_never_back_to_an_older_version(self):
        """A hotfix of an older line (v0.4.1 after v0.5.0) leaves it."""
        text = REQUIREMENTS.format(pin="bucli[django]==0.5.0")
        assert saas_bump.bump_pin(text, "v0.4.1") == (text, None)
        assert saas_bump.bump_pin(REQUIREMENTS.format(pin="bucli==0.10.0"), "v0.9.0")[1] is None
        assert saas_bump.bump_pin(REQUIREMENTS.format(pin=GIT.format(ref="v0.10.0")), "v0.9.0")[1] is None

    def test_every_pin_moves_and_one_already_there_does_not_hide_the_other(self):
        text = "bucli==0.6.0\nbucli[django]==0.5.0\n"
        new_text, previous = saas_bump.bump_pin(text, "v0.6.0")
        assert previous == "v0.5.0" and new_text == "bucli==0.6.0\nbucli[django]==0.6.0\n"

    def test_already_there_is_nothing_to_do(self):
        text = REQUIREMENTS.format(pin="bucli[django]==0.6.0")
        assert saas_bump.bump_pin(text, "v0.6.0") == (text, None)

    def test_a_commented_pin_is_not_a_pin(self):
        with pytest.raises(saas_bump.NoPinError):
            saas_bump.bump_pin("# bucli[django]==0.1.0\n", "v0.5.0")

    def test_another_package_is_not_the_pin(self):
        with pytest.raises(saas_bump.NoPinError):
            saas_bump.bump_pin("bucli-tools==1.0.0\nmybucli==2.0.0\n", "v0.5.0")

    def test_the_owner_in_capitals_is_still_the_pin(self):
        text, previous = saas_bump.bump_pin(
            "bucli @ git+https://github.com/Bithaus-SAS/bucli-python@v0.4.0\n", "v0.5.0"
        )
        assert previous == "v0.4.0" and text == "bucli==0.5.0\n"

    def test_ssh_and_dot_git_pins_move_too_and_keep_their_comment(self):
        text, previous = saas_bump.bump_pin(
            "bucli[django] @ git+ssh://git@github.com/bithaus-sas/bucli-python.git@v0.1.0  # billing\n", "v0.5.0"
        )
        assert previous == "v0.1.0"
        assert text == "bucli[django]==0.5.0  # billing\n"

    def test_a_pre_or_post_release_is_not_half_rewritten(self):
        for pin in ("bucli[django]==0.5.0.post1", "bucli[django]==0.5.0rc1", "bucli[django]==0.5.1a1"):
            with pytest.raises(saas_bump.NoPinError):
                saas_bump.bump_pin(pin + "\n", "v0.6.0")

    def test_a_git_pin_with_a_fragment_moves_whole(self):
        text, previous = saas_bump.bump_pin(
            "bucli[django] @ git+https://github.com/bithaus-sas/bucli-python@v0.4.0#egg=bucli\n", "v0.6.0"
        )
        assert previous == "v0.4.0" and text == "bucli[django]==0.6.0\n"

    def test_an_environment_marker_stays(self):
        text, _ = saas_bump.bump_pin('bucli[django]==0.5.0 ; python_version >= "3.11"\n', "v0.6.0")
        assert text == 'bucli[django]==0.6.0 ; python_version >= "3.11"\n'

    def test_a_hash_pinned_file_is_left_to_a_person(self, tmp_path):
        for text in (
            "bucli[django]==0.5.0 --hash=sha256:abc\n",
            "bucli[django]==0.5.0 \\\n    --hash=sha256:abc\n",
        ):
            with pytest.raises(saas_bump.HashPinnedError):
                saas_bump.bump_pin(text, "v0.6.0")
        path = tmp_path / "base.txt"
        path.write_text("bucli[django]==0.5.0 --hash=sha256:abc\n")
        assert saas_bump.main(["saas_bump", "pin", str(path), "v0.6.0"]) == saas_bump.HASH_PINNED

    def test_a_file_without_the_pin_is_said(self):
        with pytest.raises(saas_bump.NoPinError):
            saas_bump.bump_pin("django==5.2\n", "v0.5.0")

    def test_only_a_release_tag_is_written(self):
        with pytest.raises(ValueError):
            saas_bump.bump_pin(REQUIREMENTS.format(pin="bucli==0.3.0"), "main")
        with pytest.raises(ValueError):
            saas_bump.bump_pin(REQUIREMENTS.format(pin="bucli==0.3.0"), "0.5.0")

    def test_the_command_says_when_there_is_nothing_to_do(self, tmp_path, capsys):
        path = tmp_path / "base.txt"
        path.write_text(REQUIREMENTS.format(pin=GIT.format(ref="v0.4.0")))

        assert saas_bump.main(["saas_bump", "pin", str(path), "v0.5.0"]) == 0
        assert capsys.readouterr().out.strip() == "v0.4.0"
        assert "bucli[django]==0.5.0" in path.read_text()
        # Again: already there.
        assert saas_bump.main(["saas_bump", "pin", str(path), "v0.5.0"]) == saas_bump.UNCHANGED

    def test_the_command_fails_without_the_pin(self, tmp_path):
        path = tmp_path / "base.txt"
        path.write_text("django==5.2\n")
        assert saas_bump.main(["saas_bump", "pin", str(path), "v0.5.0"]) == saas_bump.NO_PIN

    def test_crlf_lines_stay_crlf(self, tmp_path):
        path = tmp_path / "base.txt"
        path.write_bytes(b"django==5.2\r\nbucli[django]==0.4.0\r\n")
        assert saas_bump.main(["saas_bump", "pin", str(path), "v0.5.0"]) == 0
        assert path.read_bytes() == b"django==5.2\r\nbucli[django]==0.5.0\r\n"


class TestTheNotes:
    def test_the_section_of_the_version(self):
        notes = saas_bump.release_notes(CHANGELOG, "v0.5.0")
        assert notes == "### Added\n- Eventos `product.created` y `product.updated`."

    def test_the_last_section_runs_to_the_end(self):
        assert saas_bump.release_notes(CHANGELOG, "0.4.0") == "### Added\n- `invoice.updated`."

    def test_link_references_are_not_notes(self):
        changelog = CHANGELOG + "\n[0.4.0]: https://github.com/bithaus-sas/bucli-python/compare/v0.3.0...v0.4.0\n"
        assert saas_bump.release_notes(changelog, "v0.4.0") == "### Added\n- `invoice.updated`."

    def test_a_version_without_notes(self):
        assert saas_bump.release_notes(CHANGELOG, "v9.9.9") == ""

    def test_the_real_changelog_has_this_version(self):
        changelog = (SCRIPT.parent.parent / "CHANGELOG.md").read_text()
        version = (SCRIPT.parent.parent / "src" / "bucli" / "_version.py").read_text().split('"')[1]
        assert saas_bump.release_notes(changelog, f"v{version}")
