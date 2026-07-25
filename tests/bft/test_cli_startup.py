from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def _fresh_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", dedent(code)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_top_level_help_does_not_import_command_or_bft_modules() -> None:
    result = _fresh_python(
        """
        import sys
        from cli.main import fern_cli

        try:
            fern_cli.main(args=['--help'], prog_name='fern')
        except SystemExit as exc:
            assert exc.code == 0
        loaded = sorted(
            name for name in sys.modules
            if name.startswith('cli.commands.') or name.startswith('fern.bft')
        )
        print('LOADED=' + ','.join(loaded))
        """
    )

    assert result.returncode == 0, result.stderr
    assert "Commands:" in result.stdout
    assert "LOADED=" in result.stdout
    assert "LOADED=\n" in result.stdout


def test_subcommand_help_imports_only_the_selected_command_module() -> None:
    result = _fresh_python(
        """
        import sys
        from cli.main import fern_cli

        try:
            fern_cli.main(args=['init', '--help'], prog_name='fern')
        except SystemExit as exc:
            assert exc.code == 0
        loaded = sorted(name for name in sys.modules if name.startswith('cli.commands.'))
        print('LOADED=' + ','.join(loaded))
        """
    )

    assert result.returncode == 0, result.stderr
    assert "LOADED=cli.commands.init" in result.stdout
