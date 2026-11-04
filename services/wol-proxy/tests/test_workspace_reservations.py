import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import yaml

from test_interlock import FakeKubernetes
from wol_proxy.interlock import Interlock, InterlockError
from wol_proxy.workspace import WorkspaceBusy, WorkspaceReservations


class WorkspaceReservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = FakeKubernetes()
        self.api.data["workspace_records"] = "{}"
        self.interlock = Interlock(self.api)
        await self.interlock.claim()
        await self.interlock.transition("idle", "releasing")
        self.reservations = WorkspaceReservations(self.interlock, ("desktop", "gpu"))

    async def test_reserves_workspace_for_one_pod_and_both_possible_workers(self):
        expected = {
            "claim_uid": "claim-uid", "pod_name": "run-1", "pod_uid": "pod-uid",
            "possible_nodes": ["desktop", "gpu"],
        }
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        self.assertEqual(await self.reservations.owner("task-df-1"), expected)

    async def test_competing_pods_cannot_reserve_the_same_workspace(self):
        original_get = self.api._get
        both_read = asyncio.Event()
        count = 0

        async def synchronized_get(path):
            nonlocal count
            snapshot = await original_get(path)
            count += 1
            if count == 2:
                both_read.set()
            await both_read.wait()
            return snapshot

        self.api._get = synchronized_get
        results = await asyncio.gather(
            self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1"),
            self.reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        record = await self.reservations.owner("task-df-1")
        self.assertIn(record["pod_uid"], ("pod-1", "pod-2"))
        with self.assertRaises(InterlockError):
            await self.reservations.reserve("task-df-1", "claim-uid", "other", "other-uid")

    async def test_lost_response_keeps_reservation_and_retries_idempotently(self):
        original_put = self.api._put

        async def lost_response(path, body):
            await original_put(path, body)
            raise ConnectionError("response lost")

        self.api._put = lost_response
        with self.assertRaises(ConnectionError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        self.api._put = original_put
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        with self.assertRaises(InterlockError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-2", "another-uid")

    async def test_read_only_match_requires_pod_claim_and_full_worker_pool(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        self.assertTrue(await self.reservations.matches("task-df-1", "claim-uid", "run-1", "pod-uid"))
        for args in (("task-df-1", "other-claim", "run-1", "pod-uid"),
                     ("task-df-1", "claim-uid", "run-1", "other-pod"),
                     ("task-df-1", "claim-uid", "other-run", "pod-uid"),
                     ("task-df-2", "claim-uid", "run-1", "pod-uid")):
            with self.subTest(args=args):
                self.assertFalse(await self.reservations.matches(*args))
        subset = WorkspaceReservations(self.interlock, ("desktop",))
        self.assertFalse(await subset.matches("task-df-1", "claim-uid", "run-1", "pod-uid"))

    async def test_restart_and_recreated_interlock_do_not_authorize_takeover(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        with self.assertRaises(InterlockError):
            await Interlock(self.api).claim()
        self.api.uid = "replacement-uid"
        with self.assertRaises(InterlockError):
            await self.reservations.reserve("task-df-2", "claim-2", "run-2", "pod-2")

    async def test_restart_in_idle_preserves_record_and_requires_normal_handoff(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1")
        await self.interlock.transition("releasing", "idle")
        restarted = Interlock(self.api)
        await restarted.claim()
        await restarted.transition("idle", "releasing")
        reservations = WorkspaceReservations(restarted, ("desktop", "gpu"))
        self.assertEqual((await reservations.owner("task-df-1"))["pod_uid"], "pod-1")
        with self.assertRaises(WorkspaceBusy):
            await reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2",
                                       previous_gone=AsyncMock(return_value=False))
        self.assertEqual((await reservations.owner("task-df-1"))["pod_uid"], "pod-1")

    async def test_independent_tasks_keep_independent_reservations(self):
        await self.reservations.reserve("task-df-1", "claim-1", "run-1", "pod-1")
        await self.reservations.reserve("task-df-2", "claim-2", "run-2", "pod-2")
        self.assertEqual((await self.reservations.owner("task-df-1"))["pod_uid"], "pod-1")
        self.assertEqual((await self.reservations.owner("task-df-2"))["pod_uid"], "pod-2")

    async def test_lost_owner_never_reserves_even_if_old_state_returns(self):
        self.interlock.lost = True
        with self.assertRaises(InterlockError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")

    async def test_normal_handoff_replaces_owner_only_after_previous_pod_is_gone(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1")
        previous_gone = AsyncMock(return_value=False)
        with self.assertRaises(WorkspaceBusy):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2", previous_gone=previous_gone)
        self.assertEqual((await self.reservations.owner("task-df-1"))["pod_uid"], "pod-1")
        previous_gone.return_value = True
        await self.reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2", previous_gone=previous_gone)
        self.assertEqual((await self.reservations.owner("task-df-1"))["pod_uid"], "pod-2")

    async def test_handoff_refuses_api_errors(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1")
        previous_gone = AsyncMock(side_effect=OSError("API unavailable"))
        with self.assertRaises(OSError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2", previous_gone=previous_gone)
        self.assertEqual((await self.reservations.owner("task-df-1"))["pod_uid"], "pod-1")

    async def test_fresh_claim_generation_waits_for_previous_pod(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1")
        previous_gone = AsyncMock(return_value=False)
        with self.assertRaises(WorkspaceBusy):
            await self.reservations.reserve("task-df-1", "replacement-claim", "run-2", "pod-2", previous_gone=previous_gone)
        self.assertEqual((await self.reservations.owner("task-df-1"))["claim_uid"], "claim-uid")
        previous_gone.return_value = True
        await self.reservations.reserve("task-df-1", "replacement-claim", "run-2", "pod-2", previous_gone=previous_gone)
        self.assertEqual((await self.reservations.owner("task-df-1"))["claim_uid"], "replacement-claim")

    async def test_handoff_uses_resource_version_after_check(self):
        await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-1")

        async def stale_check(previous):
            self.api.revision += 1
            return True

        with self.assertRaises(RuntimeError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-2", "pod-2", previous_gone=stale_check)
        self.assertEqual((await self.reservations.owner("task-df-1"))["pod_uid"], "pod-1")

    async def test_stale_version_does_not_write_reservation(self):
        original_put = self.api._put

        async def conflict(path, body):
            self.api.revision += 1
            return await original_put(path, body)

        self.api._put = conflict
        with self.assertRaises(RuntimeError):
            await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")
        self.assertEqual(json.loads(self.api.data["workspace_records"]), {})

    async def test_missing_or_corrupt_reservations_fail_closed(self):
        for raw in (None, "not-json", "[]", '{"task-other": null}', '{"task-other":{},"task-other":{}}',
                    '{"task-other":{"claim_uid":"claim"}}'):
            with self.subTest(raw=raw):
                if raw is None:
                    self.api.data.pop("workspace_records", None)
                else:
                    self.api.data["workspace_records"] = raw
                with self.assertRaises(InterlockError):
                    await self.reservations.reserve("task-df-1", "claim-uid", "run-1", "pod-uid")

    async def test_invalid_pool_is_rejected(self):
        for nodes in ((), ("desktop", "desktop"), ("desktop", ""), ("desktop", 42), "desktop"):
            with self.subTest(nodes=nodes):
                with self.assertRaises(ValueError):
                    WorkspaceReservations(self.interlock, nodes)


class WorkspaceInitialStateTests(unittest.TestCase):
    def test_initial_manifest_provides_empty_reservations_without_overwriting_live_data(self):
        root = Path(__file__).resolve().parents[3]
        objects = list(yaml.safe_load_all((root / "apps/dark-factory-wake/interlock.yaml").read_text()))
        configmap = next(obj for obj in objects if obj["kind"] == "ConfigMap")
        self.assertEqual(configmap["data"]["workspace_records"], "{}")
        app = yaml.safe_load((root / "apps/_argocd/dark-factory-wake-app.yaml").read_text())
        differences = app["spec"]["ignoreDifferences"]
        self.assertTrue(any(item.get("kind") == "ConfigMap" and item.get("name") == "dark-factory-interlock"
                            and "/data" in item.get("jsonPointers", []) for item in differences))
        self.assertIn("RespectIgnoreDifferences=true", app["spec"]["syncPolicy"]["syncOptions"])
