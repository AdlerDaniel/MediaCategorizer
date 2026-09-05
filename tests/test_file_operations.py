import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_categorizer.file_operations import (
    FileReservations, PublishedMoveError, perform_file_operation, rename_no_replace,
)


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.bin"
        self.target = self.root / "destination.bin"
        self.source.write_bytes(b"original data" * 1024)

    def assert_no_partials(self):
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_copy_publishes_complete_file_only(self):
        observations = []
        def progress(copied, total):
            observations.append((copied, total, self.target.exists()))
        perform_file_operation("copy", self.source, self.target, progress)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertFalse(observations[0][2])
        self.assertTrue(observations[-1][2])
        self.assert_no_partials()

    def test_existing_destination_survives_copy_and_move(self):
        self.target.write_bytes(b"unrelated file")
        for kind in ("copy", "move"):
            with self.subTest(kind=kind), self.assertRaises(FileExistsError):
                perform_file_operation(kind, self.source, self.target)
            self.assertEqual(self.target.read_bytes(), b"unrelated file")
            self.assertTrue(self.source.exists())
        self.assert_no_partials()

    def test_destination_created_during_copy_survives(self):
        def competing_writer(*_):
            self.target.write_bytes(b"created by another application")
        with self.assertRaises(FileExistsError):
            perform_file_operation("copy", self.source, self.target, competing_writer)
        self.assertEqual(self.target.read_bytes(), b"created by another application")
        self.assertTrue(self.source.exists())
        self.assert_no_partials()

    def test_failed_copy_removes_only_its_temporary_file(self):
        def disk_error(*_):
            raise OSError("simulated write failure")
        with self.assertRaises(OSError):
            perform_file_operation("copy", self.source, self.target, disk_error)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assert_no_partials()

    def test_metadata_failure_does_not_publish_incomplete_result(self):
        with patch("media_categorizer.file_operations.shutil.copystat", side_effect=OSError("metadata")):
            with self.assertRaises(OSError):
                perform_file_operation("copy", self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.source.exists())
        self.assert_no_partials()

    def test_readonly_source_conflict_still_cleans_temporary_file(self):
        original_metadata_copy = __import__("shutil").copystat
        def readonly_metadata(source, target):
            original_metadata_copy(source, target)
            target.chmod(0o444)
            self.target.write_bytes(b"external file")
        with patch("media_categorizer.file_operations.shutil.copystat", side_effect=readonly_metadata):
            with self.assertRaises(FileExistsError):
                perform_file_operation("copy", self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"external file")
        self.assert_no_partials()

    def test_same_volume_move_does_not_copy_bytes(self):
        with patch("media_categorizer.file_operations.tempfile.mkstemp", side_effect=AssertionError("copy")):
            perform_file_operation("move", self.source, self.target)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b"original data" * 1024)

    def test_cross_volume_move_copies_then_removes_source(self):
        with patch("media_categorizer.file_operations._same_device", return_value=False):
            perform_file_operation("move", self.source, self.target)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b"original data" * 1024)
        self.assert_no_partials()

    def test_source_removal_failure_preserves_both_files(self):
        original_unlink = Path.unlink
        def locked_source(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError("source is locked")
            return original_unlink(path, *args, **kwargs)
        with patch("media_categorizer.file_operations._same_device", return_value=False), \
             patch.object(Path, "unlink", locked_source), self.assertRaises(PublishedMoveError):
            perform_file_operation("move", self.source, self.target)
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
        self.assert_no_partials()

    def test_rename_and_move_back_never_overwrite(self):
        rename_no_replace(self.source, self.target)
        self.source.write_bytes(b"new occupant of original name")
        with self.assertRaises(FileExistsError):
            rename_no_replace(self.target, self.source)
        with self.assertRaises(FileExistsError):
            perform_file_operation("move", self.target, self.source)
        self.assertEqual(self.source.read_bytes(), b"new occupant of original name")
        self.assertEqual(self.target.read_bytes(), b"original data" * 1024)

    def test_reservations_cover_source_destination_and_release(self):
        reservations = FileReservations()
        reservations.acquire("first", self.source, self.target)
        self.assertTrue(reservations.is_busy(self.source))
        self.assertTrue(reservations.is_busy(self.root / "." / "destination.bin"))
        with self.assertRaises(RuntimeError):
            reservations.acquire("second", self.target, self.root / "other.bin")
        reservations.release("first")
        self.assertFalse(reservations.is_busy(self.source, self.target))

    def test_hardlink_alias_is_busy(self):
        alias = self.root / "alias.bin"
        try:
            os.link(self.source, alias)
        except OSError as exc:
            self.skipTest(str(exc))
        reservations = FileReservations()
        reservations.acquire("copy", self.source, self.target)
        self.assertTrue(reservations.is_busy(alias))


if __name__ == "__main__":
    unittest.main()
