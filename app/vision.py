"""
vision.py — turn a top-down image of a 60"x44" table into a scene, via any
vision model on OpenRouter.

The image is first warped onto a clean board image (20 px per inch) with a
labelled 2" grid in table coordinates, so the model only has to read grid
positions, never guess pixel scale. It answers per unit (centre + how many
models it can see); unseen units are left out and end up in reserves.
"""

import base64
import json
import re
import urllib.error
import urllib.request

import cv2
import numpy as np

W_IN, H_IN = 60, 44
PX = 20          # board image pixels per inch
MARGIN = 44      # label margin around the board

OPENROUTER = "https://openrouter.ai/api/v1"


# --------------------------------------------------------------------------
# Image preparation

def decode_data_url(data_url):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Couldn't read that image")
    return img


def to_data_url(img, quality=88):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


# Where each army deploys, per LCT deployment (Red / Blue), and which way its
# models should face. Board coordinates: +x right, +z up.
DEPLOYMENT_SIDES = {
    "Hammer and Anvil": ("along the right (+x) short edge", "along the left (-x) short edge", 270, 90),
    "Crucible of Battle": ("in the right (+x) half", "in the left (-x) half", 270, 90),
    "Tipping Point": ("along the right (+x) short edge", "along the left (-x) short edge", 270, 90),
    "Dawn of War": ("along the top (+z) long edge", "along the bottom (-z) long edge", 180, 0),
    "Sweeping Engagement": ("along the top (+z) long edge", "along the bottom (-z) long edge", 180, 0),
    "Search and Destroy": ("in the top-right (+x, +z) quarter", "in the bottom-left (-x, -z) quarter", 225, 45),
}


def facings(deployment):
    d = DEPLOYMENT_SIDES.get(deployment)
    return (d[2], d[3]) if d else (180, 0)


def frame(rect):
    """Board frame in image pixels: centre, px per inch, and the image-space
    directions of table +x and +z. rot is how far the table is turned
    counter-clockwise in the image (0 = +x points right)."""
    if "cx" not in rect:  # older scenes stored left/top/width with no rotation
        rect = {"cx": rect["left"] + rect["width"] / 2,
                "cy": rect["top"] + rect["width"] * H_IN / W_IN / 2, "width": rect["width"], "rot": 0}
    ppi = rect["width"] / W_IN
    t = np.radians(rect.get("rot", 0))
    ex = np.array([np.cos(t), -np.sin(t)]) * ppi
    ez = np.array([-np.sin(t), -np.cos(t)]) * ppi
    return np.array([rect["cx"], rect["cy"]]), ex, ez


