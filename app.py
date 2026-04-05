import os
import sys
import json
import logging
import traceback
import time
import re
import tempfile
import subprocess
import shutil
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, make_response, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from anthropic import Anthropic
from supabase import create_client
from auth import require_auth, optional_auth

load_dotenv()

app = Flask(__name__)
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
CORS(app, origins=ALLOWED_ORIGINS)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute"],
    storage_uri="memory://",
)

# Configurable log level via environment variable
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(stream=sys.stderr, level=getattr(logging, log_level, logging.INFO))
log = app.logger

FAL_KEY = os.environ.get("FAL_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

APP_VERSION = "2025-02-22-v3"

# In-memory cache for uploaded photos (keyed by a simple token)
_photo_cache = {}


def validate_json(*required_fields):
    """Validate request has JSON body with required fields."""
    data = request.get_json(silent=True)
    if not data:
        return None, (jsonify({"error": "JSON body required"}), 400)
    missing = [f for f in required_fields if f not in data or not data[f]]
    if missing:
        return None, (jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400)
    return data, None


@app.errorhandler(Exception)
def handle_exception(e):
    log.error(f"Unhandled exception: {e}")
    log.error(traceback.format_exc())
    return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": APP_VERSION})


@app.route("/api/config")
def get_config():
    """Return public Supabase config for frontend auth initialization."""
    return jsonify({
        "supabase_url": SUPABASE_URL or "",
        "supabase_anon_key": SUPABASE_KEY or "",
    })


# Hardcoded fallback when Supabase is not configured
_FALLBACK_SHOWS = [
    {"name": "Fifty Shades of Grey", "poster_url": "https://image.tmdb.org/t/p/w500/63kGofUkt1Mx0SIL4XI4Z5AoSgt.jpg"},
    {"name": "Twilight", "poster_url": "https://image.tmdb.org/t/p/w500/3Gkb6jm6962ADUPaCBqzz9CTbn9.jpg"},
    {"name": "Crash Landing on You", "poster_url": "https://image.tmdb.org/t/p/w500/fgBNLPr6mC8pxuR79ENAJY4nBmj.jpg"},
    {"name": "Eternal Love", "poster_url": "https://image.tmdb.org/t/p/w500/paeDktO7Bx2lmv9mEDiHtneoYrF.jpg"},
    {"name": "Crazy Rich Asians", "poster_url": "https://image.tmdb.org/t/p/w500/1XxL4LJ5WHdrcYcihEZUCgNCpAW.jpg"},
    {"name": "My Love from the Star", "poster_url": "https://image.tmdb.org/t/p/w500/o5EYVYCVtDUdajP4rWfv6q0BTmm.jpg"},
    {"name": "Bridgerton", "poster_url": "https://image.tmdb.org/t/p/w500/uXTg565ahu9RwonCX1V2Hex1NU6.jpg"},
    {"name": "Single's Inferno", "poster_url": "https://image.tmdb.org/t/p/w500/86zkkFCrNc4VeqvCANTmpGNgFEF.jpg"},
]


@app.route("/api/shows")
def list_shows():
    """Fetch shows from Supabase, fall back to hardcoded list."""
    if supabase:
        try:
            result = supabase.table("shows").select("name, poster_url").order("sort_order").execute()
            if result.data:
                return jsonify({"shows": result.data})
        except Exception as e:
            log.error(f"[SHOWS] Supabase error: {e}")

    return jsonify({"shows": _FALLBACK_SHOWS})


@app.route("/api/detect-gender", methods=["POST"])
@limiter.limit("20 per hour")
@require_auth
def detect_gender():
    data, err = validate_json("photo")
    if err: return err
    photo = data["photo"]

    # Validate photo is a data URI
    if not photo.startswith("data:image/"):
        return jsonify({"error": "Invalid photo format"}), 400

    # Parse the data URI to extract media type and base64 data
    # Format: data:image/jpeg;base64,/9j/4AAQ...
    header, b64_data = photo.split(",", 1)
    media_type = header.split(":")[1].split(";")[0]

    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=600.0)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Is the person in this photo male or female? Reply with only one word: male or female",
                    },
                ],
            }
        ],
    )

    gender = message.content[0].text.strip().lower()
    # Normalize to "male" or "female"
    if "female" in gender or "woman" in gender:
        gender = "female"
    else:
        gender = "male"

    log.info(f"[DETECT-GENDER] Detected: {gender}")
    return jsonify({"gender": gender})


