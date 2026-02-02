from pathlib import Path
from typing import Optional
import hashlib
import tempfile
import git

from .config.target import TargetConfig
from .ui import MultiTaskProgress, TaskResult
from . import ui


def extract_name_from_proj_path(proj_path: str) -> str:
    tmp = proj_path.split("/")
    return tmp[-1] or tmp[-2]


class Target:
    def __init__(
        self,
        work_dir: Path,
        proj_path: Path,
        repo_path: Optional[Path],
        no_checkout: bool = False,
        target_harness: Optional[str] = None,
    ):
        self.name = extract_name_from_proj_path(str(proj_path))
        self.work_dir = work_dir / "targets" / self.name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.proj_path = proj_path
        if repo_path:
            self.repo_path = repo_path
        else:
            self.repo_path = self.work_dir / "repo"
        self.config = TargetConfig.from_yaml_file(proj_path / "project.yaml")
        self.repo_hash: Optional[str] = None
        self.no_checkout = no_checkout
        self.target_harness = target_harness

    def get_docker_image_name(self) -> str:
        repo_hash = self.__get_repo_hash()
        return f"{self.name}:{repo_hash}"

    def build_docker_image(self) -> str:
        if not self.__init_repo(self.no_checkout):
            return None
        repo_hash = self.__get_repo_hash()
        image_tag = self.get_docker_image_name()

        tasks = [
            (
                "Build docker image with the given repo",
                lambda progress: self.__build_docker_image_with_repo(
                    image_tag, progress
                ),
            ),
        ]

        with MultiTaskProgress(
            tasks, title=f"Building {self.name} docker image"
        ) as progress:
            progress.add_items_to_head(
                [
                    ui.bold(
                        f"Repo hash: {repo_hash} (calculated from {self.repo_path})"
                    ),
                    ui.bold(f"Image tag: {image_tag}"),
                    ui.yellow(
                        f"Note: /src/ will have contents in {self.repo_path}",
                        True,
                    ),
                ]
            )
            if progress.run_added_tasks().success:
                return image_tag
        return None

    def __init_repo(self, no_checkout: bool) -> bool:
        title = f"Setting up Target {self.name}"
        head = [
            ui.bold(f"Init {self.name} repo into {self.repo_path}"),
            ui.yellow(
                "Please make sure the repo is accessible without typing your credentials.",
                True,
            ),
        ]
        tasks = []
        if self.repo_path.exists():
            head.append(ui.bold(f"--no-checkout: {no_checkout}, repo exists: True"))
            if no_checkout:
                head.append(ui.yellow("Skipping repository initialization."))
            else:
                head.append(ui.green("Fetching latest changes..."))
                tasks += [
                    ("Git fetch", lambda progress: self.__fetch_main_repo(progress)),
                    (
                        "Git checkout main",
                        lambda progress: self.__checkout_main_repo(progress),
                    ),
                ]
        else:
            head.append(ui.green("Cloning repository..."))
            tasks += [
                ("Git clone", lambda progress: self.__clone(progress)),
            ]
        with MultiTaskProgress(tasks=tasks, title=title) as progress:
            progress.add_items_to_head(head)
            return progress.run_added_tasks().success

    def __fetch_main_repo(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = ["git", "fetch", "origin"]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path,
        )

    def __checkout_main_repo(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = ["git", "checkout", "-f", "main"]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path,
        )

    def __clone(self, progress: MultiTaskProgress) -> "TaskResult":
        cmd = ["git", "clone", self.config.main_repo, str(self.repo_path)]
        return progress.run_command_with_streaming_output(
            cmd=cmd,
            cwd=self.repo_path.parent,
        )

    def __get_repo_hash(self) -> str:
        """
        Get a hash representing the current state of the repository.

        If the working directory is clean, returns the current commit hash.
        Otherwise, returns a hash combining the commit hash and the diff of changes.
        """
        if self.repo_hash is not None:
            return self.repo_hash
        repo = git.Repo(self.repo_path)
        commit_hash = repo.head.commit.hexsha

        # Check if working directory is clean
        if not repo.is_dirty(untracked_files=True):
            self.repo_hash = commit_hash
            return commit_hash

        # Dirty working directory - combine commit hash with changes
        hasher = hashlib.sha256()
        hasher.update(commit_hash.encode())

        # Get list of changed tracked files (modified + deleted + staged)
        changed_files = set()
        # Unstaged changes
        for diff_item in repo.index.diff(None):
            changed_files.add(diff_item.a_path)
        # Staged changes
        for diff_item in repo.index.diff("HEAD"):
            changed_files.add(diff_item.a_path)

        # Process tracked changed files in sorted order
        for file_path in sorted(changed_files):
            full_path = self.repo_path / file_path
            hasher.update(f"\n--- changed: {file_path}\n".encode())
            if full_path.exists() and full_path.is_file():
                try:
                    # Read file as binary to handle both text and binary files
                    hasher.update(full_path.read_bytes())
                except Exception:
                    hasher.update(b"<read-error>")
            else:
                # File was deleted
                hasher.update(b"<deleted>")

        # Process untracked files in sorted order
        for file_path in sorted(repo.untracked_files):
            full_path = self.repo_path / file_path
            if full_path.is_file():
                hasher.update(f"\n--- untracked: {file_path}\n".encode())
                try:
                    hasher.update(full_path.read_bytes())
                except Exception:
                    hasher.update(b"<read-error>")

        self.repo_hash = hasher.hexdigest()[:12]
        return self.repo_hash

    def __build_docker_image_with_repo(
        self, image_tag: str, progress: MultiTaskProgress
    ) -> "TaskResult":
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".Dockerfile", delete=True
        ) as tmp_dockerfile:
            added_dockerfile = (self.proj_path / "Dockerfile").read_bytes()
            added_dockerfile += b"\n# Added by CRS Target build\n"
            # Exclude volatile .git files that change on fetch/checkout but aren't needed for history:
            # - FETCH_HEAD: updated every fetch
            # - logs/: reflog entries (not needed for commit history)
            # - refs/remotes/: remote tracking branches (updated on fetch)
            # - ORIG_HEAD: updated on various operations
            # Core history is preserved in: objects/, refs/heads/, refs/tags/, HEAD
            added_dockerfile += (
                f"COPY --exclude=.git/FETCH_HEAD "
                f"--exclude=.git/logs "
                f"--exclude=.git/refs/remotes "
                f"--exclude=.git/ORIG_HEAD "
                f"--from=repo_path . /src/{self.name}\n"
            ).encode()
            added_dockerfile += ("COPY . /project_dir\n").encode()
            tmp_dockerfile.write(added_dockerfile)
            tmp_dockerfile.flush()

            # TODO: We might need to consider cache options here later.

            cmd = [
                "docker",
                "buildx",
                "build",
                "--build-context",
                f"repo_path={self.repo_path}",
                "-t",
                image_tag,
                "-f",
                tmp_dockerfile.name,
                str(self.proj_path),
            ]
            return progress.run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.work_dir,
            )

    def get_target_env(self) -> dict:
        # TODO: implement this properly
        ret = {
            "name": self.name,
            "language": self.config.language.value,
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
        }

        if self.target_harness:
            ret["harness"] = self.target_harness

        return ret
