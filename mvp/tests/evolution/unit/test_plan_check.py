"""Completed-session claims must remain tied to complete sequential history."""

from evolution.plan_check import SESSION_SECTIONS, session_findings


def test_active_plan_requires_matching_complete_sequential_session_history(tmp_path):
    progress = tmp_path / "progress.md"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    progress.write_text("**Sessions completed:** 1\n")
    assert session_findings(tmp_path)
    first = sessions / "001.md"
    first.write_text(
        "\n".join(
            "## " + section + "\nRecorded outcome\n" for section in SESSION_SECTIONS
        )
    )
    assert session_findings(tmp_path) == []
    progress.write_text("**Sessions completed:** 0\n")
    assert session_findings(tmp_path) == []  # One open current session.
    first.rename(sessions / "002.md")
    assert session_findings(tmp_path)
    (sessions / "002.md").rename(first)
    first.write_text("## Work Completed\n")
    assert session_findings(tmp_path)
    progress.write_text("**Sessions completed:** -1\n")
    assert session_findings(tmp_path)