@app.route("/api/generate-storyboard", methods=["POST"])
@limiter.limit("10 per hour")
@require_auth
def generate_storyboard():
    data, err = validate_json("show_name")
    if err: return err
    show_name = data["show_name"]
    gender = data.get("gender", "male")
    user_name = data.get("user_name", "the protagonist")

    if gender == "female":
        gender_upper = "FEMALE"
        pronoun_sub = "she"
        pronoun_obj = "her"
        pronoun_pos = "her"
    else:
        gender_upper = "MALE"
        pronoun_sub = "he"
        pronoun_obj = "him"
        pronoun_pos = "his"

    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=600.0)

    try:
        log.info(f"[STORYBOARD] Calling Claude for show='{show_name}', gender={gender}, user_name='{user_name}'")
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"You are a storyboard artist for a short drama fan fiction of '{show_name}'. "
                        f"The user's name is '{user_name}' and {pronoun_sub} will be cast as the {gender_upper} lead/love interest character. "
                        f"A reference photo of {user_name} will be provided to the image generator.\n\n"
                        f"You must follow the ACTUAL plot, storyline, and iconic scenes of '{show_name}'. "
                        f"Use the real character names (except the {gender} lead, who is {user_name}), real locations, "
                        f"and real plot points from the show/movie. The 5 acts should retell the key dramatic beats "
                        f"of '{show_name}' faithfully — not a generic romance, but the specific story audiences know and love. "
                        f"Include signature moments, settings, and conflicts that are unique to '{show_name}'.\n\n"
                        f"Each act should build on the previous one and represent "
                        f"a major dramatic beat in the story.\n\n"
                        f"IMPORTANT RULES FOR SCENE DESCRIPTIONS ('scenes' field):\n"
                        f"- Use '{user_name}' to refer to the {gender} lead (the user), not 'the man'/'the woman'.\n"
                        f"- For the OTHER protagonist (love interest / co-lead), use their real character name "
                        f"from '{show_name}' (e.g. 'Rachel Chu', 'Edward Cullen', 'Ri Jeong-hyeok').\n\n"
                        f"IMPORTANT RULES FOR THE 'prompt' FIELD (image generation):\n"
                        f"- The prompt must depict SCENE 1 of the act — the opening/starting moment.\n"
                        f"- BOTH the {gender} lead AND the love interest must appear together in EVERY image. "
                        f"Always describe both characters' positions, actions, and expressions.\n"
                        f"- Refer to the {gender} lead as 'the {gender} from the reference photo'. "
                        f"Describe {pronoun_pos} actions, pose, and expression, "
                        f"but do NOT describe {pronoun_pos} physical appearance (hair color, skin tone, etc.) since "
                        f"{pronoun_pos} look comes from the reference photo.\n"
                        f"- For the OTHER protagonist (the love interest / co-lead), ALWAYS use their full character name "
                        f"from '{show_name}' (e.g. 'Rachel Chu', 'Edward Cullen', 'Ri Jeong-hyeok'). "
                        f"The image generator knows these characters and will generate them accurately by name. "
                        f"Include their name in every prompt.\n"
                        f"- You may also name other supporting characters from the show for accuracy.\n\n"
                        f"For each act, provide:\n"
                        f"- A 'title': a dramatic 3-6 word title for the act\n"
                        f"- A 'prompt': an image generation prompt (2-3 sentences) for SCENE 1 of this act. "
                        f"Describe the visual composition with BOTH protagonists together, "
                        f"setting, cinematic lighting/mood/camera angle, in 9:16 portrait format.\n"
                        f"- A 'scenes': an array of exactly 4 strings, each a short 1-sentence scene description "
                        f"summarizing what happens in that part of the act. Do NOT include 'Scene X:' prefixes — "
                        f"just the description itself, e.g. '{user_name} arrives at the grand estate for the first time'.\n\n"
                        f"Return ONLY a JSON array of 5 objects, each with 'act_number' (1-5), 'title', 'prompt', and 'scenes'. "
                        f"No markdown, no explanation, just the JSON array."
                    ),
                }
            ],
        )
    except Exception as e:
        log.error(f"[STORYBOARD] Claude API error: {e}")
        log.error(traceback.format_exc())
        return jsonify({"error": f"Claude API error: {str(e)}"}), 502

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    log.info(f"[STORYBOARD] Raw Claude response: {raw[:500]}")

    try:
        acts = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"[STORYBOARD] JSON parse error: {e}")
        log.error(f"[STORYBOARD] Raw text: {raw[:1000]}")
        return jsonify({"error": "Failed to parse storyboard response"}), 502

    return jsonify({"acts": acts})


