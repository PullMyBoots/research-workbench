import argparse
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "cli" / "discovery.py"
SPEC = importlib.util.spec_from_file_location("discovery_cli_resource_tests", MODULE_PATH)
assert SPEC and SPEC.loader
DISCOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISCOVERY)


def resource_config(*, queue_cpus: int = 4, queue_gpus: list[int] | None = None) -> dict:
    return {
        "schema_version": 1,
        "free_run": {
            "default": {"cpus": 1, "memory_gb": 1, "gpus": []},
            "agents": {"agent1": {"cpus": 2}},
        },
        "queue": {"capacity": {"cpus": queue_cpus, "memory_gb": 8, "gpus": queue_gpus or []}},
        "evaluation": {"resources": {"cpus": 2, "memory_gb": 4, "gpus": []}, "timeout_seconds": None},
        "scheduler": {
            "memory_reserve_gb": 0,
            "respect_system_load": False,
            "respect_external_gpu_processes": True,
        },
    }


class ProblemResourceContractTests(unittest.TestCase):
    def test_problem_policy_is_normalized_and_agent_override_is_small(self) -> None:
        config = DISCOVERY.validate_resource_config_data(resource_config())
        self.assertEqual(DISCOVERY.free_run_resources(config, "agent1")["cpus"], 2)
        self.assertEqual(DISCOVERY.free_run_resources(config, "agent2")["cpus"], 1)
        self.assertEqual(DISCOVERY.queue_capacity(config)["cpus"], 4)
        self.assertEqual(DISCOVERY.evaluation_resources(config)["cpus"], 2)

    def test_eval_must_fit_queue_and_free_gpu_cannot_overlap_queue(self) -> None:
        too_small = resource_config(queue_cpus=1)
        with self.assertRaisesRegex(SystemExit, "evaluation.resources must fit"):
            DISCOVERY.validate_resource_config_data(too_small)

        overlap = resource_config(queue_gpus=[0])
        overlap["free_run"]["agents"]["agent1"]["gpus"] = [0]
        with self.assertRaisesRegex(SystemExit, "assigned to both"):
            DISCOVERY.validate_resource_config_data(overlap)

    def test_request_schema_rejects_environment_overrides(self) -> None:
        with self.assertRaisesRegex(SystemExit, "unsupported fields: env"):
            DISCOVERY.normalize_resource_request({"cpus": 1, "memory_gb": 1, "gpus": [], "env": {"OMP_NUM_THREADS": "99"}})

    def test_runner_uses_cgroup_limits_and_derived_thread_environment(self) -> None:
        request = {"cpus": 3, "memory_gb": 2.0, "gpus": [1], "timeout_seconds": None}
        with mock.patch.object(DISCOVERY.shutil, "which", return_value="/usr/bin/systemd-run"):
            command = DISCOVERY.resource_runner_command(["python3", "task.py"], request)
        self.assertIn("CPUQuota=300%", command)
        self.assertIn(f"MemoryMax={2 * 1024**3}", command)
        env = DISCOVERY.build_resource_env(request, [1])
        self.assertEqual(env["OMP_NUM_THREADS"], "3")
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "3")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "1")


class CapacityWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "problem-a"
        (self.workspace / ".DiscoveryConsole" / "pub" / "log").mkdir(parents=True)
        DISCOVERY.write_json(self.workspace / "problem.json", {"problem_id": "problem-a"})
        config = resource_config(queue_cpus=2)
        config["evaluation"]["resources"]["cpus"] = 1
        DISCOVERY.write_json(DISCOVERY.resource_config_path(self.workspace), config)
        DISCOVERY.write_json(
            DISCOVERY.dashboard_worker_state_path(self.workspace),
            {"status": "running", "pid": os.getpid(), "current_job": None, "active_jobs": [], "stop_requested_at": None},
        )
        for index in (1, 2):
            DISCOVERY.upsert_job(
                self.workspace,
                {
                    "id": f"job-{index}",
                    "status": "queued",
                    "agent": "agent1",
                    "command": ["/usr/bin/true"],
                    "cwd": str(self.workspace),
                    "resources": {"cpus": 1, "memory_gb": 1, "gpus": [], "timeout_seconds": None},
                    "log": f".DiscoveryConsole/pub/log/job-{index}.log",
                    "launcher": "submit",
                },
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_worker_starts_every_job_that_fits_capacity(self) -> None:
        fake_processes = [mock.Mock(pid=101), mock.Mock(pid=102)]
        with mock.patch.object(DISCOVERY.subprocess, "Popen", side_effect=fake_processes) as popen, mock.patch.object(
            DISCOVERY, "process_alive", return_value=True
        ), mock.patch.object(DISCOVERY.time, "sleep", side_effect=RuntimeError("stop test loop")):
            with self.assertRaisesRegex(RuntimeError, "stop test loop"):
                DISCOVERY._run_worker_loop(self.workspace, argparse.Namespace(once=False, poll_seconds=0.01))
        self.assertEqual(popen.call_count, 2)
        self.assertEqual({job["status"] for job in DISCOVERY.read_jsonl(DISCOVERY.job_index(self.workspace))}, {"starting"})
        self.assertEqual(len(DISCOVERY.read_resource_state(self.workspace)["leases"]), 2)


if __name__ == "__main__":
    unittest.main()
