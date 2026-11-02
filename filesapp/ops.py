"""File operations on worker threads. Nothing here overwrites or removes data without a decision from the user."""
import os
import re
import threading
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SCAN_ATTRS = "standard::name,standard::type,standard::size"
COPY_FLAGS = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA
NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS

# Conflict answers
REPLACE, MERGE, SKIP, KEEP_BOTH, STOP = "replace", "merge", "skip", "keep-both", "stop"

NO_RENAME = (
    Gio.IOErrorEnum.NOT_SUPPORTED,
    Gio.IOErrorEnum.WOULD_RECURSE,
    Gio.IOErrorEnum.WOULD_MERGE,
)


class Cancelled(Exception):
    pass


def split_name(name):
    match = re.search(r"\.tar\.[A-Za-z0-9]+$", name)
    if match:
        return name[: match.start()], name[match.start():]
    return os.path.splitext(name)


def numbered_name(name, n):
    stem, ext = split_name(name)
    return f"{stem} ({n}){ext}"


def unique_child(folder, name):
    """A child of `folder` that does not exist yet: `name`, or `name (2)`, `name (3)`…"""
    child = folder.get_child(name)
    n = 2
    while child.query_exists(None) or _is_link(child):
        child = folder.get_child(numbered_name(name, n))
        n += 1
    return child


def _is_link(file):
    # query_exists follows links, so a dangling symlink would otherwise look free
    try:
        file.query_info("standard::type", NOFOLLOW, None)
        return True
    except GLib.Error:
        return False


def _type(file):
    return file.query_info("standard::type", NOFOLLOW, None).get_file_type()


def _same_file(a, b):
    """True if two paths are one file, e.g. via a symlinked folder. Copying over it would destroy it."""
    if a.equal(b):
        return True
    try:
        ida = a.query_info("id::file", NOFOLLOW, None).get_attribute_string("id::file")
        idb = b.query_info("id::file", NOFOLLOW, None).get_attribute_string("id::file")
    except GLib.Error:
        return False
    return bool(ida) and ida == idb