@app.route("/api/expand-video-prompt", methods=["POST"])
@limiter.limit("10 per hour")
@require_auth
def expand_video_prompt():
    """Use Opus to expand 4 scene descriptions into a detailed Sora 2 video prompt."""
    data, err = validate_json("show_name", "act_title", "scenes")
    if err: return err
    show_name = data["show_name"]
    act_title = data["act_title"]
    scenes = data["scenes"]  # array of 4 short scene strings
    all_acts = data.get("all_acts", [])

    acts_context = "\n".join(
        f"Act {a.get('act_number', i+1)}: {a.get('title', '')}"
        for i, a in enumerate(all_acts)
    )

    scenes_text = "\n".join(f"Scene {i+1}: {s}" for i, s in enumerate(scenes))

    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=600.0)
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": (
                    f"You are creating a detailed video generation prompt for a 16-second drama clip.\n\n"
                    f"This is for Act '{act_title}' of a drama inspired by '{show_name}'.\n\n"
                    f"Full story context:\n{acts_context}\n\n"
                    f"The 4 scenes in this act:\n{scenes_text}\n\n"
                    f"Expand each scene into a detailed video prompt with:\n"
                    f"- Vivid description of the physical action, movement, and emotion\n"
                    f"- Camera movement and angles (dolly, pan, close-up, wide shot, etc.)\n"
                    f"- Dialogue written as 'the man says: \"...\"' or 'the woman says: \"...\"'\n"
                    f"- Write original dialogue that captures the emotional essence — "
                    f"do NOT use verbatim lines from any existing work\n\n"
                    f"CRITICAL RULES:\n"
                    f"- NO character names, NO show/movie titles, NO copyrighted references. "
                    f"Use only 'the man', 'the woman', 'the older woman', 'the friend', etc.\n"
                    f"- AVOID words that trigger content filters: no kiss, passionate, intimate, sexual, "
                    f"naked, nude, blood, kill, death, gun, stab, drug, alcohol, profanity. "
                    f"Use softer alternatives: 'lean close' instead of 'kiss', 'tender' instead of 'intimate', "
                    f"'wounded' instead of 'bloody', 'defeat' instead of 'kill', 'yearning' instead of 'desire'.\n\n"
                    f"Return the result as a single string with this exact format:\n"
                    f"Scene 1: [detailed prompt]\nScene 2: [detailed prompt]\n"
                    f"Scene 3: [detailed prompt]\nScene 4: [detailed prompt]\n\n"
                    f"Each scene prompt should be 2-3 sentences. Return ONLY the formatted scenes, "
                    f"no preamble or explanation."
                ),
            }
        ],
    )

    video_prompt = message.content[0].text.strip()
    log.info(f"[EXPAND-VIDEO-PROMPT] Response: {video_prompt[:500]}")

    return jsonify({"video_prompt": video_prompt})


@app.route("/api/upload-photo", methods=["POST"])
@require_auth
def upload_photo():
    """Cache the user's photo and return a token to reference it."""
    import uuid
    data, err = validate_json("photo")
    if err: return err
    photo = data["photo"]
    token = str(uuid.uuid4())
    _photo_cache[token] = photo
    log.info(f"[UPLOAD-PHOTO] Cached photo with token {token}, size {len(photo)}")
    return jsonify({"photo_token": token})


