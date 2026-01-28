import subprocess
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"


class MultiTaskProgress:
    """
    A progress tracker for multiple tasks with live updates.
    Shows a table of all tasks with their current status.

    Args:
        tasks: A list of (task_name, task_function) tuples.
               Each function takes (progress: MultiTaskProgress) and returns bool.
        title: Title for the progress panel.
        console: Optional Rich console instance.
    """

    def __init__(
        self,
        tasks: list[tuple[str, Callable[["MultiTaskProgress"], bool]]],
        title: str = "Task Progress",
        console: Optional[Console] = None,
    ):
        self.tasks = tasks
        self.task_names = [name for name, _ in tasks]
        self.title = title
        self.console = console or Console()
        self.statuses: dict[str, TaskStatus] = {
            name: TaskStatus.PENDING for name in self.task_names
        }
        self.task_info: dict[str, str] = {}  # Info text for each task
        self.cmd_info: dict[str, tuple[str, str]] = {}  # (cmd, cwd) for each task
        self.error_info: dict[str, str] = {}  # Error message for failed tasks
        self.current_output_lines: list[str] = []
        self.max_output_lines = 10
        self._live: Optional[Live] = None
        self._current_task: Optional[str] = None

    def _get_status_icon(self, status: TaskStatus) -> str:
        icons = {
            TaskStatus.PENDING: "[dim]○[/dim]",
            TaskStatus.IN_PROGRESS: "[yellow]●[/yellow]",
            TaskStatus.SUCCESS: "[green]✓[/green]",
            TaskStatus.FAILED: "[red]✗[/red]",
        }
        return icons[status]

    def _get_status_text(self, status: TaskStatus) -> str:
        texts = {
            TaskStatus.PENDING: "[dim]Pending[/dim]",
            TaskStatus.IN_PROGRESS: "[yellow]In Progress[/yellow]",
            TaskStatus.SUCCESS: "[green]Success[/green]",
            TaskStatus.FAILED: "[red]Failed[/red]",
        }
        return texts[status]

    def _build_display(self) -> Panel:
        """Build the display panel with task table and current output."""
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("", width=3)
        table.add_column("Task", style="bold")
        table.add_column("Status")

        current_task = None
        for name in self.task_names:
            status = self.statuses[name]
            if status == TaskStatus.IN_PROGRESS:
                current_task = name
            table.add_row(
                self._get_status_icon(status),
                name,
                self._get_status_text(status),
            )

        content_parts = [table]

        # Add task info if there's an in-progress task with info
        if current_task and current_task in self.task_info:
            info_panel = Panel(
                f"[dim]{self.task_info[current_task]}[/dim]",
                title=f"[bold]{current_task}[/bold]",
                border_style="yellow",
            )
            content_parts.append(info_panel)

        # Add command progress panel if there's an in-progress task
        if current_task and current_task in self.cmd_info:
            cmd, cwd = self.cmd_info[current_task]
            cmd_header = Text()
            cmd_header.append("Command: ", style="bold")
            cmd_header.append(f"{cmd}\n", style="dim")
            cmd_header.append("CWD: ", style="bold")
            cmd_header.append(f"{cwd}", style="dim")

            if self.current_output_lines:
                output_text = Text()
                output_text.append("\n\n")
                for line in self.current_output_lines[-self.max_output_lines :]:
                    output_text.append(f"{line}\n", style="dim")
                cmd_content = Group(cmd_header, output_text)
            else:
                cmd_content = cmd_header

            cmd_panel = Panel(
                cmd_content,
                title="[bold yellow]Command Progress[/bold yellow]",
                border_style="yellow",
            )
            content_parts.append(cmd_panel)

        # Add error panel if there's a failed task with error info
        for name in self.task_names:
            if self.statuses[name] == TaskStatus.FAILED and name in self.error_info:
                error_panel = Panel(
                    f"[red]{self.error_info[name]}[/red]",
                    title=f"[bold red]Error: {name}[/bold red]",
                    border_style="red",
                )
                content_parts.append(error_panel)
                break  # Only show the first error

        return Panel(
            Group(*content_parts),
            title=f"[bold]{self.title}[/bold]",
            border_style="blue",
        )

    def set_status(self, task_name: str, status: TaskStatus) -> None:
        """Update the status of a task."""
        self.statuses[task_name] = status
        if status != TaskStatus.IN_PROGRESS:
            # Clear output and cmd_info when task completes
            self.current_output_lines = []
            if task_name in self.cmd_info:
                del self.cmd_info[task_name]
        if self._live:
            self._live.update(self._build_display())

    def __set_task_info(self, task_name: str, info: str) -> None:
        """Set info text for a task (shown when task is in progress)."""
        self.task_info[task_name] = info
        if self._live:
            self._live.update(self._build_display())

    def __set_cmd_info(self, task_name: str, cmd: str, cwd: str) -> None:
        """Set command info for a task (shown in command progress panel)."""
        self.cmd_info[task_name] = (cmd, cwd)
        if self._live:
            self._live.update(self._build_display())

    def set_error_info(self, task_name: str, error: str) -> None:
        """Set error message for a failed task."""
        self.error_info[task_name] = error
        if self._live:
            self._live.update(self._build_display())

    def add_output_line(self, line: str) -> None:
        """Add an output line for the current in-progress task."""
        self.current_output_lines.append(line)
        if self._live:
            self._live.update(self._build_display())

    def __enter__(self) -> "MultiTaskProgress":
        self._live = Live(
            self._build_display(),
            console=self.console,
            refresh_per_second=10,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *args) -> None:
        if self._live:
            self._live.__exit__(*args)
            self._live = None

    def run_all_tasks(self) -> bool:
        """
        Run all tasks in order.

        Returns:
            True if all tasks succeeded, False if any task failed.
        """
        for task_name, task_func in self.tasks:
            self._current_task = task_name
            self.set_status(task_name, TaskStatus.IN_PROGRESS)
            try:
                result = task_func(self)
                self.set_status(
                    task_name, TaskStatus.SUCCESS if result else TaskStatus.FAILED
                )
                if not result:
                    self._current_task = None
                    return False
            except Exception as e:
                self.set_error_info(task_name, str(e))
                self.set_status(task_name, TaskStatus.FAILED)
                self._current_task = None
                return False
        self._current_task = None
        return True

    def run_task(
        self,
        task_name: str,
        task_func: Callable[[], bool],
    ) -> bool:
        """
        Run a task and update its status.

        Args:
            task_name: Name of the task to run.
            task_func: Function that returns True on success, False on failure.

        Returns:
            True if task succeeded, False otherwise.
        """
        self.set_status(task_name, TaskStatus.IN_PROGRESS)
        try:
            result = task_func()
            self.set_status(
                task_name, TaskStatus.SUCCESS if result else TaskStatus.FAILED
            )
            return result
        except Exception:
            self.set_status(task_name, TaskStatus.FAILED)
            return False

    def run_command_with_streaming_output(
        self,
        cmd: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict] = None,
        info_text: Optional[str] = None,
    ) -> bool:
        """
        Run a subprocess command with streaming output displayed via MultiTaskProgress.

        Args:
            cmd: Command and arguments to execute.
            cwd: Working directory for the command.
            env: Environment variables for the command.

        Returns:
            True if command succeeded, False otherwise.
        """
        task_name = self._current_task
        if info_text:
            self.__set_task_info(task_name, info_text)
        self.__set_cmd_info(self._current_task, " ".join(cmd), str(cwd) if cwd else ".")
        output_lines = []

        def process_output(line: str) -> None:
            """Process a line of output."""
            output_lines.append(line)
            self.add_output_line(line)

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

            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    process_output(line)
            process.wait()

            if process.returncode == 0:
                return True
            else:
                # Set error info with last output lines
                if task_name:
                    error_msg = f"Command failed with exit code {process.returncode}:\n{' '.join(cmd)}\n\n"
                    error_msg += "\n".join(output_lines[-10:])
                    self.set_error_info(task_name, error_msg)
                return False

        except FileNotFoundError:
            cmd_name = cmd[0] if cmd else "command"
            error_msg = (
                f"{cmd_name} not found. Please ensure it is installed and in PATH."
            )
            if task_name:
                self.set_error_info(task_name, error_msg)
            else:
                Console().print(f"[bold red]Error:[/bold red] {error_msg}")
            return False
        except Exception as e:
            error_msg = str(e)
            if task_name:
                self.set_error_info(task_name, error_msg)
            else:
                Console().print(f"[bold red]Error:[/bold red] {error_msg}")
            return False