def parse_clipboard(mime, text):
    """Returns (is_cut, [uris]) from a gnome-copied-files or uri-list payload."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
    cut = False
    if mime == "x-special/gnome-copied-files" and lines:
        cut = lines[0] == "cut"
        lines = lines[1:]
    return cut, [l for l in lines if not l.startswith("#")]


class Job:
    """One operation. Callbacks run through `dispatch`, which the UI points at the main loop."""

    def __init__(self, kind, sources, dest=None, dispatch=GLib.idle_add,
                 on_update=None, on_conflict=None, on_done=None):
        self.kind = kind  # copy, move, trash, delete, restore, undo-trash, empty-trash
        self.sources = list(sources)
        self.dest = dest
        self.dispatch = dispatch
        self.on_update = on_update
        self.on_conflict = on_conflict
        self.on_done = on_done
        self.cancellable = Gio.Cancellable()
        self.errors = []        # (name, message)
        self.untrashable = []   # files whose filesystem has no trash
        self.trashed = []       # original paths, for undo
        self.done_names = []    # names placed in dest, so the UI can select them
        self.count = 0
        self.total_items = len(self.sources)
        self.bytes_done = 0
        self.bytes_total = 0
        self.cancelled = False
        self._measured = False
        self._rename_fails = False
        self._current = 0
        self._last_update = 0.0
        self._answer = None
        self._answer_set = threading.Event()
        self._sticky = {}       # conflict type -> answer, from "apply to all"

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def run_sync(self):
        self._run()

    def cancel(self):
        self.cancelled = True
        self.cancellable.cancel()
        self._answer_set.set()

    def answer(self, choice, apply_all=False, conflict_type=None):
        if apply_all and conflict_type:
            self._sticky[conflict_type] = choice
        self._answer = choice
        self._answer_set.set()

    @property
    def fraction(self):
        if self.bytes_total > 0:
            return min(1.0, self.bytes_done / self.bytes_total)
        return min(1.0, self.count / self.total_items) if self.total_items else 0.0

    def _emit(self, force=False):
        now = time.monotonic()
        if self.on_update and (force or now - self._last_update > 0.1):
            self._last_update = now
            self.dispatch(self.on_update, self)

    def _run(self):
        try:
            getattr(self, "_do_" + self.kind.replace("-", "_"))()
        except Cancelled:
            self.cancelled = True
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.cancelled = True
            else:
                self.errors.append(("", e.message))
        if self.on_done:
            self.dispatch(self.on_done, self)

    def _check(self):
        if self.cancelled or self.cancellable.is_cancelled():
            raise Cancelled

    def _fail(self, file, error):
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            raise Cancelled
        self.errors.append((file.get_basename() or file.get_uri(), error.message))
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NO_SPACE):
            raise Cancelled  # every later item would fail the same way

    def _children(self, folder):
        en = folder.enumerate_children(SCAN_ATTRS, NOFOLLOW, self.cancellable)
        try:
            while True:
                info = en.next_file(self.cancellable)
                if info is None:
                    return
                yield info
        finally:
            en.close(None)

    def _measure(self, file):
        try:
            info = file.query_info(SCAN_ATTRS, NOFOLLOW, self.cancellable)
        except GLib.Error:
            return
        self._add_size(file, info)

    def _add_size(self, file, info):
        self._check()
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            try:
                for child in self._children(file):
                    self._add_size(file.get_child(child.get_name()), child)
            except GLib.Error:
                pass  # unreadable folders report their error during the transfer
        else:
            self.bytes_total += info.get_size()

    def _ensure_measured(self):
        if not self._measured:
            self._measured = True
            for src in self.sources[self._current:]:
                self._measure(src)

    # ---- copy / move ----

    def _do_copy(self):
        self._transfer_all(move=False)

    def _do_move(self):
        self._transfer_all(move=True)

    def _transfer_all(self, move):
        if not move:
            self._ensure_measured()
        for i, src in enumerate(self.sources):
            self._current = i
            self._check()
            dst = self.dest.get_child(src.get_basename())
            if self.dest.equal(src) or self.dest.has_prefix(src):
                self.errors.append((src.get_basename(), "A folder can't go inside itself."))
                continue
            try:
                placed = self._transfer(src, dst, move)
                if placed:
                    self.done_names.append(placed.get_basename())
            except GLib.Error as e:
                self._fail(src, e)
            self.count += 1
            self._emit(True)

    def _ask(self, src, dst, conflict_type):
        sticky = self._sticky.get(conflict_type)
        if sticky:
            return sticky
        self._answer_set.clear()
        self._answer = None
        self.dispatch(self.on_conflict, self, src, dst, conflict_type)
        while not self._answer_set.wait(0.1):
            pass
        self._check()
        return self._answer or STOP

    def _transfer(self, src, dst, move):
        """Moves or copies one item. Returns the destination, or None if it was skipped or failed."""
        self._check()
        src_type = _type(src)
        overwrite = merge = False

        if _is_link(dst):
            if _same_file(src, dst):
                if move:
                    return None  # moving a file onto itself changes nothing
                dst = unique_child(dst.get_parent(), dst.get_basename())
            else:
                dst_type = _type(dst)
                src_dir = src_type == Gio.FileType.DIRECTORY
                dst_dir = dst_type == Gio.FileType.DIRECTORY
                conflict = "folders" if src_dir and dst_dir else "mixed" if src_dir or dst_dir else "files"
                choice = self._ask(src, dst, conflict)
                if choice == STOP:
                    raise Cancelled
                if choice == SKIP:
                    return None
                if choice == KEEP_BOTH:
                    dst = unique_child(dst.get_parent(), dst.get_basename())
                elif choice == MERGE and conflict == "folders":
                    merge = True
                elif choice == REPLACE and conflict == "files":
                    overwrite = True
                else:
                    return None  # replacing a folder with a file (or the reverse) is never offered

        if move and not merge and not self._rename_fails:
            try:
                flags = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
                if overwrite:
                    flags |= Gio.FileCopyFlags.OVERWRITE
                src.move(dst, flags, self.cancellable, None, None)
                return dst
            except GLib.Error as e:
                if e.domain != GLib.quark_to_string(Gio.io_error_quark()) or e.code not in NO_RENAME:
                    raise
                self._rename_fails = True  # different filesystem: rename can't work for the children either

        if move:
            self._ensure_measured()

        if src_type == Gio.FileType.DIRECTORY:
            return dst if self._copy_folder(src, dst, merge, move) else None
        self._copy_file(src, dst, overwrite)
        if move:
            src.delete(self.cancellable)
        return dst

    def _copy_folder(self, src, dst, merge, move):
        if not merge:
            dst.make_directory(self.cancellable)
        ok = True
        try:
            for info in self._children(src):
                name = info.get_name()
                try:
                    if self._transfer(src.get_child(name), dst.get_child(name), move) is None:
                        ok = False
                except GLib.Error as e:
                    self._fail(src.get_child(name), e)
                    ok = False
        except GLib.Error as e:
            self._fail(src, e)
            ok = False
        try:
            src.copy_attributes(dst, COPY_FLAGS, self.cancellable)
        except GLib.Error:
            pass  # e.g. FAT drives can't hold permissions; the files themselves are intact
        if move and ok:
            src.delete(self.cancellable)  # only once every child made it across
        return ok

    def _copy_file(self, src, dst, overwrite):
        base = self.bytes_done
        size_seen = [0]

        def progress(current, total, *_):
            size_seen[0] = current
            self.bytes_done = base + current
            self._emit()

        if overwrite:
            # Copy beside the target and swap it in, so a failed copy leaves the original untouched.
            tmp = unique_child(dst.get_parent(), f".{dst.get_basename()}.files-partial")
            try:
                src.copy(tmp, COPY_FLAGS, self.cancellable, progress, None)
                tmp.move(dst, Gio.FileCopyFlags.OVERWRITE | Gio.FileCopyFlags.NOFOLLOW_SYMLINKS,
                         self.cancellable, None, None)
            except GLib.Error:
                self._discard(tmp)
                raise
        else:
            try:
                src.copy(dst, COPY_FLAGS, self.cancellable, progress, None)
            except GLib.Error as e:
                # dst did not exist before this copy (checked above), so any leftover is our partial file
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                    self._discard(dst)
                raise
        self.bytes_done = base + size_seen[0]

    @staticmethod
    def _discard(file):
        try:
            file.delete(None)
        except GLib.Error:
            pass

    # ---- trash / delete ----

    def _do_trash(self):
        for i, src in enumerate(self.sources):
            self._check()
            try:
                src.trash(self.cancellable)
                self.trashed.append(src.get_path())
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                    self.untrashable.append(src)
                else:
                    self._fail(src, e)
            self.count = i + 1
            self._emit()

    def _do_delete(self):
        for i, src in enumerate(self.sources):
            self._check()
            try:
                self._delete(src)
            except GLib.Error as e:
                self._fail(src, e)
            self.count = i + 1
            self._emit()

    def _delete(self, file):
        # The trash backend deletes a whole top-level item at once and refuses anything inside it.
        if file.get_uri_scheme() != "trash" and _type(file) == Gio.FileType.DIRECTORY:
            for info in self._children(file):  # NOFOLLOW: a symlink is removed, never its target
                self._check()
                self._delete(file.get_child(info.get_name()))
        file.delete(self.cancellable)

    def _do_empty_trash(self):
        self.sources = []
        trash = Gio.File.new_for_uri("trash:///")
        for info in self._children(trash):
            self.sources.append(trash.get_child(info.get_name()))
        self.total_items = len(self.sources)
        self._do_delete()

    # ---- restore ----

    def _restore_one(self, item, orig_path):
        dst = Gio.File.new_for_path(orig_path)
        parent = dst.get_parent()
        if not parent.query_exists(None):
            parent.make_directory_with_parents(self.cancellable)
        if _is_link(dst):
            dst = unique_child(parent, dst.get_basename())
        item.move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, self.cancellable, None, None)
        self.done_names.append(dst.get_basename())

    def _do_restore(self):
        for i, item in enumerate(self.sources):
            self._check()
            try:
                info = item.query_info("trash::orig-path", NOFOLLOW, self.cancellable)
                orig = info.get_attribute_byte_string("trash::orig-path")
                if not orig:
                    raise GLib.Error("The original location is unknown.")
                self._restore_one(item, orig)
            except GLib.Error as e:
                self._fail(item, e)
            self.count = i + 1
            self._emit()

    def _do_undo_trash(self):
        """Restores files just trashed. `sources` holds their original paths."""
        wanted = set(self.sources)
        trash = Gio.File.new_for_uri("trash:///")
        latest = {}
        for _ in range(25):  # gvfsd-trash notices new items a moment after the trash call returns
            self._check()
            latest = {}
            en = trash.enumerate_children("standard::name,trash::orig-path,trash::deletion-date", NOFOLLOW,
                                          self.cancellable)
            for info in en:
                orig = info.get_attribute_byte_string("trash::orig-path")
                if orig in wanted:
                    date = info.get_attribute_string("trash::deletion-date") or ""
                    if orig not in latest or date > latest[orig][0]:
                        latest[orig] = (date, info.get_name())
            en.close(None)
            if len(latest) == len(wanted):
                break
            time.sleep(0.2)
        for orig in wanted - latest.keys():
            self.errors.append((os.path.basename(orig), "It's no longer in Trash."))
        for orig, (_, name) in latest.items():
            self._check()
            item = trash.get_child(name)
            try:
                self._restore_one(item, orig)
            except GLib.Error as e:
                self._fail(item, e)
            self.count += 1
            self._emit()