@app.route("/api/generate-image", methods=["POST"])
@limiter.limit("30 per hour")
@require_auth
def generate_image():
    """Submit image generation to fal.ai queue, return request_id for client polling."""
    data = request.get_json(silent=True) or {}
    # Support both direct photo and cached photo token
    user_photo = data.get("photo")
    if not user_photo:
        photo_token = data.get("photo_token")
        if photo_token and photo_token in _photo_cache:
            user_photo = _photo_cache[photo_token]
        else:
            return jsonify({"error": "No photo provided"}), 400
    prompt = data["prompt"]  # scene prompt from storyboard
    scene_number = data.get("scene_number", 1)
    gender = data.get("gender", "male")

    # Prepend instruction to use the reference photo's face for the lead character
    if gender == "female":
        role_desc = "female lead character"
        pronoun_obj = "her"
    else:
        role_desc = "male lead character"
        pronoun_obj = "his"
    full_prompt = (
        f"Use the face and appearance of the person in the reference image as the {role_desc}. "
        f"Keep {pronoun_obj} face, identity, and features exactly as shown in the reference photo. "
        f"Place them into this scene: {prompt}"
    )

    log.info(f"[GENERATE-IMAGE] Scene {scene_number}, prompt: {full_prompt[:150]}...")
    log.info(f"[GENERATE-IMAGE] Photo data URI length: {len(user_photo)}")

    payload = {
        "prompt": full_prompt,
        "image_urls": [user_photo],
        "aspect_ratio": "9:16",
        "num_images": 1,
    }

    try:
        submit_resp = requests.post(
            "https://queue.fal.run/fal-ai/nano-banana-2/edit",
            headers={
                "Authorization": f"Key {FAL_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        log.info(f"[GENERATE-IMAGE] Scene {scene_number} submit: {submit_resp.status_code} {submit_resp.text[:500]}")

        if submit_resp.status_code != 200:
            return jsonify({"error": f"fal.ai submit error: {submit_resp.text[:500]}"}), submit_resp.status_code

        return jsonify(submit_resp.json())

    except Exception as e:
        log.error(f"[GENERATE-IMAGE] Scene {scene_number} exception: {e}")
        log.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/api/image-status/<request_id>")
@limiter.limit("120 per minute")
def image_status(request_id):
    response = requests.get(
        f"https://queue.fal.run/fal-ai/nano-banana-2/requests/{request_id}/status",
        headers={"Authorization": f"Key {FAL_KEY}"},
        timeout=30,
    )
    log.info(f"[IMAGE-STATUS] {request_id}: {response.text[:300]}")
    try:
        return jsonify(response.json())
    except Exception:
        return jsonify({"status": "IN_PROGRESS"})


@app.route("/api/image-result/<request_id>")
@limiter.limit("120 per minute")
def image_result(request_id):
    response = requests.get(
        f"https://queue.fal.run/fal-ai/nano-banana-2/requests/{request_id}",
        headers={"Authorization": f"Key {FAL_KEY}"},
        timeout=30,
    )
    log.info(f"[IMAGE-RESULT] {request_id}: {response.status_code} {response.text[:500]}")
    try:
        return jsonify(response.json())
    except Exception:
        return jsonify({"error": "Invalid response", "raw": response.text[:200]}), 502


@app.route("/api/generate-scene-prompt", methods=["POST"])
@require_auth
def generate_scene_prompt():
    data, err = validate_json("show_name")
    if err: return err
    show_name = data["show_name"]

    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=600.0)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Describe the iconic opening/first scene of '{show_name}' in 2-3 sentences "
                    f"as a video generation prompt. Focus on: visual action, mood, camera movement, "
                    f"and cinematic style. Make it vivid and specific for generating a short 5-second "
                    f"video clip. The scene should feature the protagonist in a dramatic moment. "
                    f"Do not include any preamble - just output the scene description directly."
                ),
            }
        ],
    )

    return jsonify({"prompt": message.content[0].text})


