import tempfile
import unittest
from pathlib import Path

from core.media_store import MediaStore, MediaStoreError


class MediaStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "media"
        self.store = MediaStore(self.root)
        self.source = Path(self.tmp.name) / "sample.jpg"
        self.source.write_bytes(b"not-a-real-jpeg")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_save_image_copy_and_delete(self):
        entry = await self.store.save_image(
            str(self.source), umo="aiocqhttp:group:1", row_id=7
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["type"], "image")
        target = self.store.resolve(entry["path"])
        self.assertEqual(target.read_bytes(), b"not-a-real-jpeg")
        self.store.delete(entry["path"])
        with self.assertRaises(MediaStoreError):
            self.store.resolve(entry["path"])

    async def test_save_audio_copy(self):
        source = Path(self.tmp.name) / "sample.silk"
        source.write_bytes(b"audio")
        entry = await self.store.save_audio(
            str(source), umo="aiocqhttp:group:1", row_id=8
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["type"], "audio")
        self.assertEqual(self.store.resolve(entry["path"]).read_bytes(), b"audio")

    async def test_path_traversal_rejected(self):
        with self.assertRaises(MediaStoreError):
            self.store.resolve("../outside.jpg")


if __name__ == "__main__":
    unittest.main()
