"""Expand drilling/boring cycles to explicit motion in FreeCAD coordinates.

G81/G82/G85 semantics follow the LinuxCNC RS274 reference. This is an
analysis/simulation helper; it never rewrites a native operation or G-code file.
Peck cycles need a controller's retract-distance convention and are not guessed.
"""

import math

import FreeCAD as App
import Path


class CannedCycleState:
    supported = ("G81", "G82", "G85")

    def __init__(self):
        self.return_initial = True
        self.reset()

    def reset(self):
        self.code = None
        self.mode = None
        self.initial_level = None
        self.sticky = {}

    def expand(self, name, params, position, plane, absolute, scale):
        """Return canonical G0/G1/G4 commands and the final tip position."""
        mode = (plane, absolute, scale)
        if self.code is not None and mode != self.mode:
            raise ValueError(
                "Cancel the canned cycle before changing plane, units or distance mode"
            )
        if name not in self.supported:
            raise ValueError("Unsupported canned cycle: " + name)
        if any(key not in set("XYZRLPF") for key in params):
            raise ValueError(
                "Unsupported canned cycle words; rotary/auxiliary axes and peck cycles require separate handling"
            )
        if not all(math.isfinite(float(value)) for value in params.values()):
            raise ValueError("Canned cycle parameters must be finite")
        a, b, w = {"G17": (0, 1, 2), "G18": (2, 0, 1), "G19": (1, 2, 0)}[plane]
        depth_key = "XYZ"[w]
        if not any(key in params for key in "XYZ"):
            raise ValueError("Canned cycle requires at least one axis word")
        if self.code != name:
            # R/Z/P are sticky only for consecutive occurrences of that cycle.
            self.sticky = {}
        self.sticky.update(
            {key: float(params[key]) for key in ("R", depth_key, "P") if key in params}
        )
        if "R" not in self.sticky or depth_key not in self.sticky:
            raise ValueError(
                "First canned cycle requires R and the drilling-axis depth"
            )
        dwell = self.sticky.get("P", 0.0)
        if name == "G82" and ("P" not in self.sticky or dwell < 0):
            raise ValueError("G82 requires a nonnegative P dwell in seconds")
        if "P" in params and name != "G82":
            raise ValueError("P dwell is supported only for G82")
        repeats = float(params.get("L", 1))
        if repeats < 1 or repeats != int(repeats) or repeats > 10000:
            raise ValueError("Canned cycle L must be a positive integer at most 10000")
        xyz = list(position)
        retract = self.sticky["R"] * scale
        if not absolute:
            retract += xyz[w]
        depth = self.sticky[depth_key] * scale
        if not absolute:
            depth += retract
        if depth > retract:
            raise ValueError("Canned cycle depth must not be above R")
        if self.initial_level is None:
            self.initial_level = xyz[w]
        self.code, self.mode = name, mode
        clear = max(self.initial_level, retract) if self.return_initial else retract
        commands = []

        def move(code, coordinates):
            endpoint = list(xyz)
            for axis, value in coordinates.items():
                endpoint[axis] = value
            if endpoint != xyz:
                commands.append(Path.Command(code, dict(zip("XYZ", endpoint))))
            xyz[:] = endpoint

        # Lift before traversing if the starting position is below the R plane.
        if xyz[w] < retract:
            move("G0", {w: retract})
        for _ in range(int(repeats)):
            lateral = {}
            for axis in (a, b):
                if "XYZ"[axis] in params:
                    value = float(params["XYZ"[axis]]) * scale
                    lateral[axis] = value if absolute else xyz[axis] + value
            move("G0", lateral)
            move("G0", {w: retract})
            move("G1", {w: depth})
            if name == "G82":
                commands.append(Path.Command("G4", {"P": dwell}))
            if name == "G85":
                move("G1", {w: retract})
            move("G0", {w: clear})
        return commands, App.Vector(*xyz)
