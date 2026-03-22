from __future__ import annotations

import copy
import pickle
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.memory import InMemorySaver


class PersistentInMemorySaver(InMemorySaver):
    """A lightweight file-backed saver for local HITL durability."""

    def __init__(self, path: str):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._load()

    def _empty_storage(self):
        return defaultdict(lambda: defaultdict(dict))

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            with self.path.open("rb") as file:
                snapshot = pickle.load(file)
            self.storage = self._empty_storage()
            for thread_id, namespaces in snapshot.get("storage", {}).items():
                namespace_map = defaultdict(dict)
                for checkpoint_ns, checkpoints in namespaces.items():
                    namespace_map[checkpoint_ns] = dict(checkpoints)
                self.storage[thread_id] = namespace_map
            self.writes = defaultdict(dict, snapshot.get("writes", {}))
            self.blobs = dict(snapshot.get("blobs", {}))

    def _persist(self) -> None:
        snapshot = {
            "storage": {
                thread_id: {checkpoint_ns: dict(checkpoints) for checkpoint_ns, checkpoints in namespaces.items()}
                for thread_id, namespaces in self.storage.items()
            },
            "writes": dict(self.writes),
            "blobs": dict(self.blobs),
        }
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("wb") as file:
            pickle.dump(snapshot, file, protocol=pickle.HIGHEST_PROTOCOL)
        temp_path.replace(self.path)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._persist()
            return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._persist()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._persist()

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        with self._lock:
            if source_thread_id not in self.storage:
                return
            self.storage[target_thread_id] = copy.deepcopy(self.storage[source_thread_id])
            for key, value in list(self.writes.items()):
                if key[0] == source_thread_id:
                    self.writes[(target_thread_id, key[1], key[2])] = copy.deepcopy(value)
            for key, value in list(self.blobs.items()):
                if key[0] == source_thread_id:
                    self.blobs[(target_thread_id, key[1], key[2], key[3])] = copy.deepcopy(value)
            self._persist()

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        if not run_ids:
            return
        with self._lock:
            target_runs = set(run_ids)
            for thread_id, namespaces in list(self.storage.items()):
                for checkpoint_ns, checkpoints in list(namespaces.items()):
                    for checkpoint_id, saved in list(checkpoints.items()):
                        metadata = self.serde.loads_typed(saved[1])
                        if metadata.get("run_id") in target_runs:
                            del checkpoints[checkpoint_id]
                            self.writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                    if not checkpoints:
                        del namespaces[checkpoint_ns]
                if not namespaces:
                    self.storage.pop(thread_id, None)
            self._persist()

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        with self._lock:
            for thread_id in thread_ids:
                if thread_id not in self.storage:
                    continue
                if strategy == "delete":
                    super().delete_thread(thread_id)
                    continue
                if strategy != "keep_latest":
                    continue
                namespaces = self.storage[thread_id]
                for checkpoint_ns, checkpoints in list(namespaces.items()):
                    if not checkpoints:
                        continue
                    latest_checkpoint_id = max(checkpoints.keys())
                    for checkpoint_id in list(checkpoints.keys()):
                        if checkpoint_id == latest_checkpoint_id:
                            continue
                        del checkpoints[checkpoint_id]
                        self.writes.pop((thread_id, checkpoint_ns, checkpoint_id), None)
                for key in list(self.blobs.keys()):
                    if key[0] != thread_id:
                        continue
                    if not any(key[1] == ns for ns in namespaces):
                        del self.blobs[key]
            self._persist()
