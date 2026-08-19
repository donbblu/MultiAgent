from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import MappingProxyType

from .scenario import ScenarioRunState


class SQLiteScenarioRunStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS scenario_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5)

    def save(self, state: ScenarioRunState) -> None:
        payload = {
            "current_round": state.current_round,
            "max_rework_rounds": state.max_rework_rounds,
            "round_snapshot_ids": state.round_snapshot_ids,
            "active_snapshot_id": state.active_snapshot_id,
            "round_artifacts": [dict(item) for item in state.round_artifacts],
            "active_artifacts": dict(state.active_artifacts),
            "gate_artifact_ref": state.gate_artifact_ref,
            "result_artifact_ref": state.result_artifact_ref,
            "summary": state.summary,
            "request_fingerprint": state.request_fingerprint,
            "workspace_hashes": dict(state.workspace_hashes),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO scenario_runs(
                    run_id, task_id, project_id, scenario, status, payload, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    project_id=excluded.project_id,
                    scenario=excluded.scenario,
                    status=excluded.status,
                    payload=excluded.payload,
                    version=scenario_runs.version + 1""",
                (
                    state.run_id, state.task_id, state.project_id,
                    state.scenario, state.status,
                    json.dumps(payload, ensure_ascii=False), state.version,
                ),
            )

    def load(self, run_id: str) -> ScenarioRunState | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT task_id, project_id, scenario, status, payload, version
                   FROM scenario_runs WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        task_id, project_id, scenario, status, raw, version = row
        data = json.loads(raw)
        return ScenarioRunState(
            run_id, str(task_id), str(project_id), str(scenario), str(status),
            int(data["current_round"]), int(data["max_rework_rounds"]),
            tuple(data["round_snapshot_ids"]), str(data["active_snapshot_id"]),
            tuple(
                MappingProxyType(dict(item))
                for item in data.get("round_artifacts", [])
            ),
            MappingProxyType(dict(data.get("active_artifacts", {}))),
            str(data.get("gate_artifact_ref", "")),
            str(data.get("result_artifact_ref", "")),
            str(data.get("summary", "")),
            str(data.get("request_fingerprint", "")),
            MappingProxyType(dict(data.get("workspace_hashes", {}))),
            int(version),
        )
