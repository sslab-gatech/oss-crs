from pydantic import BaseModel
from typing import Optional
import json
from pathlib import Path


class SinkConfig(BaseModel):
    function_name: str
    file_path: str
    start_line: int
    end_line: int


class TargetInput(BaseModel):
    sink: SinkConfig
    description: Optional[str] = None

    @classmethod
    def from_json_file(cls, path: Path) -> "TargetInput":
        return cls(**json.loads(path.read_text()))
