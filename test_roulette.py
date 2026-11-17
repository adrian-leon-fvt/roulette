import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import uuid

import roulette


def person(name, enabled=True, cooldown=0):
    return {"name": name, "enabled": enabled, "cooldown": cooldown}


class RouletteTests(unittest.TestCase):
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
        for key, value in [("speed", True), ("speed", 11), ("scheme", "bad"),
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


if __name__ == "__main__":
    unittest.main()
