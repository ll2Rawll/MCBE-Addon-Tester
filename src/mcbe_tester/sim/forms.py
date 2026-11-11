"""Inspectable forms with shared visual/control layout and asynchronous responses."""

import math
from copy import deepcopy

from .diagnostics import Unsupported

INPUTS = {"toggle", "slider", "dropdown", "text"}
PAGE_SIZE = 6


def text(value, strings, depth=0):
    if depth > 12:
        raise ValueError("RawMessage depth limit")
    if isinstance(value, str):
        return value[:4096]
    if not isinstance(value, dict):
        raise ValueError("Form text requires a string or RawMessage")
    if "rawtext" in value:
        return "".join(text(v, strings, depth + 1) for v in value["rawtext"][:128])[:4096]
    if "text" in value:
        return text(value["text"], strings, depth + 1)
    if "translate" in value:
        result = strings.get(value["translate"], value["translate"])
        args = value.get("with", [])
        if not isinstance(args, list):
            raise Unsupported("RawMessage nested translation arguments")
        for i, arg in enumerate(args):
            replacement = text(arg, strings, depth + 1)
            result = result.replace(f"%{i + 1}", replacement).replace("%s", replacement, 1)
        return result[:4096]
    raise Unsupported("RawMessage selector/score expansion")


def validate(control, value):
    kind = control["type"]
    if kind == "toggle" and type(value) is not bool:
        raise ValueError("Toggle requires a boolean")
    if kind == "text" and (not isinstance(value, str) or len(value) > 2048):
        raise ValueError("Text field limit/type")
    if kind == "dropdown" and (type(value) is not int or not 0 <= value < len(control["options"])):
        raise ValueError("Invalid dropdown choice")
    if kind == "slider":
        minimum, maximum, step = control["min"], control["max"], control.get("step", 1)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (minimum, maximum, step, value)):
            raise ValueError("Slider requires finite numbers")
        if not minimum <= value <= maximum or step <= 0:
            raise ValueError("Slider outside range or invalid step")
        if abs((value - minimum) / step - round((value - minimum) / step)) > 1e-6:
            raise ValueError("Slider value does not match its step")


class Forms:
    def __init__(self, world):
        self.world = world
        self.current = None

    def show(self, request_id, player, data):
        if player != "player":
            raise ValueError("Forms require a player")
        if self.current or self.world.player["menu"] != "game":
            return {"canceled": True, "cancelationReason": "UserBusy"}
        if data["kind"] not in {"action", "message", "modal"} or len(data["controls"]) > 40:
            raise ValueError("Invalid/oversized form")
        data = deepcopy(data)
        for key in ("title", "body", "submit"):
            if key in data:
                data[key] = text(data[key], self.world.catalog.strings)
        for control in data["controls"]:
            if control["type"] not in INPUTS | {"button", "header", "label", "divider"}:
                raise Unsupported("Form control: " + control["type"])
            for key in ("text", "label", "placeholder"):
                if key in control:
                    control[key] = text(control[key], self.world.catalog.strings)
            if "options" in control:
                if not 1 <= len(control["options"]) <= 128:
                    raise ValueError("Dropdown option limit")
                control["options"] = [text(v, self.world.catalog.strings) for v in control["options"]]
            if control["type"] in INPUTS:
                validate(control, control["value"])
        self.current = {"id": request_id, **data, "page": 0, "focus": None}
        self.world.player["menu"] = "form"
        self.world.log("form-opened", form=deepcopy(self.current))

    def inputs(self):
        if not self.current:
            raise ValueError("No form is open")
        return [c for c in self.current["controls"] if c["type"] in INPUTS]

    def set_value(self, index, value):
        controls = self.inputs()
        if type(index) is not int or not 0 <= index < len(controls):
            raise ValueError("Form input index outside range")
        validate(controls[index], value)
        controls[index]["value"] = value
        return {"input": index, "value": value}

    def respond(self, selection=None, values=None, canceled=False):
        form = self.current
        if form is None:
            raise ValueError("No form is open")
        response = {"canceled": bool(canceled)}
        if canceled:
            response["cancelationReason"] = "UserClosed"
        elif form["kind"] in {"action", "message"}:
            buttons = [c for c in form["controls"] if c["type"] == "button"]
            if type(selection) is not int or not 0 <= selection < len(buttons):
                raise ValueError("Form selection outside button range")
            response["selection"] = selection
        else:
            controls = self.inputs()
            values = values if values is not None else [c["value"] for c in controls]
            if not isinstance(values, list) or len(values) != len(controls):
                raise ValueError("One form value per input control is required")
            for control, value in zip(controls, values, strict=True):
                validate(control, value)
            response["formValues"] = deepcopy(values)
        self.current = None
        self.world.player["menu"] = "game"
        self.world.log("form-response", response=response)
        if self.world.script:
            self.world.script.call("formResponse", {"id": form["id"], "response": response})
        return response

    def regions(self):
        if not self.current:
            return []
        regions = [{"id": "form:cancel", "rect": [0.82, 0.12, 0.9, 0.2], "label": "X"}]
        page = self.current["page"]
        input_index = button_index = 0
        for i, control in enumerate(self.current["controls"]):
            kind = control["type"]
            index = input_index if kind in INPUTS else button_index
            input_index += kind in INPUTS
            button_index += kind == "button"
            if i // PAGE_SIZE != page:
                continue
            y = 0.29 + (i % PAGE_SIZE) * 0.068
            region = {
                "rect": [0.16, y, 0.84, y + 0.058],
                "kind": kind,
                "label": control.get("label", control.get("text", "")),
            }
            if kind == "button":
                region.update(id=f"form:{index}", icon=control.get("iconPath"))
            elif kind in INPUTS:
                region.update(
                    id=f"form:input:{index}",
                    input=index,
                    value=control["value"],
                    focused=self.current["focus"] == index,
                )
                if kind == "dropdown":
                    region["value_label"] = control["options"][control["value"]]
            else:
                region.update(id=f"form:label:{i}", disabled=True)
            regions.append(region)
        pages = max(1, math.ceil(len(self.current["controls"]) / PAGE_SIZE))
        if page:
            regions.append({"id": "form:prev", "rect": [0.16, 0.77, 0.29, 0.84], "label": "Previous"})
        if page < pages - 1:
            regions.append({"id": "form:next", "rect": [0.71, 0.77, 0.84, 0.84], "label": "Next"})
        if self.current["kind"] == "modal":
            regions.append(
                {
                    "id": "form:submit",
                    "rect": [0.34, 0.77, 0.66, 0.84],
                    "label": self.current.get("submit", "Submit"),
                }
            )
        return regions

    def click(self, region, x):
        target = region["id"].removeprefix("form:")
        if region.get("disabled"):
            return {"clicked": None}
        if target == "cancel":
            return self.respond(canceled=True)
        if target == "submit":
            return self.respond()
        if target in {"next", "prev"}:
            self.current["page"] += 1 if target == "next" else -1
            return {"page": self.current["page"]}
        if target.startswith("input:"):
            index = region["input"]
            self.current["focus"] = index
            control = self.inputs()[index]
            if control["type"] == "toggle":
                return self.set_value(index, not control["value"])
            if control["type"] == "dropdown":
                return self.set_value(index, (control["value"] + 1) % len(control["options"]))
            if control["type"] == "slider":
                a, _, b, _ = region["rect"]
                steps = math.floor((control["max"] - control["min"]) / control["step"])
                return self.set_value(
                    index, control["min"] + round((x - a) / (b - a) * steps) * control["step"]
                )
            return {"focus": index}
        return self.respond(selection=int(target))
