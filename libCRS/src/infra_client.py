from pathlib import Path
from dataclasses import dataclass


@dataclass
class SubmitData:
    file_path: Path
    hash: str
    finder: str


class InfraClient:
    def __init__(self):
        pass

    def submit_batch(
        self, data_type: str, data_list: list[SubmitData], ship_file_data: bool
    ) -> None:
        pass

    # TODO
