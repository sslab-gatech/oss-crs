from pathlib import Path
import subprocess
import os

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry

CRS_YAML_PATH = "oss-crs/crs.yaml"


class CRS:
    @classmethod
    def from_yaml_file(cls, crs_path: Path, work_dir: Path) -> "CRS":
        config = CRSConfig.from_yaml_file(crs_path / CRS_YAML_PATH)
        return cls(config.name, crs_path, work_dir)

    @classmethod
    def from_crs_compose_entry(
        cls, name: str, entry: CRSEntry, work_dir: Path
    ) -> "CRS":
        if entry.source.local_path:
            return cls(name, Path(entry.source.local_path), work_dir)
        raise NotImplementedError("Only local_path source is implemented yet.")

    def __init__(self, name: str, crs_path: Path, work_dir: Path):
        self.name = name
        self.crs_path = crs_path.expanduser().resolve()
        self.config = CRSConfig.from_yaml_file(self.crs_path / CRS_YAML_PATH)
        self.work_dir = work_dir

    def prepare(self, publish: bool = False, docker_registry: str = None) -> bool:
        """
        Run docker buildx bake to prepare CRS images.

        Args:
            publish: If True, push baked images to the docker registry.
            docker_registry: Override registry for push/cache. If set, overrides config.

        Returns:
            True if bake succeeded, False otherwise.
        """
        console = Console()

        # Determine the registry to use (parameter overrides config)
        registry = docker_registry if docker_registry else self.config.docker_registry
        version = self.config.version

        # Build HCL file path (relative to crs_path)
        hcl_path = self.crs_path / self.config.prepare_phase.hcl

        # Build the base command
        cmd = ["docker", "buildx", "bake", "-f", str(hcl_path)]

        # Add cache-from options (buildx silently ignores unavailable sources)
        if registry:
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-from=type=registry,ref={cache_ref_version}",
                    f"--set=*.cache-from=type=registry,ref={cache_ref_latest}",
                ]
            )

        # Add push and cache-to options if publishing
        if publish:
            if not registry:
                console.print(
                    "[bold red]Error:[/bold red] Cannot publish without a docker registry. "
                    "Provide docker_registry parameter or set it in config."
                )
                return False

            cmd.append("--push")
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-to=type=registry,ref={cache_ref_version},mode=max",
                    f"--set=*.cache-to=type=registry,ref={cache_ref_latest},mode=max",
                ]
            )

        # Set up environment with VERSION
        env = os.environ.copy()
        env["VERSION"] = version

        # Display command info
        console.print(
            Panel(
                f"[bold blue]Docker Buildx Bake[/bold blue]\n"
                f"[dim]HCL:[/dim] {hcl_path}\n"
                f"[dim]Version:[/dim] {version}\n"
                f"[dim]Registry:[/dim] {registry or 'N/A'}\n"
                f"[dim]Publish:[/dim] {publish}",
                title=f"[bold]Preparing CRS: {self.name}[/bold]",
                border_style="blue",
            )
        )

        # Run the bake command with streaming output
        output_lines = []
        max_display_lines = 20

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.crs_path,
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
                        content.append("Building...\n\n", style="bold")
                        for display_line in display_lines:
                            content.append(f"{display_line}\n", style="dim")

                        live.update(
                            Panel(
                                content,
                                title="[bold yellow]Build Output[/bold yellow]",
                                border_style="yellow",
                            )
                        )

                process.wait()

            if process.returncode == 0:
                console.print(
                    Panel(
                        "[bold green]✓ Build completed successfully![/bold green]",
                        border_style="green",
                    )
                )
                return True
            else:
                console.print(
                    Panel(
                        f"[bold red]✗ Build failed with exit code {process.returncode}[/bold red]\n\n"
                        + "\n".join(output_lines[-10:]),
                        title="[bold red]Error[/bold red]",
                        border_style="red",
                    )
                )
                return False

        except FileNotFoundError:
            console.print(
                "[bold red]Error:[/bold red] docker command not found. "
                "Please ensure Docker is installed and in PATH."
            )
            return False
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            return False
