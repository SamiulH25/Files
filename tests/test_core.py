"""Logic tests that need no display. Run: python3 -m unittest discover -s tests"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gi.repository import Gio

import cairo

from filesapp import icons, jump, kinds, ops, state, thumbs


class IconsTest(unittest.TestCase):
    def ink_centre(self, painter, value):
        scale = 8
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, icons.SIZE * scale, icons.SIZE * scale)
        cr = cairo.Context(surface)
        cr.scale(scale, scale)
        icons.paint(cr, painter, value)
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        inked = [(x, y) for y in range(surface.get_height()) for x in range(surface.get_width())
                 if data[y * stride + x * 4 + 3]]
        xs, ys = [p[0] for p in inked], [p[1] for p in inked]
        return (min(xs) + max(xs) + 1) / 2 / scale, (min(ys) + max(ys) + 1) / 2 / scale

    def test_every_icon_is_centred(self):
        for painter in (icons.back, icons.forward, icons.up, icons.list_view, icons.grid_view, icons.search,
                        icons.cancel):
            x, y = self.ink_centre(painter, 1.0)
            self.assertAlmostEqual(x, icons.SIZE / 2, delta=0.5, msg=painter.__name__)
            self.assertAlmostEqual(y, icons.SIZE / 2, delta=0.5, msg=painter.__name__)

    def test_eye_stays_centred_while_it_shuts(self):
        for opened in (0.0, 0.25, 0.5, 0.75, 1.0):
            x, y = self.ink_centre(icons.eye, opened)
            self.assertAlmostEqual(x, icons.SIZE / 2, delta=0.5, msg=opened)
            self.assertAlmostEqual(y, icons.SIZE / 2, delta=1.0, msg=opened)


class JumpTest(unittest.TestCase):
    ENTRIES = [
        (156.0, "/home/u/Projects/tools"),
        (54.0, "/home/u/Projects"),
        (38.0, "/home/u/Projects/games"),
        (8.0, "/home/u/Projects/tools/Folderol"),
        (4.0, "/home/u/Projects/tools/Files"),
        (1.0, "/home/u/old/tools/thing"),
    ]

    def top(self, query):
        return jump.search(query, self.ENTRIES)[0].path

    def test_parse_keeps_spaces_in_paths(self):
        self.assertEqual(jump.parse(" 156.0 /a/b c\n   4.5 /d\n"), [(156.0, "/a/b c"), (4.5, "/d")])

    def test_frecency_wins_like_z(self):
        self.assertEqual(self.top("o"), "/home/u/Projects/tools")
        self.assertEqual(self.top("files"), "/home/u/Projects/tools/Files")

    def test_last_keyword_must_be_in_final_component(self):
        self.assertFalse(jump.zoxide_match(["tools"], "/home/u/tools/x"))
        self.assertTrue(jump.zoxide_match(["tools", "x"], "/home/u/tools/x"))
        self.assertFalse(jump.zoxide_match(["x", "tools"], "/home/u/tools/x"))

    def test_fuzzy_only_after_exact(self):
        hits = [h.path for h in jump.search("fls", self.ENTRIES)]
        self.assertEqual(hits, ["/home/u/Projects/tools/Files"])
        self.assertEqual(jump.search("zzzz", self.ENTRIES), [])

    def test_here_ranks_between_exact_and_fuzzy(self):
        hits = jump.search("fil", self.ENTRIES, here=["profile.txt", "file.txt", "nope"])
        self.assertEqual([h.kind for h in hits], ["jump", "here", "here"])
        self.assertEqual(hits[1].path, "file.txt")

    def test_empty_query_lists_top_dirs(self):
        self.assertEqual(jump.search("", self.ENTRIES, limit=2)[0].path, "/home/u/Projects/tools")

    def test_positions(self):
        self.assertEqual(jump.positions("ol", "tools"), [2, 3])
        self.assertEqual(jump.positions("ts", "tools"), [0, 4])
        self.assertIsNone(jump.positions("zz", "tools"))

    def test_location_detection(self):
        for q in ("/usr", "~/x", "./a", "../b", "sftp://host/x", "mtp://phone"):
            self.assertTrue(jump.is_location(q), q)
        for q in ("tools", "a/b"):
            self.assertFalse(jump.is_location(q), q)

    def test_complete(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("alpha", "Almond", ".allhid", "beta"):
                os.mkdir(os.path.join(d, name))
            open(os.path.join(d, "alfile"), "w").close()
            names = [os.path.basename(p) for p in jump.complete(d + "/al", d, show_hidden=False)]
            self.assertEqual(names, ["Almond", "alpha"])
            names = [os.path.basename(p) for p in jump.complete(d + "/", d, show_hidden=True)]
            self.assertEqual(names[-1], ".allhid")
            names = [os.path.basename(p) for p in jump.complete("./be", d)]
            self.assertEqual(names, ["beta"])

    @unittest.skipUnless(shutil.which("zoxide"), "zoxide not installed")
    def test_top_hit_equals_real_zoxide(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, _ZO_DATA_DIR=d, _ZO_EXCLUDE_DIRS="")
            paths = [os.path.join(d, n) for n in ("tools", "tools/Files", "games", "toolshed", "Go tools")]
            for i, p in enumerate(paths):
                os.makedirs(p, exist_ok=True)
                for _ in range((len(paths) - i) * 2):
                    subprocess.run(["zoxide", "add", "--", p], env=env, check=True)

            def z(*args):
                return subprocess.run(["zoxide", "query", *args], env=env, capture_output=True, text=True).stdout

            entries = jump.parse(z("--list", "--score"))
            self.assertEqual(len(entries), 5)
            for query in ("to", "tools", "FILES", "go tools", "sh", "am", "tools files"):
                expected = z(*query.split()).strip()
                self.assertEqual(jump.search(query, entries)[0].path, expected, query)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.dir.name

    def tearDown(self):
        del os.environ["XDG_STATE_HOME"]
        self.dir.cleanup()

    def test_defaults_show_hidden(self):
        self.assertTrue(state.load()["show_hidden"])

    def test_roundtrip(self):
        s = state.load()
        s["last_dir"] = "/tmp"
        s["show_hidden"] = False
        state.save(s)
        self.assertEqual(state.load()["last_dir"], "/tmp")
        self.assertFalse(state.load()["show_hidden"])

    def test_corrupt_file_falls_back(self):
        path = os.path.join(self.dir.name, "files", "state.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{nope")
        self.assertEqual(state.load()["view"], "list")

    def test_nearest_existing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(state.nearest_existing(d + "/gone/deeper"), d)
        self.assertEqual(state.nearest_existing(None), os.path.expanduser("~"))


class KindsTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(kinds.kind_of("x", None, True)[1], "k-folder")
        self.assertEqual(kinds.kind_of("a.png", "image/png", False)[1], "k-image")
        self.assertEqual(kinds.kind_of("a.py", "text/x-python", False)[1], "k-code")
        self.assertEqual(kinds.kind_of("a.tar.gz", "application/gzip", False)[1], "k-archive")
        self.assertEqual(kinds.kind_of("noext", None, False)[1], "k-file")

    def test_size_text(self):
        self.assertEqual(kinds.size_text(0), "0 B")
        self.assertEqual(kinds.size_text(999), "999 B")
        self.assertEqual(kinds.size_text(1500), "1.5 KB")
        self.assertEqual(kinds.size_text(2_500_000_000), "2.5 GB")


class OpsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.a = self.mk("a")
        self.b = self.mk("b")

    def tearDown(self):
        self.tmp.cleanup()

    def mk(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        return path

    def write(self, path, text="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def read(self, path):
        with open(path) as f:
            return f.read()

    def job(self, kind, paths, dest=None, answer=None, sticky=False):
        def on_conflict(job, src, dst, conflict):
            self.conflicts.append(conflict)
            job.answer(answer or ops.STOP, sticky, conflict)
        self.conflicts = []
        job = ops.Job(kind, [Gio.File.new_for_path(p) for p in paths],
                      Gio.File.new_for_path(dest) if dest else None,
                      dispatch=lambda fn, *args: fn(*args), on_conflict=on_conflict)
        job.run_sync()
        return job


class TransferTest(OpsBase):
    def test_names(self):
        self.assertEqual(ops.numbered_name("a.txt", 2), "a (2).txt")
        self.assertEqual(ops.numbered_name("a.tar.gz", 3), "a (3).tar.gz")
        self.assertEqual(ops.numbered_name(".bashrc", 2), ".bashrc (2)")
        self.assertEqual(ops.numbered_name("folder", 2), "folder (2)")

    def test_copy_tree_with_symlink_and_hidden(self):
        self.write(f"{self.a}/d/f.txt", "hi")
        self.write(f"{self.a}/d/.hidden", "h")
        os.symlink("/etc/hostname", f"{self.a}/d/link")
        job = self.job("copy", [f"{self.a}/d"], self.b)
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(f"{self.b}/d/f.txt"), "hi")
        self.assertEqual(self.read(f"{self.b}/d/.hidden"), "h")
        self.assertEqual(os.readlink(f"{self.b}/d/link"), "/etc/hostname")
        self.assertTrue(os.path.exists(f"{self.a}/d/f.txt"))
        self.assertEqual(job.done_names, ["d"])

    def test_copy_preserves_mtime_and_mode(self):
        p = self.write(f"{self.a}/f")
        os.chmod(p, 0o750)
        os.utime(p, (1000000, 1000000))
        self.job("copy", [p], self.b)
        st = os.stat(f"{self.b}/f")
        self.assertEqual(int(st.st_mtime), 1000000)
        self.assertEqual(st.st_mode & 0o777, 0o750)

    def test_move_renames(self):
        p = self.write(f"{self.a}/f", "v")
        job = self.job("move", [p], self.b)
        self.assertEqual(job.errors, [])
        self.assertFalse(os.path.exists(p))
        self.assertEqual(self.read(f"{self.b}/f"), "v")

    def test_paste_into_same_folder_copies_beside(self):
        p = self.write(f"{self.a}/f.txt", "v")
        self.job("copy", [p], self.a)
        self.assertEqual(self.conflicts, [])
        self.assertEqual(self.read(p), "v")
        self.assertEqual(self.read(f"{self.a}/f (2).txt"), "v")

    def test_move_onto_itself_is_noop(self):
        p = self.write(f"{self.a}/f.txt", "v")
        job = self.job("move", [p], self.a)
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(p), "v")
        self.assertEqual(self.conflicts, [])

    def test_same_file_through_symlinked_folder_keeps_data(self):
        p = self.write(f"{self.a}/f.txt", "precious")
        os.symlink(self.a, f"{self.root}/alias")
        self.job("copy", [p], f"{self.root}/alias", answer=ops.REPLACE)
        self.assertEqual(self.conflicts, [])
        self.assertEqual(self.read(p), "precious")
        self.job("move", [p], f"{self.root}/alias", answer=ops.REPLACE)
        self.assertEqual(self.read(p), "precious")

    def test_folder_into_itself_refused(self):
        self.write(f"{self.a}/d/f", "v")
        for kind in ("copy", "move"):
            job = self.job(kind, [f"{self.a}/d"], f"{self.a}/d")
            self.assertEqual(len(job.errors), 1)
            self.assertEqual(self.read(f"{self.a}/d/f"), "v")
        self.mk("a/d/sub")
        job = self.job("copy", [f"{self.a}/d"], f"{self.a}/d/sub")
        self.assertEqual(len(job.errors), 1)
        self.assertFalse(os.path.exists(f"{self.a}/d/sub/d"))

    def test_conflict_never_overwrites_without_answer(self):
        src = self.write(f"{self.a}/f", "new")
        dst = self.write(f"{self.b}/f", "old")
        job = self.job("copy", [src], self.b, answer=ops.STOP)
        self.assertEqual(self.conflicts, ["files"])
        self.assertTrue(job.cancelled)
        self.assertEqual(self.read(dst), "old")

    def test_conflict_skip_keep_replace(self):
        src = self.write(f"{self.a}/f", "new")
        dst = self.write(f"{self.b}/f", "old")
        self.job("move", [src], self.b, answer=ops.SKIP)
        self.assertEqual(self.read(dst), "old")
        self.assertTrue(os.path.exists(src))
        self.job("copy", [src], self.b, answer=ops.KEEP_BOTH)
        self.assertEqual(self.read(dst), "old")
        self.assertEqual(self.read(f"{self.b}/f (2)"), "new")
        self.job("copy", [src], self.b, answer=ops.REPLACE)
        self.assertEqual(self.read(dst), "new")
        self.assertEqual(sorted(os.listdir(self.b)), ["f", "f (2)"])

    def test_merge_folders_asks_for_children(self):
        self.write(f"{self.a}/d/one", "1")
        self.write(f"{self.a}/d/clash", "new")
        self.write(f"{self.b}/d/two", "2")
        self.write(f"{self.b}/d/clash", "old")

        def on_conflict(job, src, dst, conflict):
            job.answer(ops.MERGE if conflict == "folders" else ops.SKIP)

        job = ops.Job("move", [Gio.File.new_for_path(f"{self.a}/d")], Gio.File.new_for_path(self.b),
                      dispatch=lambda fn, *args: fn(*args), on_conflict=on_conflict)
        job.run_sync()
        self.assertEqual(self.read(f"{self.b}/d/one"), "1")
        self.assertEqual(self.read(f"{self.b}/d/two"), "2")
        self.assertEqual(self.read(f"{self.b}/d/clash"), "old")
        # A skipped child means the source folder must survive with that child in it.
        self.assertEqual(self.read(f"{self.a}/d/clash"), "new")
        self.assertFalse(os.path.exists(f"{self.a}/d/one"))

    def test_mixed_conflict_cannot_replace(self):
        src = self.write(f"{self.a}/x", "file")
        self.write(f"{self.b}/x/keep", "safe")
        self.job("copy", [src], self.b, answer=ops.REPLACE)
        self.assertEqual(self.conflicts, ["mixed"])
        self.assertEqual(self.read(f"{self.b}/x/keep"), "safe")

    def test_apply_to_all_asks_once(self):
        for n in "123":
            self.write(f"{self.a}/{n}", "new")
            self.write(f"{self.b}/{n}", "old")
        self.job("copy", [f"{self.a}/{n}" for n in "123"], self.b, answer=ops.SKIP, sticky=True)
        self.assertEqual(self.conflicts, ["files"])

    def test_dangling_symlink_is_a_conflict_not_followed(self):
        src = self.write(f"{self.a}/f", "new")
        os.symlink("/nonexistent/target", f"{self.b}/f")
        self.job("copy", [src], self.b, answer=ops.SKIP)
        self.assertEqual(self.conflicts, ["files"])
        self.assertTrue(os.path.islink(f"{self.b}/f"))

    def test_cancelled_copy_leaves_no_partial_file(self):
        src = f"{self.a}/big"
        with open(src, "wb") as f:
            f.truncate(200_000_000)
        job = ops.Job("copy", [Gio.File.new_for_path(src)], Gio.File.new_for_path(self.b),
                      dispatch=lambda fn, *args: fn(*args))
        job.on_update = lambda j: j.cancel()
        job.run_sync()
        self.assertTrue(job.cancelled)
        self.assertEqual(os.listdir(self.b), [])
        self.assertTrue(os.path.exists(src))

    def test_failed_replace_keeps_original(self):
        src = self.write(f"{self.a}/f", "new")
        dst = self.write(f"{self.b}/f", "old")
        os.chmod(src, 0)
        try:
            job = self.job("copy", [src], self.b, answer=ops.REPLACE)
        finally:
            os.chmod(src, 0o644)
        if os.geteuid() != 0:
            self.assertEqual(len(job.errors), 1)
        self.assertEqual(self.read(dst), "old")
        self.assertEqual(os.listdir(self.b), ["f"])

    def test_cross_filesystem_move_falls_back_and_only_then_removes(self):
        self.write(f"{self.a}/d/one", "1")
        self.write(f"{self.a}/d/sub/two", "2")
        job = ops.Job("move", [Gio.File.new_for_path(f"{self.a}/d")], Gio.File.new_for_path(self.b),
                      dispatch=lambda fn, *args: fn(*args))
        job._rename_fails = True  # what a real EXDEV sets
        job.run_sync()
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(f"{self.b}/d/sub/two"), "2")
        self.assertFalse(os.path.exists(f"{self.a}/d"))

    def test_clipboard_parsing(self):
        self.assertEqual(ops.parse_clipboard("x-special/gnome-copied-files", "cut\nfile:///a\nfile:///b"),
                         (True, ["file:///a", "file:///b"]))
        self.assertEqual(ops.parse_clipboard("x-special/gnome-copied-files", "copy\nfile:///a\n"),
                         (False, ["file:///a"]))
        self.assertEqual(ops.parse_clipboard("text/uri-list", "# c\r\nfile:///a\r\n"), (False, ["file:///a"]))


class DeleteTest(OpsBase):
    def test_delete_never_follows_symlinks(self):
        keep = self.write(f"{self.b}/keep", "safe")
        self.write(f"{self.a}/d/f")
        os.symlink(self.b, f"{self.a}/d/link")
        job = self.job("delete", [f"{self.a}/d"])
        self.assertEqual(job.errors, [])
        self.assertFalse(os.path.exists(f"{self.a}/d"))
        self.assertEqual(self.read(keep), "safe")


@unittest.skipUnless(os.path.isdir("/dev/shm"), "needs a second filesystem")
class CrossFilesystemTest(OpsBase):
    def setUp(self):
        super().setUp()
        self.home_tmp = tempfile.mkdtemp(prefix="files-test-", dir=os.path.expanduser("~/.cache"))
        self.shm = tempfile.mkdtemp(prefix="files-test-", dir="/dev/shm")
        self.addCleanup(shutil.rmtree, self.home_tmp, True)
        self.addCleanup(shutil.rmtree, self.shm, True)
        if os.stat(self.home_tmp).st_dev == os.stat(self.shm).st_dev:
            self.skipTest("same filesystem")

    def test_real_move_across_filesystems(self):
        self.write(f"{self.home_tmp}/d/sub/f", "data")
        os.symlink("/etc/hostname", f"{self.home_tmp}/d/link")
        job = self.job("move", [f"{self.home_tmp}/d"], self.shm)
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(f"{self.shm}/d/sub/f"), "data")
        self.assertEqual(os.readlink(f"{self.shm}/d/link"), "/etc/hostname")
        self.assertFalse(os.path.exists(f"{self.home_tmp}/d"))

    def test_skipped_child_keeps_source_on_real_move(self):
        self.write(f"{self.home_tmp}/d/clash", "new")
        self.write(f"{self.home_tmp}/d/ok", "ok")
        self.write(f"{self.shm}/d/clash", "old")

        def on_conflict(job, src, dst, conflict):
            job.answer(ops.MERGE if conflict == "folders" else ops.SKIP)

        job = ops.Job("move", [Gio.File.new_for_path(f"{self.home_tmp}/d")], Gio.File.new_for_path(self.shm),
                      dispatch=lambda fn, *args: fn(*args), on_conflict=on_conflict)
        job.run_sync()
        self.assertEqual(self.read(f"{self.home_tmp}/d/clash"), "new")
        self.assertEqual(self.read(f"{self.shm}/d/clash"), "old")
        self.assertEqual(self.read(f"{self.shm}/d/ok"), "ok")
        self.assertFalse(os.path.exists(f"{self.home_tmp}/d/ok"))


class TrashTest(OpsBase):
    """Uses the real desktop trash, in a uniquely named folder under the home directory."""

    def setUp(self):
        super().setUp()
        self.home_tmp = tempfile.mkdtemp(prefix="files-test-", dir=os.path.expanduser("~/.cache"))
        self.addCleanup(shutil.rmtree, self.home_tmp, True)

    def test_trash_then_undo_restores(self):
        p = self.write(f"{self.home_tmp}/doomed.txt", "back")
        job = self.job("trash", [p])
        self.assertEqual(job.errors, [])
        self.assertEqual(job.trashed, [p])
        self.assertFalse(os.path.exists(p))
        # Straight after trashing, which is the worst case for gvfsd-trash having caught up.
        undo = ops.Job("undo-trash", job.trashed, dispatch=lambda fn, *args: fn(*args))
        undo.run_sync()
        self.assertEqual(undo.errors, [])
        self.assertEqual(self.read(p), "back")

    def list_trash_item(self, path):
        trash = Gio.File.new_for_uri("trash:///")
        en = trash.enumerate_children("standard::name,trash::orig-path", ops.NOFOLLOW, None)
        found = None
        for info in en:
            if info.get_attribute_byte_string("trash::orig-path") == path:
                found = trash.get_child(info.get_name())
        en.close(None)
        return found

    def trash_item_for(self, path):
        # gvfsd-trash notices new items a moment after the trash call returns.
        for _ in range(50):
            found = self.list_trash_item(path)
            if found is not None:
                return found
            time.sleep(0.1)
        self.fail(f"{path} never appeared in Trash")

    def test_restore_from_trash_view_including_missing_parent(self):
        p = self.write(f"{self.home_tmp}/deep/er/file.txt", "back")
        self.job("trash", [p])
        shutil.rmtree(f"{self.home_tmp}/deep")
        item = self.trash_item_for(p)
        self.assertIsNotNone(item)
        job = ops.Job("restore", [item], dispatch=lambda fn, *args: fn(*args))
        job.run_sync()
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(p), "back")

    def test_restore_never_overwrites(self):
        p = self.write(f"{self.home_tmp}/f.txt", "trashed")
        self.job("trash", [p])
        self.write(p, "newer")
        job = ops.Job("restore", [self.trash_item_for(p)], dispatch=lambda fn, *args: fn(*args))
        job.run_sync()
        self.assertEqual(job.errors, [])
        self.assertEqual(self.read(p), "newer")
        self.assertEqual(self.read(f"{self.home_tmp}/f (2).txt"), "trashed")

    def test_permanent_delete_of_one_trashed_folder(self):
        self.write(f"{self.home_tmp}/gone/a", "1")
        p = f"{self.home_tmp}/gone"
        self.job("trash", [p])
        item = self.trash_item_for(p)
        job = ops.Job("delete", [item], dispatch=lambda fn, *args: fn(*args))
        job.run_sync()
        self.assertEqual(job.errors, [])
        for _ in range(50):
            if self.list_trash_item(p) is None:
                break
            time.sleep(0.1)
        self.assertIsNone(self.list_trash_item(p))


class ThumbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original = thumbs.CACHE
        thumbs.CACHE = os.path.join(self.tmp.name, "cache")
        self.addCleanup(setattr, thumbs, "CACHE", original)

    def image(self, name="a.png", color=0xff0000ff):
        from gi.repository import GdkPixbuf
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 800, 400)
        pixbuf.fill(color)
        path = os.path.join(self.tmp.name, name)
        pixbuf.savev(path, "png", [], [])
        return path

    def test_creates_scaled_cached_thumbnail(self):
        path = self.image()
        uri = Gio.File.new_for_path(path).get_uri()
        texture = thumbs.load_or_create(path, uri, 100, "image/png")
        self.assertEqual((texture.get_width(), texture.get_height()), (256, 128))
        self.assertTrue(os.path.exists(thumbs.cache_path(uri)))

    def test_stale_cache_is_regenerated(self):
        path = self.image()
        uri = Gio.File.new_for_path(path).get_uri()
        thumbs.load_or_create(path, uri, 100, "image/png")
        before = os.stat(thumbs.cache_path(uri)).st_mtime_ns
        thumbs.load_or_create(path, uri, 100, "image/png")
        self.assertEqual(os.stat(thumbs.cache_path(uri)).st_mtime_ns, before, "fresh cache reused")
        time.sleep(0.01)
        thumbs.load_or_create(path, uri, 200, "image/png")
        self.assertNotEqual(os.stat(thumbs.cache_path(uri)).st_mtime_ns, before, "changed file re-rendered")

    def test_broken_image_raises_instead_of_crashing(self):
        path = os.path.join(self.tmp.name, "bad.png")
        with open(path, "w") as f:
            f.write("not an image")
        with self.assertRaises(Exception):
            thumbs.load_or_create(path, Gio.File.new_for_path(path).get_uri(), 1, "image/png")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffmpegthumbnailer"), "needs ffmpeg")
    def test_video(self):
        path = os.path.join(self.tmp.name, "v.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=320x180", path], check=True)
        texture = thumbs.load_or_create(path, Gio.File.new_for_path(path).get_uri(), 1, "video/mp4")
        self.assertEqual(texture.get_width(), 256)

    @unittest.skipUnless(shutil.which("pdftoppm"), "needs poppler")
    def test_pdf(self):
        path = os.path.join(self.tmp.name, "p.pdf")
        objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
                   b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>"]
        data, offsets = b"%PDF-1.4\n", []
        for i, body in enumerate(objects, 1):
            offsets.append(len(data))
            data += b"%d 0 obj\n%s\nendobj\n" % (i, body)
        xref = len(data)
        data += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
        data += b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % xref
        with open(path, "wb") as f:
            f.write(data)
        texture = thumbs.load_or_create(path, Gio.File.new_for_path(path).get_uri(), 1, "application/pdf")
        self.assertEqual(texture.get_width(), 256)


if __name__ == "__main__":
    unittest.main()