# ---------------------------------------------------------------------------
# Sora 2 prompt sanitiser — replace words likely to trigger content filters
# ---------------------------------------------------------------------------
_SORA_REPLACEMENTS = [
    # Romantic / sexual
    (r'\bkiss(?:es|ed|ing)?\b', 'lean close'),
    (r'\bmaking out\b', 'embracing tenderly'),
    (r'\bpassionate(?:ly)?\b', 'intense'),
    (r'\bseduc(?:e|es|ed|ing|tion|tive)\b', 'captivat\\1' if False else 'alluring'),
    (r'\bseduc\w*', 'alluring'),
    (r'\blust(?:ful|ing)?\b', 'longing'),
    (r'\bintimate(?:ly)?\b', 'tender'),
    (r'\bintimacy\b', 'closeness'),
    (r'\bsexual(?:ly)?\b', 'romantic'),
    (r'\bsex\b', 'romance'),
    (r'\bnaked\b', 'bare-shouldered'),
    (r'\bnude\b', 'bare-shouldered'),
    (r'\bundress(?:es|ed|ing)?\b', 'loosening clothes'),
    (r'\bstrip(?:s|ped|ping)?\b(?!\s+(?:of|away|down))', 'disrobe'),
    (r'\blingerie\b', 'elegant attire'),
    (r'\bbra\b', 'top'),
    (r'\bcleavage\b', 'neckline'),
    (r'\bcaress(?:es|ed|ing)?\b', 'gently touch'),
    (r'\bmoan(?:s|ed|ing)?\b', 'sigh'),
    (r'\bgroan(?:s|ed|ing)?\b', 'exhale deeply'),
    (r'\berotica?\b', 'romantic'),
    (r'\bdesire\b', 'yearning'),
    (r'\bsensual(?:ly)?\b', 'gentle'),
    (r'\bbed\s*room\s*scene\b', 'private moment'),
    (r'\bbed\s*scene\b', 'private moment'),
    (r'\bmake\s+love\b', 'share a tender moment'),
    # Violence / weapons
    (r'\bkill(?:s|ed|ing)?\b', 'defeat'),
    (r'\bmurder(?:s|ed|ing)?\b', 'eliminate'),
    (r'\bstab(?:s|bed|bing)?\b', 'strike'),
    (r'\bblood(?:y|ied|iest)?\b', 'red-stained'),
    (r'\bbleed(?:s|ing)?\b', 'wounded'),
    (r'\bgun(?:s|fire|shot)?\b', 'weapon'),
    (r'\brifle(?:s)?\b', 'weapon'),
    (r'\bpistol(?:s)?\b', 'weapon'),
    (r'\bshotgun(?:s)?\b', 'weapon'),
    (r'\bbullet(?:s)?\b', 'projectile'),
    (r'\bshoot(?:s|ing)?\b(?!\s+(?:a look|glance))', 'fire'),
    (r'\bshot\b(?!\s+(?:of|glass))', 'blast'),
    (r'\bknife\b', 'blade'),
    (r'\bsword(?:s)?\b', 'blade'),
    (r'\bexplosion(?:s)?\b', 'burst of light'),
    (r'\bexplod(?:e|es|ed|ing)\b', 'burst apart'),
    (r'\bbomb(?:s|ed|ing)?\b', 'blast'),
    (r'\bsuicid\w*\b', 'sacrifice'),
    (r'\bdeath\b', 'loss'),
    (r'\bdie(?:s|d)?\b', 'fall'),
    (r'\bdying\b', 'fading'),
    (r'\bcorpse(?:s)?\b', 'fallen figure'),
    (r'\bdead\b', 'fallen'),
    (r'\btortur(?:e|es|ed|ing)\b', 'suffering'),
    (r'\bstrangle(?:s|d)?\b', 'restrain'),
    (r'\bchoke(?:s|d|ing)?\b', 'gasp'),
    (r'\bpoison(?:s|ed|ing)?\b', 'taint'),
    # Substances
    (r'\bdrunk(?:en)?\b', 'tipsy'),
    (r'\balcohol\b', 'drink'),
    (r'\bdrug(?:s|ged)?\b', 'substance'),
    (r'\bcocaine\b', 'powder'),
    (r'\bheroin\b', 'substance'),
    (r'\bsmoking\b', 'exhaling'),
    (r'\bcigarette(?:s)?\b', 'thin stick'),
    # Profanity (catch common ones)
    (r'\bf+u+c+k\w*\b', ''),
    (r'\bsh[i!]+t\w*\b', ''),
    (r'\bass(?:hole)?\b', ''),
    (r'\bbitch\w*\b', ''),
    (r'\bdamn(?:ed)?\b', ''),
    (r'\bhell\b(?!\s*o)', ''),
    # IP / copyright safety net (in case Claude leaks names)
    (r'\bfifty\s+shades\b', 'the story'),
    (r'\btwilight\b', 'the story'),
    (r'\bbridgerton\b', 'the story'),
    (r'\bgrey\s*(?:\'s)?\s*anatomy\b', 'the story'),
    (r'\bchristian\s+grey\b', 'the man'),
    (r'\banastasia\s+steele?\b', 'the woman'),
    (r'\bedward\s+cullen\b', 'the man'),
    (r'\bbella\s+swan\b', 'the woman'),
    (r'\bjacob\s+black\b', 'the friend'),
    (r'\bnick\s+young\b', 'the man'),
    (r'\brachel\s+chu\b', 'the woman'),
]

