"""Spine key-channel sampling, shared by every leg that has to reproduce a curve.

Spine keys may carry a ``curve``: four values for a single-axis channel
(rotate), eight for a two-axis one (translate: x then y). Interpolating such a
segment linearly bakes a straight line where the source eases — the hero's head
drifted 0.24 units mid-segment. The curve is expanded with the same 10-point
forward-difference table the runtime uses (``model.bezier_table_point``), so a
sampled value matches the runtime exactly.

Two callers need the identical numbers: the constraint solver, which samples a
pose to solve IK/path constraints, and the Godot writer, which bakes the mesh
deform into a `PackedVector2Array` track that Godot cannot interpolate itself.
"""

from __future__ import annotations


def sample_key(keys: list, time: float, field: str, default: float) -> float:
    """Sample one key channel at ``time``, honouring Spine bezier curves."""
    if not keys:
        return default
    if time <= keys[0].get("time", 0.0):
        return keys[0].get(field, default)
    for index in range(1, len(keys)):
        previous, current = keys[index - 1], keys[index]
        start, end = previous.get("time", 0.0), current.get("time", 0.0)
        if time > end:
            continue
        if end <= start:
            return current.get(field, default)
        a, b = previous.get(field, default), current.get(field, default)
        curve = previous.get("curve")
        if not isinstance(curve, (list, tuple)) or len(curve) < 4:
            ratio = (time - start) / (end - start)
            return a + (b - a) * ratio
        # Spine stores one curve per axis: 4 values for rotate, 8 for
        # translate (x then y). Axis 0 is always the TIME curve; the value
        # curve is the axis matching the field. Sampling axis 0 for the value
        # returns the time component and eases the wrong way.
        axis = {"x": 0, "y": 1}.get(field, 0)
        table = [bezier_table(curve, axis, step, start, end, a, b)
                 for step in range(11)]
        for step in range(10):
            x0, y0 = table[step]
            x1, y1 = table[step + 1]
            if time <= x1 or step == 9:
                span = x1 - x0
                ratio = 0.0 if span <= 0 else (time - x0) / span
                return y0 + (y1 - y0) * ratio
        return b
    return keys[-1].get(field, default)


def bezier_table(curve: list, axis: int, step: int, time1: float,
                 time2: float, value1: float, value2: float) -> tuple:
    """One entry of the runtime's 10-point bezier table.

    ``axis`` selects the VALUE curve (0 for x/rotate, 1 for y); the TIME curve
    is always axis 0 when a value axis exists. Returns (time, value).
    """
    from .model import bezier_table_point
    time_point = bezier_table_point(curve, 0, step, time1, time2, value1, value2)
    if axis == 0:
        return time_point
    value_point = bezier_table_point(curve, axis, step, time1, time2, value1, value2)
    return (time_point[0], value_point[1])


def clamp_first_key(entries: list, fields: tuple) -> list:
    """Insert an extrapolated key at t=0 when a track starts late.

    Godot's AnimationPlayer extrapolates the FIRST REAL SEGMENT (key1 -> key2)
    backwards: value(t) = key1 + slope*(t1 - t). Both the Spine and the
    DragonBones runtimes instead hold the first frame's value, so any track
    whose first key is not at t=0 diverges from the engine — and a comparison
    frozen at t=0 then shows two different poses. Writing the extrapolated key
    makes every runtime sample the same ramp.

    ``entries`` are JSON frame dicts carrying ``time`` plus the named value
    ``fields``; a field missing from either key is left alone.
    """
    if not entries:
        return entries
    out = list(entries)
    if out[0]["time"] > 0.0:
        first = dict(out[0])
        second = out[1] if len(out) > 1 else out[0]
        first["time"] = 0.0
        if second is not out[0]:
            time1, time2 = out[0]["time"], second["time"]
            span = time2 - time1
            if span > 0:
                for field in fields:
                    if field in out[0] and field in second:
                        slope = (second[field] - out[0][field]) / span
                        first[field] = round(
                            out[0][field] + slope * (time1 - 0.0), 6)
        first.pop("curve", None)
        out.insert(0, first)
    return out
