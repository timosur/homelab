import asyncio
import copy
import unittest

from wol_proxy.interlock import Interlock, InterlockError


class FakeKubernetes:
    def __init__(self):
        self.data = {"epoch": "0", "owner": "", "phase": "idle", "http_active": "0"}
        self.uid = "12345678-1234-1234-1234-123456789abc"
        self.revision = 1

    async def _get(self, path):
        return {"metadata": {"resourceVersion": str(self.revision), "uid": self.uid}, "data": copy.deepcopy(self.data)}

    async def _put(self, path, body):
        if body["metadata"]["resourceVersion"] != str(self.revision):
            raise RuntimeError("conflict")
        self.data = copy.deepcopy(body["data"])
        self.revision += 1
        return {}


class InterlockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeKubernetes()
        self.first = Interlock(self.client)

    async def test_claim_and_release(self):
        state = await self.first.claim()
        self.assertEqual(state["epoch"], 1)
        await self.first.transition("idle", "releasing")
        await self.first.transition("releasing", "idle")
        self.assertEqual(self.client.data["phase"], "idle")

    async def test_takeover_cannot_pass_uncertain_release(self):
        await self.first.claim()
        await self.first.transition("idle", "releasing")
        second = Interlock(self.client)
        with self.assertRaises(InterlockError):
            await second.claim()

    async def test_new_owner_fences_stale_state_changes(self):
        await self.first.claim()
        second = Interlock(self.client)
        await second.claim()
        with self.assertRaises(InterlockError):
            await self.first.transition("idle", "suspending")
        self.assertEqual(second.epoch, 2)

    async def test_takeover_preserves_uncertain_suspend(self):
        await self.first.claim()
        await self.first.transition("idle", "suspending")
        second = Interlock(self.client)
        state = await second.claim()
        self.assertEqual(state["phase"], "suspending")
        self.assertEqual(second.epoch, 2)

    async def test_suspend_request_epoch_is_retired_before_recovery(self):
        await self.first.claim()
        await self.first.transition("idle", "suspending")
        await self.first.retire_suspend_epoch()
        self.assertEqual(self.first.epoch, 2)
        self.assertEqual(self.client.data["phase"], "suspending")

    async def test_recreated_configmap_invalidates_existing_owner(self):
        await self.first.claim()
        self.client.uid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        with self.assertRaises(InterlockError):
            await self.first.transition("idle", "releasing")

    async def test_takeover_waits_for_inflight_http(self):
        await self.first.claim()
        await self.first.http_change(1)
        second = Interlock(self.client)
        with self.assertRaises(InterlockError):
            await second.claim()
        await self.first.http_change(-1)
        await second.claim()

    async def test_http_activity_cannot_begin_during_suspend(self):
        await self.first.claim()
        await self.first.transition("idle", "suspending")
        with self.assertRaises(InterlockError):
            await self.first.http_change(1)

    async def test_concurrent_claims_use_resource_version_as_cas(self):
        client = FakeKubernetes()
        original_get = client._get
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

        client._get = synchronized_get
        results = await asyncio.gather(Interlock(client).claim(), Interlock(client).claim(), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        self.assertEqual(client.data["epoch"], "1")
