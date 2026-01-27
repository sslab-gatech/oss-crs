import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


def run_command_with_streaming_output(
    cmd: list[str],
    cwd: Optional[Path] = None,
    env: Optional[dict] = None,
    max_display_lines: int = 20,
    progress_message: str = "Progressing..",
    panel_title: str = "Output",
    success_message: str = "Completed successfully!",
    error_title: str = "Error",
    console: Optional[Console] = None,
) -> bool:
    """
    Run a subprocess command with streaming output displayed in a rich panel.

    Args:
        cmd: Command and arguments to execute.
        cwd: Working directory for the command.
        env: Environment variables for the command.
        max_display_lines: Maximum number of output lines to display.
        progress_message: Message to show while building.
        panel_title: Title for the output panel.
        success_message: Message to show on success.
        error_title: Title for the error panel.
        console: Rich console instance (creates one if not provided).

    Returns:
        True if command succeeded, False otherwise.
    """
    if console is None:
        console = Console()

    output_lines = []

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
            bufsize=1,
        )

        with Live(console=console, refresh_per_second=10) as live:
            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    output_lines.append(line)
                    # Keep only the last N lines for display
                    display_lines = output_lines[-max_display_lines:]

                    # Build display content
                    content = Text()
                    content.append("● ", style="green")
                    content.append(f"{progress_message}\n\n", style="bold")
                    for display_line in display_lines:
                        content.append(f"{display_line}\n", style="dim")

                    live.update(
                        Panel(
                            content,
                            title=f"[bold yellow]{panel_title}[/bold yellow]",
                            border_style="yellow",
                        )
                    )

            process.wait()

        if process.returncode == 0:
            console.print(
                Panel(
                    f"[bold green]✓ {success_message}[/bold green]",
                    border_style="green",
                )
            )
            return True
        else:
            console.print(
                Panel(
                    f"[bold red]✗ Command failed with exit code {process.returncode}[/bold red]\n\n"
                    + "\n".join(output_lines[-10:]),
                    title=f"[bold red]{error_title}[/bold red]",
                    border_style="red",
                )
            )
            return False

    except FileNotFoundError:
        cmd_name = cmd[0] if cmd else "command"
        console.print(
            f"[bold red]Error:[/bold red] {cmd_name} not found. "
            "Please ensure it is installed and in PATH."
        )
        return False
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return False
