"""
功能：验证 PersistentInMemorySaver 的文件持久化和线程复制逻辑。
作用：确保 HITL checkpoint 在本地文件落盘后可恢复和裁剪。
实现方式：通过 unittest 操作内部存储结构并检查持久化文件快照。
"""

import os
import pickle
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.persistent_checkpointer import PersistentInMemorySaver


class PersistentCheckpointerTest(unittest.TestCase):
    def test_load_restores_storage_writes_and_blobs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "transport_order.pkl")
            snapshot = {
                "storage": {
                    "thread-1": {"ns": {"cp-1": (b"checkpoint", b"metadata", None)}}
                },
                "writes": {("thread-1", "ns", "cp-1"): {"status": "ok"}},
                "blobs": {("thread-1", "ns", "channel", "v1"): b"blob"},
            }
            with open(path, "wb") as file:
                pickle.dump(snapshot, file, protocol=pickle.HIGHEST_PROTOCOL)

            saver = PersistentInMemorySaver(path)

            self.assertIn("thread-1", saver.storage)
            self.assertIn("ns", saver.storage["thread-1"])
            self.assertIn("cp-1", saver.storage["thread-1"]["ns"])
            self.assertEqual(saver.writes[("thread-1", "ns", "cp-1")]["status"], "ok")
            self.assertEqual(saver.blobs[("thread-1", "ns", "channel", "v1")], b"blob")

    def test_copy_thread_duplicates_storage_writes_and_blobs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "transport_order.pkl")
            saver = PersistentInMemorySaver(path)
            saver.storage["source"]["ns"] = {"cp-1": (b"checkpoint", b"metadata", None)}
            saver.writes[("source", "ns", "cp-1")] = {"status": "ok"}
            saver.blobs[("source", "ns", "channel", "v1")] = b"blob"

            saver.copy_thread("source", "target")

            self.assertIn("target", saver.storage)
            self.assertEqual(
                saver.storage["target"]["ns"]["cp-1"],
                (b"checkpoint", b"metadata", None),
            )
            self.assertEqual(saver.writes[("target", "ns", "cp-1")]["status"], "ok")
            self.assertEqual(saver.blobs[("target", "ns", "channel", "v1")], b"blob")

    def test_prune_keep_latest_removes_older_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "transport_order.pkl")
            saver = PersistentInMemorySaver(path)
            saver.storage["thread-1"]["ns"] = {
                "cp-1": (b"checkpoint-1", b"metadata-1", None),
                "cp-2": (b"checkpoint-2", b"metadata-2", None),
            }
            saver.writes[("thread-1", "ns", "cp-1")] = {"old": True}
            saver.writes[("thread-1", "ns", "cp-2")] = {"latest": True}

            saver.prune(["thread-1"], strategy="keep_latest")

            self.assertNotIn("cp-1", saver.storage["thread-1"]["ns"])
            self.assertIn("cp-2", saver.storage["thread-1"]["ns"])
            self.assertNotIn(("thread-1", "ns", "cp-1"), saver.writes)
            self.assertIn(("thread-1", "ns", "cp-2"), saver.writes)


if __name__ == "__main__":
    unittest.main()