# Pre-compile patterns for performance
_SORA_COMPILED = [(re.compile(pat, re.IGNORECASE), repl) for pat, repl in _SORA_REPLACEMENTS]


def sanitize_sora_prompt(prompt: str) -> str:
    """Filter and replace sensitive words that might trigger Sora 2 content filters."""
    sanitized = prompt
    for pattern, replacement in _SORA_COMPILED:
        sanitized = pattern.sub(replacement, sanitized)
    # Collapse multiple spaces left by removals
    sanitized = re.sub(r'  +', ' ', sanitized).strip()
    # Remove empty quotes left behind
    sanitized = re.sub(r'says:\s*""', 'says: "..."', sanitized)
    return sanitized


@app.route("/api/generate-video", methods=["POST"])
@limiter.limit("10 per hour")
@require_auth
def generate_video():
    data = request.get_json(silent=True) or {}
    image_url = data["image_url"]
    prompt = data.get("prompt", "")

    log.info(f"[GENERATE-VIDEO] Received prompt ({len(prompt)} chars): '{prompt[:300]}'")
    log.info(f"[GENERATE-VIDEO] Image URL: {image_url[:100] if image_url else 'NONE'}...")

    if not prompt:
        log.warning("[GENERATE-VIDEO] Empty prompt received!")

    # Sanitize prompt to avoid triggering Sora 2 content filters
    original_prompt = prompt
    prompt = sanitize_sora_prompt(prompt)
    if prompt != original_prompt:
        log.info(f"[GENERATE-VIDEO] Sanitized prompt ({len(prompt)} chars): '{prompt[:300]}'")

    # Truncate prompt to 4900 chars to stay within Sora 2's 5000 char limit
    if len(prompt) > 4900:
        log.warning(f"[GENERATE-VIDEO] Prompt too long ({len(prompt)} chars), truncating to 4900")
        prompt = prompt[:4900]

    payload = {
        "prompt": prompt,
        "image_url": image_url,
        "duration": 16,
        "aspect_ratio": "9:16",
    }

    log.info(f"[GENERATE-VIDEO] Full payload JSON: {json.dumps(payload)[:600]}")

    response = requests.post(
        "https://queue.fal.run/fal-ai/sora-2/image-to-video",
        headers={
            "Authorization": f"Key {FAL_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    log.info(f"[GENERATE-VIDEO] Queue submission response ({response.status_code}): {response.text[:500]}")

    if response.status_code != 200:
        return jsonify({"error": response.text}), response.status_code

    return jsonify(response.json())


@app.route("/api/video-status/<request_id>")
@limiter.limit("120 per minute")
def video_status(request_id):
    response = requests.get(
        f"https://queue.fal.run/fal-ai/sora-2/requests/{request_id}/status",
        headers={"Authorization": f"Key {FAL_KEY}"},
    )
    log.info(f"[VIDEO-STATUS] raw response ({response.status_code}): {response.text[:300]}")
    try:
        return jsonify(response.json())
    except Exception:
        return jsonify({"status": "IN_PROGRESS", "raw": response.text[:200]})


@app.route("/api/video-result/<request_id>")
@limiter.limit("120 per minute")
def video_result(request_id):
    response = requests.get(
        f"https://queue.fal.run/fal-ai/sora-2/requests/{request_id}",
        headers={"Authorization": f"Key {FAL_KEY}"},
    )
    log.info(f"[VIDEO-RESULT] raw response ({response.status_code}): {response.text[:500]}")
    try:
        return jsonify(response.json())
    except Exception:
        return jsonify({"error": "Invalid response", "raw": response.text[:200]}), 502


@app.route("/api/merge-clips", methods=["POST"])
@require_auth
def merge_clips():
    """Download clip videos and merge them into a single MP4 using ffmpeg."""
    data = request.get_json(silent=True) or {}
    clip_urls = data.get("clip_urls", [])

    if len(clip_urls) < 2:
        return jsonify({"error": "Need at least 2 clips to merge"}), 400

    tmpdir = tempfile.mkdtemp()
    try:
        # Download all clips
        clip_files = []
        for i, url in enumerate(clip_urls):
            clip_path = os.path.join(tmpdir, f"clip_{i}.mp4")
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                raise Exception(f"Failed to download clip {i + 1}")
            with open(clip_path, "wb") as f:
                f.write(resp.content)
            clip_files.append(clip_path)

        # Create ffmpeg concat file
        concat_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_path, "w") as f:
            for cp in clip_files:
                f.write(f"file '{cp}'\n")

        # Merge with ffmpeg
        output_path = os.path.join(tmpdir, "merged.mp4")
        result = subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_path,
             "-c", "copy", output_path],
            capture_output=True, text=True, timeout=300,
        )

        if result.returncode != 0:
            log.error(f"[MERGE] ffmpeg stderr: {result.stderr}")
            raise Exception("Failed to merge clips with ffmpeg")

        with open(output_path, "rb") as f:
            merged_data = f.read()

        response = make_response(merged_data)
        response.headers["Content-Type"] = "video/mp4"
        response.headers["Content-Disposition"] = "attachment; filename=SoraShorts-merged.mp4"
        return response

    except FileNotFoundError:
        log.error("[MERGE] ffmpeg not found on system")
        return jsonify({"error": "ffmpeg is not installed on the server"}), 500
    except Exception as e:
        log.error(f"[MERGE] Error: {e}")
        log.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/api/user/photos", methods=["GET"])
