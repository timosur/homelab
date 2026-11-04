#!/usr/bin/python3
"""Host-side fencing primitive for Dark Factory sleep requests.

Only a trusted systemd post-sleep hook may invoke ``complete``. All host suspension
must go through this guard for a completion to identify the guard's own request.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


class GuardError(Exception):
    pass


class PowerGuard:
    def __init__(self, directory: Path, runner=subprocess.run):
        self.directory = directory
        self.runner = runner

    def initialize(self) -> None:
        """Initialize once; never repair or reset a missing existing state file."""
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            with self._locked():
                self._read()
            return
        with self._locked():
            self._write({"epoch": 0, "owner": "", "anchor": "", "phase": "ready"})

    @contextmanager
    def _locked(self):
        if not self.directory.is_dir():
            raise GuardError("power guard is not initialized")
        with (self.directory / "lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            state = json.loads((self.directory / "state.json").read_text())
        except (OSError, ValueError) as exc:
            raise GuardError("power guard state is unavailable or invalid") from exc
        if (
            not isinstance(state, dict)
            or type(state.get("epoch")) is not int
            or state["epoch"] < 0
            or not isinstance(state.get("owner"), str)
            or not isinstance(state.get("anchor"), str)
            or state.get("phase") not in ("ready", "suspending")
        ):
            raise GuardError("power guard state is invalid")
        return state

    def _write(self, state: dict) -> None:
        # State and containing directory must reach disk before a power operation.
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.directory / "state.json")
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def status(self) -> dict:
        with self._locked():
            return self._read()

    @staticmethod
    def _identity(epoch: int, owner: str, anchor: str) -> None:
        if (
            type(epoch) is not int or epoch <= 0
            or not isinstance(owner, str) or not re.fullmatch(r"[0-9a-f]{32}", owner)
            or not isinstance(anchor, str) or not re.fullmatch(r"[0-9a-f-]{36}", anchor)
        ):
            raise GuardError("invalid fencing identity")

    def fence(self, epoch: int, owner: str, anchor: str) -> dict:
        self._identity(epoch, owner, anchor)
        with self._locked():
            state = self._read()
            if epoch < state["epoch"]:
                raise GuardError("stale epoch")
            if state["anchor"] and state["anchor"] != anchor:
                raise GuardError("interlock identity changed")
            if epoch == state["epoch"] and state["owner"] != owner:
                raise GuardError("epoch belongs to another owner")
            if state["phase"] != "ready":
                raise GuardError("suspend is unresolved")
            if epoch != state["epoch"]:
                state["epoch"] = epoch
                state["owner"] = owner
                state["anchor"] = anchor
                self._write(state)
            return state

    def suspend(self, epoch: int, owner: str, anchor: str) -> dict:
        self._identity(epoch, owner, anchor)
        with self._locked():
            state = self._read()
            if epoch != state["epoch"] or owner != state["owner"] or anchor != state["anchor"]:
                raise GuardError("stale or invalid epoch")
            if state["phase"] != "ready":
                raise GuardError("suspend is unresolved")
            state["phase"] = "suspending"
            self._write(state)
            try:
                result = self.runner(
                    ["/usr/bin/systemctl", "suspend"], timeout=30, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise GuardError("suspend outcome is unknown") from exc
            if result.returncode != 0:
                raise GuardError("suspend outcome is unknown")
            # systemctl only confirms enqueueing. The post-sleep hook clears the phase.
            return state

    def complete(self) -> dict:
        with self._locked():
            state = self._read()
            if state["phase"] == "suspending":
                state["phase"] = "ready"
                self._write(state)
            return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Host power fencing for Dark Factory")
    parser.add_argument("operation", choices=("init", "status", "fence", "suspend", "complete"))
    parser.add_argument("epoch", nargs="?", type=int)
    parser.add_argument("owner", nargs="?")
    parser.add_argument("anchor", nargs="?")
    args = parser.parse_args()
    guard = PowerGuard(Path("/var/lib/dark-factory-power-guard"))
    try:
        if args.operation == "init" and args.epoch is None:
            guard.initialize()
            state = guard.status()
        elif args.operation == "status" and args.epoch is None:
            state = guard.status()
        elif args.operation == "complete" and args.epoch is None:
            state = guard.complete()
        elif args.operation == "fence" and args.epoch is not None and args.owner and args.anchor:
            state = guard.fence(args.epoch, args.owner, args.anchor)
        elif args.operation == "suspend" and args.epoch is not None and args.owner and args.anchor:
            state = guard.suspend(args.epoch, args.owner, args.anchor)
        else:
            raise GuardError("invalid arguments")
        print(json.dumps(state))
        return 0
    except GuardError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
