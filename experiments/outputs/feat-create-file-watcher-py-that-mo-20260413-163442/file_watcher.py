"""File watcher module for monitoring directory changes with glob pattern support and debouncing."""

import os
import time
import fnmatch
from pathlib import Path
from threading import Thread, Lock
from queue import Queue, Empty
from typing import Callable, Dict, List, Optional, Set, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent


class FileWatcher:
    """Monitor directory changes with glob pattern support and debouncing."""

    def __init__(self, debounce_seconds: float = 0.5):
        """
        Initialize the FileWatcher.

        Args:
            debounce_seconds: Time to wait for rapid changes to settle before triggering callback
        """
        self.debounce_seconds = debounce_seconds
        self.observer: Optional[Observer] = None
        self.watch_callbacks: Dict[Any, List[Dict]] = {}
        self.watch_handles: Dict[Any, Any] = {}  # Track watchdog watch handles
        self.lock = Lock()
        self.debounce_timers: Dict[str, float] = {}
        self.event_queue: Queue = Queue()
        self.processing_thread: Optional[Thread] = None
        self.running = False

    def watch(self, path: str, patterns: List[str], callback: Callable[[str, str, Any], None]) -> Any:
        """
        Start watching a directory for changes matching the given patterns.

        Args:
            path: Directory path to watch
            patterns: List of glob patterns to match files (e.g., ['*.txt', '*.log'])
            callback: Function to call when events occur. Receives (event_type, file_path, watch_id)
                      event_type: 'created', 'modified', 'deleted'
                      file_path: Path to the affected file
                      watch_id: Unique identifier for this watch

        Returns:
            watch_id: Unique identifier that can be used to stop watching
        """
        path_obj = Path(path).resolve()
        if not path_obj.exists():
            raise ValueError(f"Path does not exist: {path}")
        if not path_obj.is_dir():
            raise ValueError(f"Path is not a directory: {path}")

        watch_id = id(callback)  # Use callback id as watch identifier

        with self.lock:
            self.watch_callbacks[watch_id] = {
                'path': str(path_obj),
                'patterns': patterns,
                'callback': callback
            }

        if self.observer is None:
            self.observer = Observer()
            self.observer.start()
            self.running = True
            self.processing_thread = Thread(target=self._process_events, daemon=True)
            self.processing_thread.start()

        handler = _FileWatcherEventHandler(
            patterns=patterns,
            debounce_seconds=self.debounce_seconds,
            event_queue=self.event_queue,
            watch_id=watch_id
        )
        watch_handle = self.observer.schedule(handler, str(path_obj), recursive=True)

        with self.lock:
            self.watch_handles[watch_id] = watch_handle

        return watch_id

    def _process_events(self):
        """Process debounced events from the queue in a separate thread."""
        while self.running:
            try:
                event_data = self.event_queue.get(timeout=0.1)
                watch_id = event_data['watch_id']
                event_type = event_data['event_type']
                file_path = event_data['file_path']

                with self.lock:
                    if watch_id in self.watch_callbacks:
                        callback_info = self.watch_callbacks[watch_id]
                        callback = callback_info['callback']
                        try:
                            callback(event_type, file_path, watch_id)
                        except Exception as e:
                            print(f"Error in callback: {e}")
            except Empty:
                continue
            except Exception as e:
                print(f"Error processing event: {e}")

    def stop(self, watch_id: Any = None):
        """
        Stop watching one or all directories.

        Args:
            watch_id: If provided, stop only this watch. Otherwise, stop all watches.
        """
        with self.lock:
            if watch_id is not None:
                # Stop specific watch
                if watch_id in self.watch_handles:
                    watch_handle = self.watch_handles[watch_id]
                    if self.observer:
                        try:
                            self.observer.unschedule(watch_handle)
                        except Exception:
                            pass  # Watch may have already been removed
                    del self.watch_handles[watch_id]
                if watch_id in self.watch_callbacks:
                    del self.watch_callbacks[watch_id]
            else:
                # Stop all watches
                if self.observer:
                    for watch_handle in self.watch_handles.values():
                        try:
                            self.observer.unschedule(watch_handle)
                        except Exception:
                            pass  # Watch may have already been removed
                self.watch_handles.clear()
                self.watch_callbacks.clear()

        # Stop observer if no more watches
        if not self.watch_callbacks and self.observer is not None:
            self.running = False
            self.observer.stop()
            self.observer.join()
            self.observer = None
            if self.processing_thread:
                self.processing_thread.join(timeout=1.0)
                self.processing_thread = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


class _FileWatcherEventHandler(FileSystemEventHandler):
    """Internal event handler for file system events."""

    def __init__(self, patterns: List[str], debounce_seconds: float,
                 event_queue: Queue, watch_id: Any):
        super().__init__()
        self.patterns = patterns
        self.debounce_seconds = debounce_seconds
        self.event_queue = event_queue
        self.watch_id = watch_id
        self.pending_events: Dict[str, Dict] = {}
        self.last_event_time: Dict[str, float] = {}
        self.lock = Lock()

    def _matches_pattern(self, file_path: str) -> bool:
        """Check if file path matches any of the patterns."""
        filename = os.path.basename(file_path)
        return any(fnmatch.fnmatch(filename, pattern) for pattern in self.patterns)

    def _process_event(self, event_type: str, event: FileSystemEvent):
        """Process a file system event with debouncing."""
        file_path = event.src_path

        if not self._matches_pattern(file_path):
            return

        current_time = time.time()

        with self.lock:
            # Check if this event should be debounced
            last_time = self.last_event_time.get(file_path, 0)
            time_since_last = current_time - last_time

            # Update the event type and time
            self.pending_events[file_path] = {
                'event_type': event_type,
                'file_path': file_path,
                'watch_id': self.watch_id
            }
            self.last_event_time[file_path] = current_time

            # Schedule debounce check
            if time_since_last >= self.debounce_seconds:
                # Enough time has passed, process immediately
                self._flush_event(file_path)

    def _flush_event(self, file_path: str):
        """Flush a pending event to the queue."""
        if file_path in self.pending_events:
            event_data = self.pending_events[file_path]
            self.event_queue.put(event_data)
            del self.pending_events[file_path]

    def on_created(self, event: FileSystemEvent):
        """Handle file/directory creation events."""
        if not event.is_directory:
            self._process_event('created', event)

    def on_modified(self, event: FileSystemEvent):
        """Handle file/directory modification events."""
        if not event.is_directory:
            self._process_event('modified', event)

    def on_deleted(self, event: FileSystemEvent):
        """Handle file/directory deletion events."""
        if not event.is_directory:
            self._process_event('deleted', event)
            # Clean up pending events for deleted file
            with self.lock:
                if event.src_path in self.pending_events:
                    del self.pending_events[event.src_path]
                if event.src_path in self.last_event_time:
                    del self.last_event_time[event.src_path]
