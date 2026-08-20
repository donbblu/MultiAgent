from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from coding_workflow import (
    Artifact,
    ArtifactStore,
    CommandPolicy,
    CommandValidator,
    ControlledCommandRunner,
    FixedCodingSuite,
    ValidatorRunRequest,
    ValidatorSpec,
    VerificationOutcome,
)


class CommandValidatorTests(unittest.TestCase):
    def _request(
        self, kind: str, command: tuple[str, ...], **options
    ) -> ValidatorRunRequest:
        config = {
            "commands": [{"argv": list(command), **options}],
            "timeout_seconds": 1,
        }
        return ValidatorRunRequest(
            "task",
            ValidatorSpec("command_validator", kind, ("command_passes",), config),
            {},
        )

    def _validator(
        self,
        root: Path,
        kind: str,
        commands: tuple[tuple[str, ...], ...],
        *,
        timeout: float = 1,
        output_limit: int = 8000,
    ) -> CommandValidator:
        return CommandValidator(
            kind,
            ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={item[0] for item in commands},
                    allowed_commands=[list(item) for item in commands],
                ),
                max_timeout_seconds=timeout,
                output_limit_chars=output_limit,
            ),
        )

    def test_pass_records_exit_output_duration_and_hashes(self) -> None:
        command = ("python3", "-c", "print('cli-ok')")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "python3"
            marker = root / "workspace-binary-ran"
            fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
            fake.chmod(0o755)
            validator = self._validator(root, "core:cli", (command,))
            result = validator.validate(self._request(
                "core:cli", command, stdout_contains=["cli-ok"]
            ))
            self.assertFalse(marker.exists())

        self.assertEqual(result.outcome, VerificationOutcome.PASSED)
        evidence = result.evidence[0].content["result"]
        self.assertEqual(evidence["command"], list(command))
        self.assertEqual(evidence["exit_code"], 0)
        self.assertGreaterEqual(evidence["duration_ms"], 0)
        self.assertEqual(len(evidence["stdout_sha256"]), 64)

    def test_nonzero_exit_is_failed_but_timeout_and_missing_tool_are_unknown(self) -> None:
        failure = ("python3", "-c", "raise SystemExit(3)")
        timeout = ("python3", "-c", "import time; time.sleep(1)")
        missing = ("definitely_missing_validator_binary", "--version")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            failed = self._validator(root, "core:test", (failure,)).validate(
                self._request("core:test", failure)
            )
            timed_out = self._validator(
                root, "core:test", (timeout,), timeout=0.05
            ).validate(ValidatorRunRequest(
                "task",
                ValidatorSpec(
                    "timeout_validator",
                    "core:test",
                    ("tests_pass",),
                    {"commands": [{"argv": list(timeout)}], "timeout_seconds": 0.05},
                ),
                {},
            ))
            unavailable = self._validator(
                root, "core:cli", (missing,)
            ).validate(self._request("core:cli", missing))

        self.assertEqual(failed.outcome, VerificationOutcome.FAILED)
        self.assertEqual(timed_out.outcome, VerificationOutcome.UNKNOWN)
        self.assertTrue(timed_out.evidence[0].content["result"]["timed_out"])
        self.assertEqual(unavailable.outcome, VerificationOutcome.UNKNOWN)
        self.assertTrue(unavailable.evidence[0].content["result"]["tool_missing"])

    def test_assertions_use_full_output_but_evidence_is_clipped_and_redacted(self) -> None:
        code = (
            "print('token=123456789-secret ' + 'a'*300 + "
            "'needle-in-middle' + 'z'*300)"
        )
        command = ("python3", "-c", code)
        with tempfile.TemporaryDirectory() as temp:
            result = self._validator(
                Path(temp), "core:cli", (command,), output_limit=200
            ).validate(self._request(
                "core:cli", command, stdout_contains=["needle-in-middle"]
            ))

        self.assertEqual(result.outcome, VerificationOutcome.PASSED)
        evidence = result.evidence[0].content["result"]
        self.assertTrue(evidence["stdout_truncated"])
        self.assertIn("[TRUNCATED", evidence["stdout"])
        self.assertIn("token=[REDACTED]", evidence["stdout"])
        self.assertNotIn("123456789-secret", evidence["stdout"])
        self.assertNotIn("123456789-secret", repr(evidence))

    def test_unapproved_command_is_unknown_and_never_executes(self) -> None:
        allowed = ("python3", "-c", "print('allowed')")
        denied = ("python3", "-c", "print('denied')")
        with tempfile.TemporaryDirectory() as temp:
            validator = self._validator(Path(temp), "core:cli", (allowed,))
            result = validator.validate(self._request("core:cli", denied))
        self.assertEqual(result.outcome, VerificationOutcome.UNKNOWN)
        self.assertEqual(result.evidence, ())
        self.assertIn("未获 Runtime 接纳", result.summary)


