"""Local name roulette. Run with Python, Tkinter, and PyYAML."""

import copy
import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import font, messagebox, ttk
import urllib.error
import urllib.request

import yaml


PREFS_PATH = Path(__file__).resolve().with_name("prefs.yml")
if not PREFS_PATH.exists() and PREFS_PATH.with_suffix(".yaml").exists():
    PREFS_PATH = PREFS_PATH.with_suffix(".yaml")
SCHEMES = {
    "Bright": ("#f8cbad", "#ffe699", "#a9d18e", "#9dc3e6", "#d9b3e6", "#f4b6c2"),
    "Ocean": ("#a9d6e5", "#89c2d9", "#b8e0d2", "#c5d5ea", "#d7e3fc"),
    "Sunset": ("#ffcf99", "#f7b2ad", "#e7c6de", "#f6e1a3", "#edc4b3"),
}


def default_prefs():
    return {"names": [], "duration": 3, "scheme": "Bright",
            "last_run": None, "last_winner": None, "last_source": None}


def valid_duration(value):
    return type(value) in (int, float) and 0 < value <= sys.float_info.max


def validate_prefs(prefs):
    if not isinstance(prefs, dict) or set(prefs) != set(default_prefs()):
        raise ValueError("Expected names, duration, scheme, last_run, last_winner and last_source.")
    if not isinstance(prefs["names"], list):
        raise ValueError("Names must be a list.")
    seen = set()
    for item in prefs["names"]:
        if not isinstance(item, dict) or set(item) != {"name", "enabled", "cooldown"}:
            raise ValueError("Each name must have name, enabled and cooldown.")
        name, enabled, cooldown = item["name"], item["enabled"], item["cooldown"]
        if (not isinstance(name, str) or not name or name != name.strip()
                or any(c in name for c in "\r\n") or name.casefold() in seen):
            raise ValueError("Names must be nonempty, single-line and unique ignoring case.")
        if type(enabled) is not bool or type(cooldown) is not int or not 0 <= cooldown <= 3:
            raise ValueError("Enabled must be boolean and cooldown must be 0–3.")
        seen.add(name.casefold())
    if not valid_duration(prefs["duration"]):
        raise ValueError("Duration must be a positive finite number of seconds.")
    if not isinstance(prefs["scheme"], str) or prefs["scheme"] not in SCHEMES:
        raise ValueError("Unknown color scheme.")
    if prefs["last_winner"] is not None and (
        not isinstance(prefs["last_winner"], str) or not prefs["last_winner"].strip()
        or prefs["last_winner"] != prefs["last_winner"].strip()
        or any(c in prefs["last_winner"] for c in "\r\n")
    ):
        raise ValueError("Last winner must be a name or null.")
    if prefs["last_run"] is not None:
        if not isinstance(prefs["last_run"], str):
            raise ValueError("Last run must be an ISO timestamp or null.")
        try:
            datetime.datetime.fromisoformat(prefs["last_run"])
        except ValueError as exc:
            raise ValueError("Last run must be an ISO timestamp or null.") from exc
    if prefs["last_winner"] is not None and prefs["last_run"] is None:
        raise ValueError("A last winner requires a last run timestamp.")
    if prefs["last_source"] is not None and (
        not isinstance(prefs["last_source"], str) or not prefs["last_source"].strip()
        or "\n" in prefs["last_source"] or "\r" in prefs["last_source"]
    ):
        raise ValueError("Last source must be a nonempty string or null.")
    if (prefs["last_source"] is None) != (prefs["last_winner"] is None):
        raise ValueError("Last source and last winner must both be set or both be null.")
    return prefs


def load_prefs(path=PREFS_PATH):
    path = Path(path)
    if not path.exists():
        return default_prefs()
    try:
        with path.open(encoding="utf-8") as file:
            prefs = yaml.safe_load(file)
        if isinstance(prefs, dict) and "speed" in prefs and "duration" not in prefs:
            if type(prefs["speed"]) is not int or not 1 <= prefs["speed"] <= 10:
                raise ValueError("Speed must be an integer from 1 to 10.")
            del prefs["speed"]
            prefs["duration"] = 3
        return validate_prefs(prefs)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"Cannot load preferences: {exc}") from exc


