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
from flask import Flask, render_template, request, jsonify, make_response
from flask_cors import CORS
from anthropic import Anthropic
from supabase import create_client

load_dotenv()

app = Flask(__name__)
CORS(app)

# Force unbuffered logging to stderr
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
log = app.logger

FAL_KEY = os.environ.get("FAL_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

APP_VERSION = "2025-02-22-v3"

# In-memory cache for uploaded photos (keyed by a simple token)
_photo_cache = {}


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
    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "fal_key_set": bool(FAL_KEY),
        "anthropic_key_set": bool(ANTHROPIC_API_KEY),
        "supabase_set": bool(supabase),
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
def detect_gender():
    data = request.json
    photo = data["photo"]  # base64 data URI

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
def generate_storyboard():
    data = request.json
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
def expand_video_prompt():
    """Use Opus to expand 4 scene descriptions into a detailed Sora 2 video prompt."""
    data = request.json
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
def upload_photo():
    """Cache the user's photo and return a token to reference it."""
    import uuid
    data = request.json
    photo = data["photo"]
    token = str(uuid.uuid4())
    _photo_cache[token] = photo
    log.info(f"[UPLOAD-PHOTO] Cached photo with token {token}, size {len(photo)}")
    return jsonify({"photo_token": token})


@app.route("/api/generate-image", methods=["POST"])
def generate_image():
    """Submit image generation to fal.ai queue, return request_id for client polling."""
    data = request.json
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
def generate_scene_prompt():
    data = request.json
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
def generate_video():
    data = request.json
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
def merge_clips():
    """Download clip videos and merge them into a single MP4 using ffmpeg."""
    data = request.json
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(debug=True, host="0.0.0.0", port=port, threaded=True)
