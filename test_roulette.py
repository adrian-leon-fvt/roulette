import copy
import io
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock
import uuid

import roulette


def person(name, enabled=True, cooldown=0):
    return {"name": name, "enabled": enabled, "cooldown": cooldown}


class RouletteTests(unittest.TestCase):
    def test_prefers_yml_and_keeps_legacy_yaml_supported(self):
        for existence, suffix in [([True], ".yml"), ([False, True], ".yaml"),
                                  ([False, False], ".yml")]:
            with self.subTest(suffix=suffix, existence=existence):
                with mock.patch.object(roulette.Path, "exists", side_effect=existence):
                    module = runpy.run_path(roulette.__file__, run_name="prefs_path_check")
                self.assertEqual(module["PREFS_PATH"].suffix, suffix)

    def setUp(self):
        self.prefs = roulette.default_prefs()
        self.prefs["names"] = [person(name) for name in "ABCDE"]
        self.path = Path(__file__).parent / f".roulette-test-{uuid.uuid4().hex}.yaml"
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def test_three_completed_turns_before_winner_is_eligible(self):
        state = roulette.commit_winner(self.prefs, "A")
        for winner in "BCD":
            self.assertNotIn("A", roulette.eligible(state["names"]))
            state = roulette.commit_winner(state, winner)
        self.assertIn("A", roulette.eligible(state["names"]))
        self.assertEqual(self.prefs["names"][0]["cooldown"], 0)

    def test_override_and_manual_disabled(self):
        self.prefs["names"] = [person("A", True, 2), person("B", False, 1),
                               person("C")]
        state = roulette.commit_winner(self.prefs, "C")
        self.assertFalse(state["names"][1]["enabled"])
        self.assertNotIn("B", roulette.eligible(state["names"]))
        roulette.enable_name(state["names"][0])
        self.assertEqual(state["names"][0]["cooldown"], 0)
        self.assertIn("A", roulette.eligible(state["names"]))
        roulette.enable_name(state["names"][1])
        self.assertIn("B", roulette.eligible(state["names"]))

    def test_no_eligible_does_not_advance(self):
        self.prefs["names"] = [person("A", True, 1), person("B", False)]
        before = copy.deepcopy(self.prefs)
        self.assertEqual(roulette.eligible(self.prefs["names"]), [])
        with self.assertRaises(ValueError):
            roulette.commit_winner(self.prefs, "A")
        self.assertEqual(self.prefs, before)

    def test_prefs_roundtrip_and_invalid(self):
        started = copy.deepcopy(self.prefs)
        started["last_run"] = "2026-11-16T17:00:00-07:00"
        roulette.save_prefs(started, self.path)
        self.assertEqual(roulette.load_prefs(self.path), started)
        state = roulette.commit_winner(self.prefs, "A")
        roulette.save_prefs(state, self.path)
        self.assertEqual(roulette.load_prefs(self.path), state)
        bad = copy.deepcopy(state)
        bad["names"].append(person("a"))
        with self.assertRaises(ValueError):
            roulette.save_prefs(bad, self.path)
        self.assertEqual(roulette.load_prefs(self.path), state)
        self.path.write_text("names: [broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Cannot load preferences"):
            roulette.load_prefs(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "names: [broken")
        for key, value in [("duration", True), ("duration", 0), ("duration", -1),
                           ("duration", float("inf")), ("duration", float("nan")),
                           ("duration", 10 ** 400), ("scheme", "bad"),
                           ("last_run", "invalid")]:
            invalid = copy.deepcopy(state)
            invalid[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                roulette.validate_prefs(invalid)
        for field, value in [("enabled", 1), ("cooldown", True), ("cooldown", 4)]:
            invalid = copy.deepcopy(state)
            invalid["names"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                roulette.validate_prefs(invalid)

    def test_load_legacy_speed_preferences(self):
        legacy = copy.deepcopy(self.prefs)
        del legacy["duration"]
        legacy["speed"] = 5
        self.path.write_text(roulette.yaml.safe_dump(legacy), encoding="utf-8")
        self.assertEqual(roulette.load_prefs(self.path), self.prefs)
        legacy["speed"] = False
        self.path.write_text(roulette.yaml.safe_dump(legacy), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Speed"):
            roulette.load_prefs(self.path)

    def test_atomic_save_failure_preserves_existing_file(self):
        roulette.save_prefs(self.prefs, self.path)
        existing = self.path.read_bytes()
        with mock.patch.object(roulette.os, "replace", side_effect=OSError("failed")) as replace:
            with self.assertRaises(OSError):
                roulette.save_prefs(roulette.commit_winner(self.prefs, "A"), self.path)
        self.assertEqual(self.path.read_bytes(), existing)
        self.assertFalse(os.path.exists(replace.call_args.args[0]))

    def test_qrng_rejection_and_request(self):
        responses = [io.BytesIO(json.dumps({"data": value, "bytes": 1, "format": "hex"}).encode())
                     for value in ("ff", "08")]
        with mock.patch.object(roulette.urllib.request, "urlopen", side_effect=responses) as request:
            self.assertEqual(roulette.qrng_choice(10, "secret"), 8)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["timeout"], 10)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://qrngapi.com/api/random?bytes=1&format=hex")
        self.assertEqual(sent.get_header("X-api-key"), "secret")

    def test_fallbacks_are_visible_and_sanitized(self):
        with mock.patch.object(roulette.secrets, "randbelow", return_value=2):
            with mock.patch.object(roulette.urllib.request, "urlopen") as request:
                index, source = roulette.choose_winner(3, "")
                self.assertEqual(index, 2)
                self.assertIn("no QRNG_TOKEN", source)
                request.assert_not_called()
            with mock.patch.object(roulette.urllib.request, "urlopen",
                                   side_effect=OSError("secret")):
                index, source = roulette.choose_winner(3, "secret")
                self.assertEqual(index, 2)
                self.assertIn("System randomness (fallback:", source)
                self.assertNotIn("secret", source)
            for entropy in (1, "0", "gg", "00ff"):
                with self.subTest(entropy=entropy), mock.patch.object(
                    roulette.urllib.request, "urlopen",
                    return_value=io.BytesIO(json.dumps({"entropy": entropy}).encode())
                ):
                    index, source = roulette.choose_winner(3, "secret")
                    self.assertEqual(index, 2)
                    self.assertIn("invalid entropy", source)
            for response in ({"data": "00", "method": "simulator"},
                             {"data": "00", "fallbackUsed": True},
                             {"data": "00", "bytes": True},
                             {"data": "00", "entropy": "01"}):
                with self.subTest(response=response), mock.patch.object(
                    roulette.urllib.request, "urlopen",
                    return_value=io.BytesIO(json.dumps(response).encode())
                ):
                    _, source = roulette.choose_winner(3, "secret")
                    self.assertIn("System randomness (fallback:", source)

    def test_wheel_pointer_center(self):
        for count in (1, 2, 3, 10, 37):
            for index in range(count):
                center = (roulette.target_angle(index, count) +
                          (index + 0.5) * 360 / count) % 360
                self.assertAlmostEqual(center, 90)

    def test_manual_cooldown_and_return_persist_without_a_win(self):
        try:
            root = roulette.tk.Tk()
        except roulette.tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        save = roulette.save_prefs
        with mock.patch.object(roulette, "save_prefs",
                               side_effect=lambda state: save(state, self.path)):
            app = roulette.Roulette(root, self.prefs)
            root.update()
            app.cooldown_button.invoke()
            self.assertIn("Select a name", app.status.get())
            app.names_list.selection_set(0)
            app.set_busy(True)
            app.send_to_cooldown()
            self.assertIn("A", roulette.eligible(app.prefs["names"]))
            self.assertIn("disabled", app.cooldown_button.state())
            app.set_busy(False)
            with mock.patch.object(roulette, "save_prefs", side_effect=OSError("disk full")):
                app.cooldown_button.invoke()
            self.assertIn("Save failed", app.status.get())
            self.assertIn("A", roulette.eligible(app.prefs["names"]))
            app.cooldown_button.invoke()
            self.assertNotIn("A", roulette.eligible(app.prefs["names"]))
            self.assertEqual(app.cool_items[0]["cooldown"], 3)
            self.assertIsNone(app.prefs["last_winner"])
            self.assertEqual(roulette.load_prefs(self.path), app.prefs)
            app.cool_list.selection_set(0)
            app.return_button.invoke()
            self.assertIn("A", roulette.eligible(app.prefs["names"]))
            self.assertEqual(app.prefs["names"][0]["cooldown"], 0)
            self.assertEqual(roulette.load_prefs(self.path), app.prefs)

    def test_committed_winner_appears_in_cooldown_during_animation(self):
        try:
            root = roulette.tk.Tk()
        except roulette.tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        self.prefs["names"] = [person("A"), person("B", False)]
        with mock.patch.object(roulette, "save_prefs"):
            app = roulette.Roulette(root, self.prefs)
            root.update()
            app.set_busy(True)
            app.queue.put((0, "System randomness (fallback: no QRNG_TOKEN)"))
            with mock.patch.object(root, "after"):
                app.poll(["A"])
            self.assertTrue(any(app.canvas.type(item) == "oval"
                                for item in app.canvas.find_all()))
            self.assertEqual(app.cool_list.size(), 1)
            self.assertEqual(app.names_list.size(), 1)
            self.assertEqual(app.wheel_names, ["A"])
            self.assertIn("fallback", app.status.get())
            self.assertEqual(app.cool_list.cget("state"), "disabled")
            app.set_busy(False)
            app.cool_list.selection_set(0)
            app.return_to_wheel()
            self.assertIn("A", roulette.eligible(app.prefs["names"]))
            self.assertNotIn("B", roulette.eligible(app.prefs["names"]))

    def test_test_run_only_spins(self):
        try:
            root = roulette.tk.Tk()
        except roulette.tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        self.prefs["names"][1]["cooldown"] = 2
        self.prefs.update(last_winner="B", last_run="2026-11-24T11:00:00-07:00",
                          last_source="Earlier spin")
        before = copy.deepcopy(self.prefs)
        with mock.patch.object(roulette, "save_prefs") as save, \
             mock.patch.object(roulette, "read_token") as token, \
             mock.patch.object(roulette.secrets, "randbelow", return_value=0), \
             mock.patch.object(roulette.time, "monotonic", side_effect=[0, 0, 100000]):
            app = roulette.Roulette(root, self.prefs)
            root.update()
            with mock.patch.object(root, "after") as after, \
                 mock.patch.object(app, "celebrate") as celebrate:
                app.test_button.invoke()
                self.assertTrue(app.busy)
                self.assertIn("disabled", app.test_button.state())
                self.assertIn("disabled", app.spin_button.state())
                after.call_args.args[1]()
                celebrate.assert_not_called()
            self.assertEqual(app.prefs, before)
            self.assertEqual(app.result.get(), "Last winner: B\nSource: Earlier spin")
            self.assertEqual(app.rotation, roulette.target_angle(0, 4))
            self.assertIn("Test run", app.status.get())
            self.assertFalse(app.busy)
            save.assert_not_called()
            token.assert_not_called()

    def test_duration_validation_and_animation(self):
        try:
            root = roulette.tk.Tk()
        except roulette.tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        save = roulette.save_prefs
        with mock.patch.object(roulette, "save_prefs",
                               side_effect=lambda state: save(state, self.path)):
            app = roulette.Roulette(root, self.prefs)
            root.update()
            self.assertEqual(app.duration.get(), "3")
            for value in ("", "0", "-1", "nan", "inf", "abc"):
                app.duration.set(value)
                app.test_button.invoke()
                self.assertIn("positive finite", app.status.get())
                self.assertFalse(app.busy)
            self.assertFalse(self.path.exists())
            app.duration.set("1.5")
            with mock.patch.object(roulette, "save_prefs", side_effect=OSError("disk full")):
                app.test_button.invoke()
                self.assertIn("Save failed", app.status.get())
                self.assertFalse(app.busy)
                self.assertEqual(app.duration.get(), "1.5")
            with mock.patch.object(roulette.secrets, "randbelow", return_value=0), \
                 mock.patch.object(roulette.time, "monotonic",
                                   side_effect=[0, 0, 0.75, 1.5]), \
                 mock.patch.object(root, "after") as after:
                app.test_button.invoke()
                self.assertTrue(app.busy)
                self.assertEqual(roulette.load_prefs(self.path)["duration"], 1.5)
                after.call_args.args[1]()
                self.assertTrue(app.busy)
                after.call_args.args[1]()
                self.assertFalse(app.busy)
            self.assertEqual(app.rotation, roulette.target_angle(0, 5))

    def test_winner_celebration_and_opt_out(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                try:
                    root = roulette.tk.Tk()
                except roulette.tk.TclError:
                    self.skipTest("Tk display unavailable")
                try:
                    with mock.patch.object(roulette, "animations_enabled", return_value=True), \
                         mock.patch.object(roulette, "save_prefs"), \
                         mock.patch.object(roulette.time, "monotonic",
                                           side_effect=[0, 3, 3, 3.8, 5]):
                        app = roulette.Roulette(root, copy.deepcopy(self.prefs))
                        root.update()
                        if not enabled:
                            app.celebration_check.invoke()
                        self.assertEqual(app.celebration_enabled.get(), enabled)
                        app.set_busy(True)
                        app.queue.put((0, "Test source"))
                        with mock.patch.object(root, "after") as after:
                            app.poll(roulette.eligible(app.prefs["names"]))
                            self.assertIn("Last winner: A", app.result.get())
                            if enabled:
                                self.assertTrue(app.busy)
                                items = app.canvas.find_withtag("celebration")
                                self.assertEqual(len(items), 34)
                                self.assertTrue(any(app.canvas.type(item) == "text"
                                                    and app.canvas.itemcget(item, "text") == "A"
                                                    for item in items))
                                after.call_args.args[1]()
                            self.assertFalse(app.busy)
                            self.assertFalse(app.canvas.find_withtag("celebration"))
                finally:
                    root.destroy()

    def test_windows_animation_preference(self):
        with mock.patch.object(roulette.sys, "platform", "win32"), \
             mock.patch.object(roulette.ctypes, "windll", create=True) as windll:
            def system_info(action, unused, value, flags):
                self.assertEqual(action, 0x1042)
                roulette.ctypes.cast(value, roulette.ctypes.POINTER(roulette.ctypes.c_int))[0] = 1
                return 1
            windll.user32.SystemParametersInfoW.side_effect = system_info
            self.assertTrue(roulette.animations_enabled())
            windll.user32.SystemParametersInfoW.side_effect = None
            windll.user32.SystemParametersInfoW.return_value = 1
            self.assertFalse(roulette.animations_enabled())
            windll.user32.SystemParametersInfoW.return_value = 0
            self.assertFalse(roulette.animations_enabled())


if __name__ == "__main__":
    unittest.main()
