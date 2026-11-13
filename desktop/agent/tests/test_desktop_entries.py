from __future__ import annotations

import pytest

from ultron_agent.desktop_entries import check_argv, parse_desktop_file


def entry(tmp, name, body):
    p = tmp / f"{name}.desktop"
    p.write_text("[Desktop Entry]\n" + body)
    return p


def test_parses_name_exec_and_strips_field_codes(short_tmp):
    c = parse_desktop_file(
        entry(
            short_tmp,
            "calc",
            "Type=Application\nName=Calculator\nExec=gnome-calculator %U --new-window\n",
        )
    )
    assert c and c.app_id == "calc" and c.name == "Calculator"
    assert c.argv == ["gnome-calculator", "--new-window"]


def test_hidden_terminal_and_non_applications_are_skipped(short_tmp):
    for i, extra in enumerate(["NoDisplay=true", "Hidden=true", "Terminal=true", "Type=Link"]):
        assert parse_desktop_file(entry(short_tmp, f"x{i}", f"Name=X\nExec=foo\n{extra}\n")) is None


def test_shell_wrappers_and_interpreters_are_never_candidates(short_tmp):
    for exe in (
        "sh -c 'rm -rf ~'",
        "bash -lc foo",
        "env FOO=1 app",
        "python3 -m http.server",
        "sudo app",
    ):
        assert parse_desktop_file(entry(short_tmp, "bad", f"Name=Bad\nExec={exe}\n")) is None


def test_actions_sections_do_not_override_the_main_entry(short_tmp):
    p = short_tmp / "app.desktop"
    p.write_text(
        "[Desktop Entry]\nName=App\nExec=app\n\n[Desktop Action new]\nName=New\nExec=app --evil\n"
    )
    assert parse_desktop_file(p).argv == ["app"]


def test_malformed_files_are_skipped(short_tmp):
    assert parse_desktop_file(entry(short_tmp, "nameless", "Exec=foo\n")) is None
    assert parse_desktop_file(entry(short_tmp, "unbalanced", "Name=U\nExec=foo 'bar\n")) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["python3.14", "-c", "pass"],  # review finding: versioned interpreters
        ["/usr/bin/python3.13"],
        ["lua5.4"],
        ["nohup", "sleep", "1"],  # wrappers that run another program
        ["setsid", "x"],
        ["timeout", "5", "x"],
        ["nice", "x"],
        ["rbash"],
        ["xterm"],  # terminals run whatever is typed or passed with -e
        ["gnome-terminal"],
        ["/tmp/some/dir/../bash"],
    ],
)
def test_more_ways_to_run_arbitrary_code_are_refused(argv):
    from ultron_agent.config import ConfigError

    with pytest.raises(ConfigError):
        check_argv(argv)


def test_a_symlink_to_a_shell_is_refused_by_what_it_points_to(short_tmp):
    from ultron_agent.config import ConfigError

    link = short_tmp / "calculator"
    link.symlink_to("/bin/sh")  # looks harmless by name
    with pytest.raises(ConfigError):
        check_argv([str(link)])


def test_ordinary_applications_are_still_allowed():
    for argv in (["gnome-calculator"], ["/usr/bin/firefox", "--new-window"], ["code"]):
        check_argv(argv)