def save_prefs(prefs, path=PREFS_PATH):
    validate_prefs(prefs)
    path = Path(path)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".roulette-", suffix=".tmp",
                                         delete=False) as file:
            temp = file.name
            yaml.safe_dump(prefs, file, allow_unicode=True, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and os.path.exists(temp):
            os.unlink(temp)


def eligible(names):
    return [item["name"] for item in names if item["enabled"] and item["cooldown"] == 0]


def commit_winner(prefs, winner, timestamp=None, source="Quantum randomness (QRNG)"):
    if winner not in eligible(prefs["names"]):
        raise ValueError("Winner is not eligible.")
    result = copy.deepcopy(prefs)
    for item in result["names"]:
        item["cooldown"] = max(0, item["cooldown"] - 1)
        if item["name"] == winner:
            item["cooldown"] = 3
    result["last_winner"] = winner
    result["last_run"] = timestamp or datetime.datetime.now().astimezone().isoformat()
    result["last_source"] = source
    return result


def enable_name(item):
    item["enabled"] = True
    item["cooldown"] = 0


def read_token():
    token = os.environ.get("QRNG_TOKEN", "").strip()
    if token:
        return token
    path = Path.home() / ".env"
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                match = re.match(r"^\s*(?:export\s+)?QRNG_TOKEN\s*=\s*(.*?)\s*$", line)
                if match:
                    value = match.group(1).strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                        value = value[1:-1]
                    return value
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot read QRNG_TOKEN from home .env.") from exc
    return ""


def qrng_choice(count, token):
    if type(count) is not int or count < 1:
        raise ValueError("No eligible names.")
    if not token:
        raise ValueError("Set QRNG_TOKEN in the environment or home .env.")
    size = max(1, ((count - 1).bit_length() + 7) // 8)
    if size > 64:
        raise ValueError("Too many eligible names.")
    limit = (1 << (size * 8)) // count * count
    for _ in range(32):
        request = urllib.request.Request(
            f"https://qrngapi.com/api/random?bytes={size}&format=hex",
            headers={"X-API-Key": token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read(65537))
        except urllib.error.HTTPError as exc:
            raise ValueError(f"QRNG service returned HTTP {exc.code}.") from None
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError("QRNG request failed or returned invalid JSON.") from None
        if isinstance(data, dict):
            provenance = data.get("provenance")
            if (data.get("fallbackUsed") is True
                    or (isinstance(provenance, dict) and provenance.get("fallbackUsed") is True)
                    or data.get("method") == "simulator" or data.get("source") == "simulator"):
                raise ValueError("QRNG service reported a non-quantum source.")
            if (("bytes" in data and (type(data["bytes"]) is not int or data["bytes"] != size))
                    or ("format" in data and data["format"] != "hex")
                    or ("data" in data and "entropy" in data and data["data"] != data["entropy"])):
                raise ValueError("QRNG service returned invalid entropy.")
            value = data.get("data", data.get("entropy"))
        else:
            value = None
        if (not isinstance(value, str) or len(value) != size * 2
                or not re.fullmatch(r"[0-9a-fA-F]+", value)):
            raise ValueError("QRNG service returned invalid entropy.")
        number = int(value, 16)
        if number < limit:
            return number % count
    raise ValueError("QRNG rejected too many samples.")


def choose_winner(count, token):
    """Choose an index and identify the randomness source without exposing secrets."""
    if type(count) is not int or count < 1:
        raise ValueError("No eligible names.")
    if not token:
        return secrets.randbelow(count), "System randomness (fallback: no QRNG_TOKEN)"
    try:
        return qrng_choice(count, token), "Quantum randomness (QRNG)"
    except (OSError, ValueError) as exc:
        message = str(exc)
        if re.fullmatch(r"QRNG service returned HTTP \d{3}\.", message):
            reason = message
        elif message in {"QRNG request failed or returned invalid JSON.",
                         "QRNG service returned invalid entropy.",
                         "QRNG service reported a non-quantum source.",
                         "QRNG rejected too many samples.",
                         "Too many eligible names."}:
            reason = message
        else:
            reason = "QRNG request failed."
        return secrets.randbelow(count), f"System randomness (fallback: {reason})"


def target_angle(index, count):
    """Wheel rotation in degrees placing the selected slice under the top pointer."""
    return (90 - (index + 0.5) * 360 / count) % 360


class Roulette:
    def __init__(self, root, prefs):
        self.root, self.prefs = root, prefs
        self.busy = False
        self.rotation = 0.0
        self.wheel_names = eligible(prefs["names"])
        self.queue = queue.Queue()
        root.title("Name roulette")
        root.minsize(760, 580)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        panel = ttk.Frame(root, padding=10)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)
        panel.rowconfigure(6, weight=1)
        ttk.Label(panel, text="Add names (one per line; Enter adds, Shift+Enter adds a line)").grid(row=0, column=0, sticky="w")
        self.input = tk.Text(panel, height=3, width=32, wrap="word")
        self.input.grid(row=1, column=0, sticky="ew")
        self.input.bind("<Return>", self.add_on_enter)
        self.add_button = ttk.Button(panel, text="Add names", command=self.add_names)
        self.add_button.grid(row=2, column=0, sticky="ew", pady=(4, 8))
        ttk.Label(panel, text="Names (enabled / disabled)").grid(row=3, column=0, sticky="nw")
        main_list = ttk.Frame(panel)
        main_list.grid(row=3, column=0, sticky="nsew", pady=(22, 0))
        self.names_list = self.make_list(main_list)
        self.names_list.bind("<space>", self.toggle_key)
        self.names_list.bind("<Delete>", self.remove_key)
        self.cooldown_button = ttk.Button(main_list, text="Send to cooldown",
                                          command=self.send_to_cooldown)
        self.cooldown_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        row = ttk.Frame(panel)
        row.grid(row=4, column=0, sticky="ew", pady=4)
        self.toggle_button = ttk.Button(row, text="Enable / disable", command=self.toggle)
        self.toggle_button.pack(side="left", fill="x", expand=True)
        self.remove_button = ttk.Button(row, text="Remove", command=self.remove)
        self.remove_button.pack(side="left", fill="x", expand=True)
        ttk.Label(panel, text="Cooldown").grid(row=5, column=0, sticky="w", pady=(8, 0))
        cool_list = ttk.Frame(panel)
        cool_list.grid(row=6, column=0, sticky="nsew")
        self.cool_list = self.make_list(cool_list)
        row = ttk.Frame(panel)
        row.grid(row=7, column=0, sticky="ew", pady=4)
        self.return_button = ttk.Button(row, text="Return to wheel", command=self.return_to_wheel)
        self.return_button.pack(side="left", fill="x", expand=True)
        self.remove_cool_button = ttk.Button(row, text="Remove", command=lambda: self.remove(True))
        self.remove_cool_button.pack(side="left", fill="x", expand=True)
        self.cool_list.bind("<Delete>", lambda event: self.remove_key(event, True))

        wheel = ttk.Frame(root, padding=10)
        wheel.grid(row=0, column=1, sticky="nsew")
        wheel.columnconfigure(0, weight=1)
        wheel.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(wheel, highlightthickness=0, background="white", width=400, height=400)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        options = ttk.Frame(wheel)
        options.grid(row=1, column=0, sticky="ew", pady=5)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Spin duration (seconds)").grid(row=0, column=0, sticky="w")
        self.duration = tk.StringVar(value=str(prefs["duration"]))
        self.duration_entry = ttk.Entry(options, textvariable=self.duration)
        self.duration_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.duration_entry.bind("<FocusOut>", self.change_duration)
        self.duration_entry.bind("<Return>", self.change_duration)
        ttk.Label(options, text="Color scheme").grid(row=1, column=0, sticky="w")
        self.scheme = ttk.Combobox(options, values=list(SCHEMES), state="readonly")
        self.scheme.set(prefs["scheme"])
        self.scheme.grid(row=1, column=1, sticky="ew", padx=8)
        self.scheme.bind("<<ComboboxSelected>>", self.change_scheme)
        buttons = ttk.Frame(wheel)
        buttons.grid(row=2, column=0, sticky="ew", pady=5)
        self.spin_button = ttk.Button(buttons, text="Spin", command=self.spin)
        self.spin_button.pack(side="left", fill="x", expand=True)
        self.test_button = ttk.Button(buttons, text="Test run", command=lambda: self.spin(test=True))
        self.test_button.pack(side="left", fill="x", expand=True)
        self.result = tk.StringVar(value=self.result_text())
        ttk.Label(wheel, textvariable=self.result, wraplength=420).grid(row=3, column=0, sticky="ew")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(wheel, textvariable=self.status, wraplength=420).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.refresh_lists()
        self.draw()

    @staticmethod
    def make_list(frame):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        widget = tk.Listbox(frame, exportselection=False, height=4)
        widget.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        return widget

    def result_text(self):
        winner = self.prefs["last_winner"]
        return (f"Last winner: {winner}\n"
                f"Source: {self.prefs['last_source']}") if winner else "No winner yet."

    def refresh_lists(self):
        self.main_items = [item for item in self.prefs["names"] if item["cooldown"] == 0]
        self.cool_items = [item for item in self.prefs["names"] if item["cooldown"] > 0]
        if self.busy:
            self.names_list.configure(state="normal")
            self.cool_list.configure(state="normal")
        self.names_list.delete(0, "end")
        self.cool_list.delete(0, "end")
        for item in self.main_items:
            self.names_list.insert("end", f"{item['name']} — {'enabled' if item['enabled'] else 'disabled'}")
        for item in self.cool_items:
            n = item["cooldown"]
            state = " (disabled)" if not item["enabled"] else ""
            self.cool_list.insert("end", f"{item['name']} — {n} {'turn' if n == 1 else 'turns'} left{state}")
        if self.busy:
            self.names_list.configure(state="disabled")
            self.cool_list.configure(state="disabled")

    def save_change(self, updated, list_changed=False):
        try:
            save_prefs(updated)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.status.set(f"Save failed: {exc}")
            return False
        self.prefs = updated
        if list_changed:
            self.wheel_names = eligible(updated["names"])
            self.rotation = 0
            self.refresh_lists()
            self.draw()
        return True

    def add_on_enter(self, event):
        if event.state & 1:
            return None
        self.add_names()
        return "break"

    def add_names(self):
        if self.busy:
            return
        values = [line.strip() for line in self.input.get("1.0", "end").splitlines() if line.strip()]
        seen = {item["name"].casefold() for item in self.prefs["names"]}
        for name in values:
            if name.casefold() in seen:
                self.status.set(f"Duplicate name: {name}")
                return
            seen.add(name.casefold())
        if not values:
            self.status.set("Enter at least one name.")
            return
        updated = copy.deepcopy(self.prefs)
        updated["names"].extend({"name": name, "enabled": True, "cooldown": 0} for name in values)
        if self.save_change(updated, True):
            self.input.delete("1.0", "end")
            self.status.set("Names added.")

    def selected(self, cooldown=False):
        widget, items = (self.cool_list, self.cool_items) if cooldown else (self.names_list, self.main_items)
        selection = widget.curselection()
        return items[selection[0]]["name"] if selection else None

    def remove(self, cooldown=False):
        if self.busy:
            return
        name = self.selected(cooldown)
        if name is None:
            self.status.set("Select a name to remove.")
            return
        updated = copy.deepcopy(self.prefs)
        updated["names"] = [item for item in updated["names"] if item["name"] != name]
        if self.save_change(updated, True):
            self.status.set("Name removed.")

    def remove_key(self, event, cooldown=False):
        self.remove(cooldown)
        return "break"

    def toggle_key(self, event):
        self.toggle()
        return "break"

    def toggle(self):
        if self.busy:
            return
        name = self.selected()
        if name is None:
            self.status.set("Select a name to enable or disable.")
            return
        updated = copy.deepcopy(self.prefs)
        item = next(item for item in updated["names"] if item["name"] == name)
        if item["enabled"]:
            item["enabled"] = False
        else:
            enable_name(item)
        if self.save_change(updated, True):
            self.status.set("Name updated.")

    def send_to_cooldown(self):
        if self.busy:
            return
        name = self.selected()
        if name is None:
            self.status.set("Select a name to send to cooldown.")
            return
        updated = copy.deepcopy(self.prefs)
        next(item for item in updated["names"] if item["name"] == name)["cooldown"] = 3
        if self.save_change(updated, True):
            self.status.set("Name sent to cooldown for 3 turns.")

    def return_to_wheel(self):
        if self.busy:
            return
        name = self.selected(True)
        if name is None:
            self.status.set("Select a cooldown name to return.")
            return
        updated = copy.deepcopy(self.prefs)
        enable_name(next(item for item in updated["names"] if item["name"] == name))
        if self.save_change(updated, True):
            self.status.set("Name returned to wheel.")

    def change_duration(self, event=None):
        if self.busy:
            return False
        try:
            duration = float(self.duration.get())
        except ValueError:
            self.status.set("Enter a positive finite spin duration in seconds.")
            return False
        if not valid_duration(duration):
            self.status.set("Enter a positive finite spin duration in seconds.")
            return False
        if duration != self.prefs["duration"]:
            updated = copy.deepcopy(self.prefs)
            updated["duration"] = duration
            if not self.save_change(updated):
                return False
        if event is not None:
            self.status.set("Spin duration set.")
        return True

    def change_scheme(self, event=None):
        updated = copy.deepcopy(self.prefs)
        updated["scheme"] = self.scheme.get()
        if self.save_change(updated):
            self.draw()
        else:
            self.scheme.set(self.prefs["scheme"])

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.input, self.names_list, self.cool_list, self.add_button,
                       self.toggle_button, self.remove_button, self.return_button,
                       self.cooldown_button, self.remove_cool_button,
                       self.duration_entry, self.spin_button, self.test_button):
            widget.configure(state=state)
        self.scheme.configure(state="disabled" if busy else "readonly")

    def spin(self, test=False):
        if self.busy:
            return
        names = eligible(self.prefs["names"])
        if not names:
            self.status.set("No eligible names. Return someone from cooldown or enable a name.")
            return
        if not self.change_duration():
            return
        self.set_busy(True)
        if test:
            self.queue.put((secrets.randbelow(len(names)), "Test run"))
            self.poll(names, test=True)
            return
        self.status.set("Requesting quantum randomness…")

        def worker():
            try:
                try:
                    token = read_token()
                except ValueError:
                    self.queue.put((secrets.randbelow(len(names)),
                                    "System randomness (fallback: cannot read QRNG_TOKEN)"))
                    return
                self.queue.put(choose_winner(len(names), token))
            except Exception:
                self.queue.put((None, "Randomness unavailable; spin not completed."))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(50, lambda: self.poll(names))

    def poll(self, names, test=False):
        try:
            index, source = self.queue.get_nowait()
        except queue.Empty:
            self.root.after(50, lambda: self.poll(names, test))
            return
        if index is None:
            self.status.set(source)
            self.set_busy(False)
            return
        if not test:
            updated = commit_winner(self.prefs, names[index], source=source)
            if not self.save_change(updated):
                self.set_busy(False)
                return
        self.wheel_names = names
        self.refresh_lists()
        start = self.rotation % 360
        end = target_angle(index, len(names))
        distance = 360 * 4 + (end - start) % 360
        duration = self.prefs["duration"]
        started = time.monotonic()
        self.status.set(f"Spinning… {source}")

        def animate():
            progress = min(1.0, (time.monotonic() - started) / duration)
            self.rotation = start + distance * (1 - (1 - progress) ** 3)
            self.draw()
            if progress < 1:
                self.root.after(16, animate)
            else:
                self.rotation = end
                self.draw()
                self.result.set(self.result_text())
                self.status.set(f"Spin complete. {source}")
                self.set_busy(False)

        animate()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        radius = max(1, min(width, height) / 2 - 20)
        cx, cy = width / 2, height / 2
        names = self.wheel_names
        if not names:
            canvas.create_text(cx, cy, text="No eligible names", fill="black")
            return
        colors = SCHEMES[self.prefs["scheme"]]
        extent = 360 / len(names)
        label_font = font.nametofont("TkDefaultFont")
        max_width = max(0, radius * 0.65)
        for index, name in enumerate(names):
            angle = self.rotation + index * extent
            bounds = (cx-radius, cy-radius, cx+radius, cy+radius)
            if len(names) == 1:
                canvas.create_oval(*bounds, fill=colors[0], outline="#555555")
            else:
                canvas.create_arc(*bounds, start=angle, extent=extent,
                                  fill=colors[index % len(colors)], outline="#555555")
            mid = math.radians(angle + extent / 2)
            label = name
            if label_font.measure(label) > max_width:
                while label and label_font.measure(label + "…") > max_width:
                    label = label[:-1]
                label += "…"
            canvas.create_text(cx + radius * 0.62 * math.cos(mid),
                               cy - radius * 0.62 * math.sin(mid), text=label, fill="black")
        canvas.create_polygon(cx, cy-radius+8, cx-8, cy-radius-12, cx+8, cy-radius-12, fill="#222222")


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        prefs = load_prefs()
    except ValueError as exc:
        messagebox.showerror("Invalid preferences", str(exc), parent=root)
        root.destroy()
        return
    prefs["last_run"] = datetime.datetime.now().astimezone().isoformat()
    try:
        save_prefs(prefs)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        messagebox.showerror("Cannot save preferences", str(exc), parent=root)
        root.destroy()
        return
    root.deiconify()
    Roulette(root, prefs)
    root.mainloop()


if __name__ == "__main__":
    main()
