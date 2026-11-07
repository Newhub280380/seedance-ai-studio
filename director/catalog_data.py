import json
from pathlib import Path


def load_catalog(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {model["id"]: model for model in document["models"]}
    return document, by_id


def max_input_images(model):
    constraints = model.get("constraints") or {}
    if constraints.get("combineImages") is False:
        return 1
    if "maxInputImages" in constraints and constraints["maxInputImages"] is not None:
        return int(constraints["maxInputImages"])
    return 3
