"""Tests for file_watcher module."""

import os
import time
import tempfile
import shutil
from pathlib import Path
import pytest

from file_watcher import FileWatcher


class TestFileWatcher:
    """Test suite for FileWatcher class."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.test_dir = tempfile.mkdtemp()
        self.events = []

    def teardown_method(self):
        """Clean up test fixtures after each test method."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_callback(self):
        """Create a callback that records events."""
        def callback(event_type, file_path, watch_id):
            self.events.append({
                'type': event_type,
                'path': file_path,
                'watch_id': watch_id
            })
        return callback

    def _wait_for_events(self, expected_count: int, timeout: float = 2.0):
        """Wait for expected number of events to be recorded."""
        start_time = time.time()
        while len(self.events) < expected_count:
            time.sleep(0.05)
            if time.time() - start_time > timeout:
                raise TimeoutError(f"Timeout waiting for {expected_count} events, got {len(self.events)}")

    def test_file_creation_detection(self):
        """Test that file creation events are detected."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['*.txt'], callback)

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('content')

        self._wait_for_events(1)

        assert len(self.events) == 1
        assert self.events[0]['type'] == 'created'
        assert self.events[0]['path'] == str(test_file)

        watcher.stop()

    def test_file_modification_detection(self):
        """Test that file modification events are detected."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('initial')

        watcher.watch(self.test_dir, ['*.txt'], callback)

        time.sleep(0.2)  # Let initial creation settle
        test_file.write_text('modified')

        self._wait_for_events(1)

        assert len(self.events) == 1
        assert self.events[0]['type'] == 'modified'

        watcher.stop()

    def test_file_deletion_detection(self):
        """Test that file deletion events are detected."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('content')

        watcher.watch(self.test_dir, ['*.txt'], callback)

        time.sleep(0.2)  # Let initial creation settle
        test_file.unlink()

        self._wait_for_events(1)

        assert len(self.events) == 1
        assert self.events[0]['type'] == 'deleted'

        watcher.stop()

    def test_glob_pattern_matching(self):
        """Test that glob patterns correctly filter files."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['*.txt'], callback)

        # Create matching file
        txt_file = Path(self.test_dir) / 'test.txt'
        txt_file.write_text('content')

        # Create non-matching file
        log_file = Path(self.test_dir) / 'test.log'
        log_file.write_text('content')

        self._wait_for_events(1)

        assert len(self.events) == 1
        assert '.txt' in self.events[0]['path']

        watcher.stop()

    def test_multiple_glob_patterns(self):
        """Test that multiple glob patterns are supported."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['*.txt', '*.log'], callback)

        txt_file = Path(self.test_dir) / 'test.txt'
        txt_file.write_text('content')

        log_file = Path(self.test_dir) / 'test.log'
        log_file.write_text('content')

        py_file = Path(self.test_dir) / 'test.py'
        py_file.write_text('content')

        self._wait_for_events(2)

        assert len(self.events) == 2
        paths = [event['path'] for event in self.events]
        assert any('.txt' in p for p in paths)
        assert any('.log' in p for p in paths)
        assert not any('.py' in p for p in paths)

        watcher.stop()

    def test_debounce_rapid_changes(self):
        """Test that rapid changes are debounced."""
        watcher = FileWatcher(debounce_seconds=0.3)
        callback = self._create_callback()

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('initial')

        watcher.watch(self.test_dir, ['*.txt'], callback)

        time.sleep(0.2)  # Let initial creation settle
        self.events.clear()

        # Make rapid changes
        for i in range(5):
            test_file.write_text(f'content {i}')
            time.sleep(0.05)

        # Wait for debounce period
        time.sleep(0.5)

        # Should only get one modified event due to debouncing
        assert len(self.events) <= 2  # Allow for timing edge cases

        watcher.stop()

    def test_multiple_watches_different_directories(self):
        """Test watching multiple different directories."""
        test_dir2 = tempfile.mkdtemp()

        try:
            watcher = FileWatcher(debounce_seconds=0.1)
            callback = self._create_callback()

            watch_id1 = watcher.watch(self.test_dir, ['*.txt'], callback)
            watch_id2 = watcher.watch(test_dir2, ['*.log'], callback)

            file1 = Path(self.test_dir) / 'test.txt'
            file1.write_text('content')

            file2 = Path(test_dir2) / 'test.log'
            file2.write_text('content')

            self._wait_for_events(2)

            assert len(self.events) == 2
            watch_ids = {event['watch_id'] for event in self.events}
            assert watch_id1 in watch_ids
            assert watch_id2 in watch_ids

            watcher.stop()
        finally:
            if os.path.exists(test_dir2):
                shutil.rmtree(test_dir2)

    def test_stop_specific_watch(self):
        """Test stopping a specific watch."""
        test_dir2 = tempfile.mkdtemp()

        try:
            watcher = FileWatcher(debounce_seconds=0.1)
            callback = self._create_callback()

            watch_id1 = watcher.watch(self.test_dir, ['*.txt'], callback)

            # Give watch time to fully initialize
            time.sleep(0.3)

            # Stop the watch
            watcher.stop(watch_id1)

            time.sleep(0.3)

            # Create file in the watched directory (should not trigger since we stopped)
            file1 = Path(self.test_dir) / 'test.txt'
            file1.write_text('content')

            # Wait a bit - should not receive any events
            time.sleep(0.5)

            assert len(self.events) == 0, "Should not receive events after stopping watch"

            watcher.stop()
        finally:
            if os.path.exists(test_dir2):
                shutil.rmtree(test_dir2)

    def test_stop_all_watches(self):
        """Test stopping all watches."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['*.txt'], callback)
        watcher.watch(self.test_dir, ['*.log'], callback)

        watcher.stop()

        time.sleep(0.2)

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('content')

        # Should not receive any events after stopping
        time.sleep(0.3)
        assert len(self.events) == 0

    def test_context_manager_usage(self):
        """Test using FileWatcher as a context manager."""
        callback = self._create_callback()

        with FileWatcher(debounce_seconds=0.1) as watcher:
            watcher.watch(self.test_dir, ['*.txt'], callback)

            test_file = Path(self.test_dir) / 'test.txt'
            test_file.write_text('content')

            self._wait_for_events(1)

        # After exiting context, no more events should be received
        time.sleep(0.2)
        test_file.write_text('modified')
        time.sleep(0.3)

        # Only the creation event should be recorded
        assert len(self.events) == 1
        assert self.events[0]['type'] == 'created'

    def test_callback_receives_correct_parameters(self):
        """Test that callback receives all expected parameters."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watch_id = watcher.watch(self.test_dir, ['*.txt'], callback)

        test_file = Path(self.test_dir) / 'test.txt'
        test_file.write_text('content')

        self._wait_for_events(1)

        assert len(self.events) == 1
        event = self.events[0]
        assert 'type' in event
        assert 'path' in event
        assert 'watch_id' in event
        assert event['watch_id'] == watch_id

        watcher.stop()

    def test_nonexistent_path_raises_error(self):
        """Test that watching a non-existent path raises ValueError."""
        watcher = FileWatcher()
        callback = self._create_callback()

        with pytest.raises(ValueError, match="Path does not exist"):
            watcher.watch('/nonexistent/path/12345', ['*.txt'], callback)

    def test_non_directory_path_raises_error(self):
        """Test that watching a file instead of directory raises ValueError."""
        test_file = Path(self.test_dir) / 'file.txt'
        test_file.write_text('content')

        watcher = FileWatcher()
        callback = self._create_callback()

        with pytest.raises(ValueError, match="Path is not a directory"):
            watcher.watch(str(test_file), ['*.txt'], callback)

    def test_subdirectory_recursive_watching(self):
        """Test that subdirectories are watched recursively."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['*.txt'], callback)

        # Create file in subdirectory
        subdir = Path(self.test_dir) / 'subdir'
        subdir.mkdir()
        test_file = subdir / 'test.txt'
        test_file.write_text('content')

        self._wait_for_events(1)

        assert len(self.events) == 1
        assert 'test.txt' in self.events[0]['path']

        watcher.stop()

    def test_pattern_matches_with_wildcards(self):
        """Test glob pattern matching with various wildcard patterns."""
        watcher = FileWatcher(debounce_seconds=0.1)
        callback = self._create_callback()

        watcher.watch(self.test_dir, ['test_*.txt'], callback)

        # Should match
        (Path(self.test_dir) / 'test_1.txt').write_text('content')
        (Path(self.test_dir) / 'test_abc.txt').write_text('content')

        # Should not match
        (Path(self.test_dir) / 'other.txt').write_text('content')
        (Path(self.test_dir) / 'test.txt').write_text('content')

        self._wait_for_events(2)

        assert len(self.events) == 2
        for event in self.events:
            assert 'test_' in event['path']
            assert '.txt' in event['path']

        watcher.stop()
