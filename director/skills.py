import hashlib
import json
from pathlib import Path


SKILL_FILES = {
    "venice-create-image": "veniceai-skills/skills/venice-create-image/SKILL.md",
    "venice-image-edit": "veniceai-skills/skills/venice-image-edit/SKILL.md",
    "venice-product-photoshoot": "veniceai-skills/skills/venice-product-photoshoot/SKILL.md",
    "venice-create-video": "veniceai-skills/skills/venice-create-video/SKILL.md",
    "venice-video": "veniceai-skills/skills/venice-video/SKILL.md",
    "venice-create-audio": "veniceai-skills/skills/venice-create-audio/SKILL.md",
    "venice-explainer-video": "veniceai-skills/skills/venice-explainer-video/SKILL.md",
    "venice-models": "veniceai-skills/skills/venice-models/SKILL.md",
}

REQUIRED_SNIPPETS = {
    "venice-create-image": ["/api/v1/image/generate"],
    "venice-image-edit": ["/image/edit", "/image/multi-edit", "modelId"],
    "venice-product-photoshoot": ["/api/v1/image/multi-edit"],
    "venice-create-video": ["/api/v1/video/queue", "/api/v1/video/retrieve", "/api/v1/video/quote"],
    "venice-video": ["/api/v1/video/queue", "/api/v1/video/retrieve"],
    "venice-create-audio": ["/api/v1/audio/speech"],
    "venice-explainer-video": ["/api/v1/video/quote", "/video/queue", "/api/v1/audio/speech"],
    "venice-models": ["/api/v1/models"],
}


def load_skills(vendor_root):
    vendor_root = Path(vendor_root)
    manifest = json.loads((vendor_root / "manifest.json").read_text(encoding="utf-8"))
    expected = {item["file"]: item for item in manifest}
    loaded = {}
    for name, relative in SKILL_FILES.items():
        path = vendor_root / relative
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        record = expected[relative]
        text = data.decode("utf-8")
        missing = [snippet for snippet in REQUIRED_SNIPPETS[name] if snippet not in text]
        loaded[name] = {
            "name": name,
            "path": relative,
            "sha256": digest,
            "sha256_ok": digest == record["sha256"],
            "license": record.get("license"),
            "source": record.get("source"),
            "role": "execution_instructions",
            "executes_backend": False,
            "missing_endpoints": missing,
        }
    return loaded


def skills_ready(skills, names):
    problems = []
    for name in names:
        skill = skills.get(name)
        if not skill or not skill["sha256_ok"] or skill["missing_endpoints"]:
            problems.append(name)
    return problems