@require_auth
def get_user_photos():
    """Get user's saved photos."""
    result = supabase.table("user_photos").select("*").eq("user_id", g.user_id).order("created_at", desc=True).execute()
    photos = []
    for row in (result.data or []):
        # Generate signed URL for each photo
        signed = supabase.storage.from_("user-photos").create_signed_url(row["storage_path"], 3600)
        photos.append({
            "id": row["id"],
            "url": signed.get("signedURL") or signed.get("signedUrl", ""),
            "created_at": row["created_at"],
        })
    return jsonify({"photos": photos})


@app.route("/api/user/upload-photo", methods=["POST"])
@require_auth
@limiter.limit("10 per hour")
def upload_user_photo():
    """Upload photo to Supabase Storage and save record."""
    import base64
    data, err = validate_json("photo")
    if err: return err

    photo = data["photo"]
    if not photo.startswith("data:image/"):
        return jsonify({"error": "Invalid photo format"}), 400

    # Parse data URI
    header, b64_data = photo.split(",", 1)
    media_type = header.split(":")[1].split(";")[0]
    ext = media_type.split("/")[1]
    if ext == "jpeg": ext = "jpg"

    photo_bytes = base64.b64decode(b64_data)

    # Check size (5MB max)
    if len(photo_bytes) > 5 * 1024 * 1024:
        return jsonify({"error": "Photo too large (max 5MB)"}), 400

    import uuid
    filename = f"{g.user_id}/{uuid.uuid4()}.{ext}"

    # Upload to Supabase Storage
    supabase.storage.from_("user-photos").upload(
        filename,
        photo_bytes,
        {"content-type": media_type}
    )

    # Save record
    supabase.table("user_photos").insert({
        "user_id": g.user_id,
        "storage_path": filename,
    }).execute()

    # Also cache in memory for immediate use during this session
    token = str(uuid.uuid4())
    _photo_cache[token] = photo

    # Generate signed URL
    signed = supabase.storage.from_("user-photos").create_signed_url(filename, 3600)

    return jsonify({
        "photo_token": token,
        "photo_url": signed.get("signedURL") or signed.get("signedUrl", ""),
        "storage_path": filename,
    })