class FixedCodingEvaluationTests(unittest.TestCase):
    @property
    def suite_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "coding_eval" / "v1"

    def test_hidden_validation_fails_starter_then_passes_reference_solution(self) -> None:
        task = FixedCodingSuite.load(self.suite_root).task(
            "python-tax-rounding"
        )
        with self.assertRaises(TypeError):
            task.validator_configs["core:test"]["commands"][0]["argv"] = (
                "python3", "unapproved.py"
            )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = task.prepare_workspace(root / "agent-workspace")
            self.assertFalse((workspace / ".harness-hidden-tests").exists())

            artifacts = ArtifactStore()
            starter_ref = artifacts.put(Artifact.create(
                "candidate", "starter-run", {"revision": "starter"}
            ))
            starter = task.validate_candidate(
                workspace=workspace,
                validation_workspace=root / "validation-starter",
                artifacts=artifacts,
                subject_refs=(starter_ref,),
                task_id="starter-run",
            )
            self.assertEqual(starter.outcome, VerificationOutcome.FAILED)
            self.assertEqual(
                [item.outcome for item in starter.validator_records],
                [VerificationOutcome.PASSED, VerificationOutcome.FAILED],
            )
            self.assertFalse((workspace / ".harness-hidden-tests").exists())

            task.apply_reference_solution(workspace)
            solved_ref = artifacts.put(Artifact.create(
                "candidate", "solved-run", {"revision": "reference-solution"}
            ))
            solved = task.validate_candidate(
                workspace=workspace,
                validation_workspace=root / "validation-solved",
                artifacts=artifacts,
                subject_refs=(solved_ref,),
                task_id="solved-run",
            )
            self.assertEqual(solved.outcome, VerificationOutcome.PASSED)
            gate = artifacts.verification(solved.verification_ref)
            self.assertTrue(artifacts.is_verified(
                solved_ref, workspace_hash=gate.workspace_hash
            ))

            evidence_text = repr([
                artifact.content
                for artifact, _ in artifacts.snapshot()
                if artifact.kind == "core:command_evidence"
            ])
            self.assertNotIn("2.675", evidence_text)
            self.assertNotIn("0.045", evidence_text)
            self.assertIn("hidden checks failed", evidence_text)

    def test_suite_rejects_tampered_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "suite"
            shutil.copytree(self.suite_root, copied)
            target = copied / "tasks/python-tax-rounding/starter/tax.py"
            target.write_text(target.read_text() + "\n# tampered\n")
            with self.assertRaisesRegex(ValueError, "哈希不匹配"):
                FixedCodingSuite.load(copied)

    def test_agent_workspace_cannot_preinstall_reserved_hidden_tests(self) -> None:
        task = FixedCodingSuite.load(self.suite_root).tasks[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = task.prepare_workspace(root / "agent-workspace")
            reserved = workspace / ".harness-hidden-tests"
            reserved.mkdir()
            (reserved / "fake.py").write_text("pass\n")
            with self.assertRaisesRegex(PermissionError, "保留目录"):
                task.prepare_validation_workspace(
                    workspace, root / "validation"
                )

    def test_candidate_cannot_modify_public_tests_or_add_unapproved_files(self) -> None:
        task = FixedCodingSuite.load(self.suite_root).tasks[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changed = task.prepare_workspace(root / "changed-tests")
            public_test = changed / "tests/test_tax_public.py"
            public_test.write_text(public_test.read_text() + "\n# changed\n")
            with self.assertRaisesRegex(PermissionError, "受保护文件"):
                task.prepare_validation_workspace(
                    changed, root / "validation-changed"
                )

            extra = task.prepare_workspace(root / "extra-file")
            (extra / "backdoor.py").write_text("pass\n")
            with self.assertRaisesRegex(PermissionError, "未授权文件"):
                task.prepare_validation_workspace(
                    extra, root / "validation-extra"
                )


if __name__ == "__main__":
    unittest.main()