def board_image(img, rect):
    """Warp the board frame (which may be rotated and may run off the image)
    to a gridded top-down view: +x right, +z up."""
    c, ex, ez = frame(rect)
    # output pixel (u, v) -> table (x, z) -> image point
    ax, az = 1 / PX, -1 / PX
    bx, bz = -MARGIN / PX - W_IN / 2, MARGIN / PX + H_IN / 2
    col_u = ex * ax
    col_v = ez * az
    off = c + ex * bx + ez * bz
    m = np.float32([[col_u[0], col_v[0], off[0]], [col_u[1], col_v[1], off[1]]])
    size = (W_IN * PX + 2 * MARGIN, H_IN * PX + 2 * MARGIN)
    out = cv2.warpAffine(img, m, size, flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP, borderValue=(90, 90, 90))

    def px(x, z):
        return int(MARGIN + (x + W_IN / 2) * PX), int(MARGIN + (H_IN / 2 - z) * PX)

    grid = out.copy()
    for x in range(-30, 31, 2):
        major = x % 10 == 0
        cv2.line(grid, px(x, -22), px(x, 22), (0, 230, 255) if major else (0, 190, 255), 2 if major else 1)
    for z in range(-22, 23, 2):
        major = z % 10 == 0
        cv2.line(grid, px(-30, z), px(30, z), (0, 230, 255) if major else (0, 190, 255), 2 if major else 1)
    out = cv2.addWeighted(grid, 0.55, out, 0.45, 0)
    cv2.rectangle(out, px(-30, 22), px(30, -22), (0, 255, 255), 2)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for x in range(-28, 29, 4):
        for y in (MARGIN - 12, MARGIN + H_IN * PX + 30):
            cv2.putText(out, str(x), (px(x, 0)[0] - 10, y), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    for z in range(-20, 21, 4):
        for x in (4, MARGIN + W_IN * PX + 6):
            cv2.putText(out, str(z), (x, px(0, z)[1] + 5), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------
# OpenRouter

_models_cache = None


def vision_models():
    """Every OpenRouter model that accepts images, with whether it supports
    JSON-schema output."""
    global _models_cache
    if _models_cache is None:
        with urllib.request.urlopen(f"{OPENROUTER}/models", timeout=30) as r:
            data = json.load(r)["data"]
        models = []
        for m in data:
            if "image" not in (m.get("architecture") or {}).get("input_modalities", []):
                continue
            params = m.get("supported_parameters") or []
            models.append({
                "id": m["id"], "name": m.get("name", m["id"]),
                "structured": "structured_outputs" in params or "response_format" in params,
                "prompt_price": (m.get("pricing") or {}).get("prompt"),
                "completion_price": (m.get("pricing") or {}).get("completion"),
            })
        _models_cache = sorted(models, key=lambda m: m["id"])
    return _models_cache


SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "army": {"type": "string", "enum": ["red", "blue"]},
                    "unit": {"type": "string"},
                    "n": {"type": "integer"},
                    "x": {"type": "number"},
                    "z": {"type": "number"},
                    "count": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["army", "unit", "n", "x", "z", "count", "confidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["units", "notes"],
    "additionalProperties": False,
}


def roster_text(label, parsed, hint):
    lines = [f"{label} army: {parsed['faction']}" + (f" / {parsed['sub']}" if parsed.get("sub") else "")]
    if hint:
        lines.append(f"How to recognise them: {hint}")
    seen = {}
    for u in parsed["units"]:
        seen[u["name"]] = seen.get(u["name"], 0) + 1
        kinds = {}
        for m in u["models"]:
            kinds[m["name"]] = kinds.get(m["name"], 0) + 1
        comp = ", ".join(f"{c}x {k}" for k, c in kinds.items())
        lines.append(f'- "{u["name"]}" n={seen[u["name"]]}: {len(u["models"])} models ({comp})')
    return "\n".join(lines)


PROMPT = """This is a top-down view of a Warhammer 40,000 game on a 60" x 44" table, \
warped so the table fills the yellow rectangle. The grid is in table inches: \
x runs -30 (left edge) to +30 (right edge), z runs -22 (bottom edge) to +22 (top edge); \
thin lines every 2", thick lines every 10", with labels in the margins. \
Grey areas are outside the original photo.

{deployment}

The image may be from the middle of a game: units can have moved anywhere, died, or be in reserves.

{red}

{blue}

For every unit you can see on the table, give its army ("red" or "blue"), the unit name \
exactly as written above, n (which copy, when a list has the same unit more than once), \
the x and z of the centre of its models in table inches, how many of its models you can \
see, and your confidence. A leader standing with its bodyguard is its own unit at the same \
spot. Leave out anything you can't find rather than guessing; missing units are put in \
reserves. Put anything worth knowing (ambiguities, terrain features you used) in notes."""


def deployment_text(deployment):
    d = DEPLOYMENT_SIDES.get(deployment)
    if not d:
        return "Which edge each army deployed from is unknown."
    return (f"Deployment is {deployment}: RED deployed {d[0]} and BLUE deployed {d[1]}, "
            "which tells you which army is which at the start of the game.")


def call_openrouter(api_key, model, prompt, image_url, structured):
    body = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
    }
    if structured:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "board_state", "strict": True, "schema": SCHEMA}}
    else:
        body["messages"][0]["content"][0]["text"] += (
            "\n\nReply with only a JSON object of this shape: "
            + json.dumps({"units": [{"army": "red", "unit": "...", "n": 1, "x": 0, "z": 0,
                                     "count": 1, "confidence": "high"}], "notes": "..."}))
    req = urllib.request.Request(
        f"{OPENROUTER}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "X-Title": "TTS Bridge"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter {e.code}: {e.read().decode(errors='replace')[:500]}")
    if "error" in resp:
        raise RuntimeError(f"OpenRouter: {resp['error']}")
    content = resp["choices"][0]["message"].get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise RuntimeError(f"Model didn't return JSON: {content[:300]}")
        data = json.loads(m.group(0))
    return data, resp.get("usage", {})


def analyze(api_key, model, img, rect, armies, deployment=None):
    """armies: [{"parsed", "hint"}], Red first. Returns (units, notes, board, usage)."""
    board = board_image(img, rect)
    prompt = PROMPT.format(deployment=deployment_text(deployment),
                           red=roster_text("RED", armies[0]["parsed"], armies[0].get("hint")),
                           blue=roster_text("BLUE", armies[1]["parsed"], armies[1].get("hint")))
    structured = next((m["structured"] for m in vision_models() if m["id"] == model), False)
    data, usage = call_openrouter(api_key, model, prompt, to_data_url(board), structured)
    return data.get("units", []), data.get("notes", ""), board, usage


def validate(units, armies):
    """Keep only units that exist in the lists; clamp counts and positions."""
    out, problems = [[], []], []
    for u in units:
        side = 0 if u.get("army") == "red" else 1
        parsed = armies[side]["parsed"]
        name = u.get("unit", "")
        copies = [x for x in parsed["units"] if x["name"].lower() == name.lower()]
        if not copies:
            problems.append(f"{u.get('army')}: no unit called \"{name}\" in that list")
            continue
        n = min(max(int(u.get("n") or 1), 1), len(copies))
        unit = copies[n - 1]
        out[side].append({
            "unit": unit["name"], "n": n,
            "at": [round(min(max(float(u["x"]), -30), 30), 1), round(min(max(float(u["z"]), -22), 22), 1)],
            "count": min(max(int(u.get("count") or 1), 1), len(unit["models"])),
            "confidence": u.get("confidence", "low"),
        })
    return out, problems