@app.route("/api/user/projects", methods=["GET"])
@require_auth
def get_user_projects():
    """Get user's project history."""
    result = supabase.table("projects").select("id, show_name, user_name, gender, created_at").eq("user_id", g.user_id).order("created_at", desc=True).limit(20).execute()

    projects = []
    for row in (result.data or []):
        # Get first image asset for thumbnail
        assets = supabase.table("project_assets").select("storage_path").eq("project_id", row["id"]).eq("asset_type", "image").eq("act_number", 1).limit(1).execute()
        thumbnail_url = ""
        if assets.data:
            signed = supabase.storage.from_("project-assets").create_signed_url(assets.data[0]["storage_path"], 3600)
            thumbnail_url = signed.get("signedURL") or signed.get("signedUrl", "")

        projects.append({
            "id": row["id"],
            "show_name": row["show_name"],
            "user_name": row["user_name"],
            "created_at": row["created_at"],
            "thumbnail_url": thumbnail_url,
        })

    return jsonify({"projects": projects})


@app.route("/api/user/projects/<project_id>", methods=["GET"])
@require_auth
def get_user_project(project_id):
    """Get a single project with all assets."""
    result = supabase.table("projects").select("*").eq("id", project_id).eq("user_id", g.user_id).limit(1).execute()
    if not result.data:
        return jsonify({"error": "Project not found"}), 404

    project = result.data[0]

    # Get all assets
    assets_result = supabase.table("project_assets").select("*").eq("project_id", project_id).order("act_number").execute()

    assets = []
    for asset in (assets_result.data or []):
        signed = supabase.storage.from_("project-assets").create_signed_url(asset["storage_path"], 3600)
        assets.append({
            "id": asset["id"],
            "act_number": asset["act_number"],
            "asset_type": asset["asset_type"],
            "url": signed.get("signedURL") or signed.get("signedUrl", ""),
            "created_at": asset["created_at"],
        })

    return jsonify({
        "project": {
            "id": project["id"],
            "show_name": project["show_name"],
            "user_name": project["user_name"],
            "gender": project["gender"],
            "storyboard": project.get("storyboard_json"),
            "created_at": project["created_at"],
        },
        "assets": assets,
    })


@app.route("/api/save-project", methods=["POST"])
@require_auth
def save_project():
    """Save a completed project with its assets."""
    import base64
    data, err = validate_json("show_name", "user_name", "gender", "storyboard", "assets")
    if err: return err

    # Create project record
    project_result = supabase.table("projects").insert({
        "user_id": g.user_id,
        "show_name": data["show_name"],
        "user_name": data["user_name"],
        "gender": data["gender"],
        "storyboard_json": data["storyboard"],
    }).execute()

    project_id = project_result.data[0]["id"]

    # Save each asset - download from URL and upload to Storage
    saved_assets = []
    for asset in data["assets"]:
        act_number = asset["act_number"]
        asset_type = asset["asset_type"]  # "image" or "video"
        url = asset["url"]

        try:
            # Download from fal.ai URL
            resp = requests.get(url, timeout=120)
            if resp.status_code != 200:
                continue

            ext = "jpg" if asset_type == "image" else "mp4"
            import uuid
            storage_path = f"{g.user_id}/{project_id}/act{act_number}_{asset_type}.{ext}"

            content_type = "image/jpeg" if asset_type == "image" else "video/mp4"
            supabase.storage.from_("project-assets").upload(
                storage_path,
                resp.content,
                {"content-type": content_type}
            )

            supabase.table("project_assets").insert({
                "project_id": project_id,
                "act_number": act_number,
                "asset_type": asset_type,
                "storage_path": storage_path,
                "original_url": url,
            }).execute()

            saved_assets.append({"act_number": act_number, "asset_type": asset_type, "status": "saved"})
        except Exception as e:
            log.error(f"[SAVE-PROJECT] Failed to save asset act {act_number} {asset_type}: {e}")
            saved_assets.append({"act_number": act_number, "asset_type": asset_type, "status": "failed"})

    return jsonify({"project_id": project_id, "assets": saved_assets})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(debug=True, host="0.0.0.0", port=port, threaded=True)
