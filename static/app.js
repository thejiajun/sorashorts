// ===== STATE =====
let userPhotoDataURI = null;
let detectedGender = null;
let selectedShow = null;
let generatedImages = []; // array of image URLs for video generation
let scenePrompts = []; // array of scene prompt texts for video generation
let videoURL = null;
let generatedClips = []; // array of video URLs per clip
let currentClipIndex = 0; // which clip we're generating
let actData = []; // [{act_number, title, prompt, scenes, imageURL}, ...]
let photoToken = null; // cached for image gen calls
let userName = null; // user's name for storyboard
let savedPhotoUrl = null; // URL of a saved photo selected by the user

// ===== SUPABASE AUTH =====
let supabaseClient = null;
let currentUser = null;
let pendingAuthCallback = null;

async function initSupabase() {
    try {
        const resp = await fetch('/api/config');
        const config = await resp.json();
        if (!config.supabase_url || !config.supabase_anon_key) {
            console.warn('Supabase config not available');
            return;
        }
        supabaseClient = supabase.createClient(config.supabase_url, config.supabase_anon_key);

        // Check for existing session
        const { data: { session } } = await supabaseClient.auth.getSession();
        if (session) {
            currentUser = session.user;
            onAuthStateChanged(session.user);
        }

        // Listen for auth changes
        supabaseClient.auth.onAuthStateChange((event, session) => {
            currentUser = session?.user || null;
            onAuthStateChanged(currentUser);
        });
    } catch (err) {
        console.error('Failed to init Supabase:', err);
    }

    // Show auth button after init
    document.getElementById('auth-btn').style.display = '';
}

async function getAuthToken() {
    if (!supabaseClient) return null;
    const { data: { session } } = await supabaseClient.auth.getSession();
    return session?.access_token || null;
}

async function authFetch(url, options = {}) {
    const token = await getAuthToken();
    const headers = { ...options.headers };
    // Only set Content-Type to JSON if not already set and no FormData body
    if (!headers["Content-Type"] && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
    }
    if (token) {
        headers["Authorization"] = `Bearer ${token}`;
    }
    return fetch(url, { ...options, headers });
}

async function requireAuthThen(callback) {
    if (currentUser) {
        return callback();
    }
    // Show login modal, store callback for after login
    pendingAuthCallback = callback;
    document.getElementById('login-overlay').style.display = 'flex';
}

function onAuthStateChanged(user) {
    const authBtn = document.getElementById('auth-btn');
    const userMenu = document.getElementById('user-menu');

    if (user) {
        authBtn.style.display = 'none';
        userMenu.style.display = 'flex';
        document.getElementById('user-avatar').src = user.user_metadata?.avatar_url || '';
        document.getElementById('user-name-display').textContent = user.user_metadata?.full_name || user.email || '';

        // Load saved photos
        loadSavedPhotos();

        // If there was a pending action after login, execute it
        if (pendingAuthCallback) {
            const cb = pendingAuthCallback;
            pendingAuthCallback = null;
            document.getElementById('login-overlay').style.display = 'none';
            cb();
        }
    } else {
        authBtn.style.display = '';
        userMenu.style.display = 'none';
        document.getElementById('saved-photos-section').style.display = 'none';
    }
}

async function loadSavedPhotos() {
    if (!currentUser) return;

    try {
        const resp = await authFetch('/api/user/photos');
        if (!resp.ok) return;
        const data = await resp.json();

        const section = document.getElementById('saved-photos-section');
        const grid = document.getElementById('saved-photos-grid');

        if (!data.photos || data.photos.length === 0) {
            section.style.display = 'none';
            return;
        }

        section.style.display = '';
        grid.innerHTML = '';

        data.photos.forEach(photo => {
            const item = document.createElement('div');
            item.className = 'saved-photo-item';
            const img = document.createElement('img');
            img.src = photo.url;
            img.alt = 'Saved photo';
            img.addEventListener('click', async () => {
                // Download saved photo and convert to data URI for compatibility
                try {
                    const resp = await fetch(photo.url);
                    const blob = await resp.blob();
                    const reader = new FileReader();
                    reader.onload = async (e) => {
                        userPhotoDataURI = await compressImage(e.target.result, 800, 0.8);
                        savedPhotoUrl = null;
                        els.photoPreview.src = userPhotoDataURI;
                        els.uploadArea.style.display = 'none';
                        els.previewContainer.style.display = 'block';
                    };
                    reader.readAsDataURL(blob);
                } catch (err) {
                    console.error('Failed to load saved photo:', err);
                    showError('Failed to load saved photo');
                }
            });
            item.appendChild(img);
            grid.appendChild(item);
        });
    } catch (err) {
        console.error('Failed to load saved photos:', err);
    }
}

async function saveProject() {
    if (!currentUser) return;

    try {
        const assets = [];
        for (let i = 0; i < actData.length; i++) {
            if (actData[i].imageURL) {
                assets.push({ act_number: i + 1, asset_type: 'image', url: actData[i].imageURL });
            }
            if (generatedClips[i]) {
                assets.push({ act_number: i + 1, asset_type: 'video', url: generatedClips[i] });
            }
        }

        const resp = await authFetch('/api/save-project', {
            method: 'POST',
            body: JSON.stringify({
                show_name: selectedShow,
                user_name: userName,
                gender: detectedGender,
                storyboard: actData,
                assets: assets,
            }),
        });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            console.error('Failed to save project:', errData.error || resp.status);
            return;
        }
        console.log('Project saved successfully');
    } catch (err) {
        console.error('Failed to save project:', err);
    }
}

// ===== DOM REFS =====
const screens = {
    upload: document.getElementById("upload-screen"),
    select: document.getElementById("select-screen"),
    generate: document.getElementById("generate-screen"),
    result: document.getElementById("result-screen"),
};

const els = {
    fileInput: document.getElementById("file-input"),
    uploadArea: document.getElementById("upload-area"),
    uploadBtn: document.getElementById("upload-btn"),
    cameraBtn: document.getElementById("camera-btn"),
    cameraContainer: document.getElementById("camera-container"),
    cameraVideo: document.getElementById("camera-video"),
    cameraCaptureBtn: document.getElementById("camera-capture-btn"),
    cameraCancelBtn: document.getElementById("camera-cancel-btn"),
    previewContainer: document.getElementById("preview-container"),
    photoPreview: document.getElementById("photo-preview"),
    retakeBtn: document.getElementById("retake-btn"),
    continueBtn: document.getElementById("continue-btn"),
    userNameInput: document.getElementById("user-name-input"),
    // navPhoto removed - replaced by global auth bar
    customShowInput: document.getElementById("custom-show"),
    customShowBtn: document.getElementById("custom-show-btn"),
    storyboard: document.getElementById("storyboard"),
    storyboardHeading: document.getElementById("storyboard-heading"),
    generateStatus: document.getElementById("generate-status"),
    statusText: document.getElementById("status-text"),
    statusDetail: document.getElementById("status-detail"),
    resultVideo: document.getElementById("result-video"),
    saveBtn: document.getElementById("save-btn"),
    tryagainBtn: document.getElementById("tryagain-btn"),
    lightboxOverlay: document.getElementById("lightbox-overlay"),
    lightboxImage: document.getElementById("lightbox-image"),
    lightboxCaption: document.getElementById("lightbox-caption"),
    lightboxClose: document.getElementById("lightbox-close"),
    videoLightboxOverlay: document.getElementById("video-lightbox-overlay"),
    videoLightboxPlayer: document.getElementById("video-lightbox-player"),
    videoLightboxClose: document.getElementById("video-lightbox-close"),
    errorOverlay: document.getElementById("error-overlay"),
    errorMessage: document.getElementById("error-message"),
    errorCloseBtn: document.getElementById("error-close-btn"),
    clipsTimeline: document.getElementById("clips-timeline"),
    clipStatus: document.getElementById("clip-status"),
    clipStatusText: document.getElementById("clip-status-text"),
    clipStatusDetail: document.getElementById("clip-status-detail"),
    clipActions: document.getElementById("clip-actions"),
    regenerateClipBtn: document.getElementById("regenerate-clip-btn"),
    nextClipBtn: document.getElementById("next-clip-btn"),
    finalActions: document.getElementById("final-actions"),
    resultHeading: document.getElementById("result-heading"),
    mergeClipsBtn: document.getElementById("merge-clips-btn"),
    mergeAllBtn: document.getElementById("merge-all-btn"),
};

// ===== SCREEN MANAGEMENT =====
function showScreen(name) {
    Object.values(screens).forEach((s) => s.classList.remove("active"));
    screens[name].classList.add("active");
    window.scrollTo(0, 0);
}

// ===== ERROR HANDLING =====
function showError(message) {
    els.errorMessage.textContent = message;
    els.errorOverlay.style.display = "flex";
}

els.errorCloseBtn.addEventListener("click", () => {
    els.errorOverlay.style.display = "none";
});

// ===== PHOTO UPLOAD =====
function compressImage(dataURI, maxSize, quality) {
    return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement("canvas");
            let w = img.width;
            let h = img.height;
            if (w > maxSize || h > maxSize) {
                if (w > h) {
                    h = Math.round((h * maxSize) / w);
                    w = maxSize;
                } else {
                    w = Math.round((w * maxSize) / h);
                    h = maxSize;
                }
            }
            canvas.width = w;
            canvas.height = h;
            canvas.getContext("2d").drawImage(img, 0, 0, w, h);
            resolve(canvas.toDataURL("image/jpeg", quality));
        };
        img.src = dataURI;
    });
}

function handleFile(file) {
    if (!file || !file.type.startsWith("image/")) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
        // Compress to max 800px, 80% JPEG quality to keep payload small
        userPhotoDataURI = await compressImage(e.target.result, 800, 0.8);
        els.photoPreview.src = userPhotoDataURI;
        els.uploadArea.style.display = "none";
        els.previewContainer.style.display = "block";
    };
    reader.readAsDataURL(file);
}

els.uploadBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    els.fileInput.click();
});

// ===== LIVE CAMERA =====
let cameraStream = null;

function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach((t) => t.stop());
        cameraStream = null;
    }
    els.cameraVideo.srcObject = null;
}

els.cameraBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
        cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: false,
        });
        els.cameraVideo.srcObject = cameraStream;
        els.uploadArea.style.display = "none";
        els.cameraContainer.style.display = "block";
    } catch {
        showError("Could not access camera. Please allow camera permissions or upload a photo instead.");
    }
});

els.cameraCaptureBtn.addEventListener("click", async () => {
    const video = els.cameraVideo;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    // Mirror the capture to match the viewfinder
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0);
    stopCamera();
    els.cameraContainer.style.display = "none";

    const dataURI = canvas.toDataURL("image/jpeg", 0.9);
    userPhotoDataURI = await compressImage(dataURI, 800, 0.8);
    els.photoPreview.src = userPhotoDataURI;
    els.previewContainer.style.display = "block";
});

els.cameraCancelBtn.addEventListener("click", () => {
    stopCamera();
    els.cameraContainer.style.display = "none";
    els.uploadArea.style.display = "block";
});

els.uploadArea.addEventListener("click", () => {
    els.fileInput.click();
});

els.fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) handleFile(e.target.files[0]);
});

// Drag & drop
els.uploadArea.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.uploadArea.classList.add("drag-over");
});

els.uploadArea.addEventListener("dragleave", () => {
    els.uploadArea.classList.remove("drag-over");
});

els.uploadArea.addEventListener("drop", (e) => {
    e.preventDefault();
    els.uploadArea.classList.remove("drag-over");
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

// Retake / Continue
els.retakeBtn.addEventListener("click", () => {
    userPhotoDataURI = null;
    savedPhotoUrl = null;
    els.photoPreview.src = "";
    els.previewContainer.style.display = "none";
    els.uploadArea.style.display = "block";
    els.fileInput.value = "";
});

els.continueBtn.addEventListener("click", async () => {
    if (!userPhotoDataURI && !savedPhotoUrl) return;

    // Read user name
    const nameVal = els.userNameInput.value.trim();
    if (!nameVal) {
        els.userNameInput.focus();
        els.userNameInput.style.borderColor = "var(--red)";
        return;
    }
    userName = nameVal;

    // Detect gender from photo
    const origText = els.continueBtn.textContent;
    els.continueBtn.textContent = "Analyzing...";
    els.continueBtn.disabled = true;

    try {
        const resp = await fetch("/api/detect-gender", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ photo: userPhotoDataURI }),
        });
        if (resp.ok) {
            const data = await resp.json();
            detectedGender = data.gender;
            console.log("Detected gender:", detectedGender);
        } else {
            detectedGender = "male"; // fallback
        }
    } catch {
        detectedGender = "male"; // fallback
    }

    els.continueBtn.textContent = origText;
    els.continueBtn.disabled = false;
    showScreen("select");
});

// ===== SHOW SELECTION =====
const showsGrid = document.getElementById("shows-grid");

function createShowCard(show) {
    const card = document.createElement("div");
    card.className = "show-card";
    card.dataset.show = show.name;
    card.innerHTML = `
        <div class="show-poster">
            <img src="${show.poster_url}" alt="${show.name}">
            <div class="show-overlay">
                <div class="play-icon">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="white">
                        <polygon points="5,3 19,12 5,21"/>
                    </svg>
                </div>
            </div>
        </div>
        <p class="show-title">${show.name}</p>`;
    card.addEventListener("click", () => {
        selectedShow = show.name;
        requireAuthThen(() => startGeneration());
    });
    return card;
}

// Shuffle array in place
function shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
}

async function loadShows() {
    try {
        const resp = await fetch("/api/shows");
        const data = await resp.json();
        const shows = shuffle(data.shows || []);
        showsGrid.innerHTML = "";
        shows.forEach((show) => showsGrid.appendChild(createShowCard(show)));
    } catch (err) {
        console.error("Failed to load shows:", err);
    }
}

loadShows();

// Custom show input
els.customShowBtn.addEventListener("click", () => {
    const val = els.customShowInput.value.trim();
    if (!val) return;
    selectedShow = val;
    requireAuthThen(() => startGeneration());
});

els.customShowInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        const val = els.customShowInput.value.trim();
        if (!val) return;
        selectedShow = val;
        requireAuthThen(() => startGeneration());
    }
});

// ===== LIGHTBOX =====
function openLightbox(imageURL, caption) {
    els.lightboxImage.src = imageURL;
    els.lightboxCaption.textContent = caption || "";
    els.lightboxOverlay.style.display = "flex";
}

function closeLightbox() {
    els.lightboxOverlay.style.display = "none";
    els.lightboxImage.src = "";
}

els.lightboxClose.addEventListener("click", closeLightbox);
els.lightboxOverlay.addEventListener("click", (e) => {
    if (e.target === els.lightboxOverlay) closeLightbox();
});

// ===== VIDEO LIGHTBOX =====
function openVideoLightbox(videoUrl) {
    els.videoLightboxPlayer.src = videoUrl;
    els.videoLightboxPlayer.muted = false;
    els.videoLightboxPlayer.currentTime = 0;
    els.videoLightboxOverlay.style.display = "flex";
    els.videoLightboxPlayer.play();
}

function closeVideoLightbox() {
    els.videoLightboxPlayer.pause();
    els.videoLightboxPlayer.src = "";
    els.videoLightboxOverlay.style.display = "none";
}

els.videoLightboxClose.addEventListener("click", closeVideoLightbox);
els.videoLightboxOverlay.addEventListener("click", (e) => {
    if (e.target === els.videoLightboxOverlay) closeVideoLightbox();
});

// ===== HELPER: extract image URL from fal.ai response =====
function extractImageURL(imageData) {
    if (imageData.images && imageData.images.length > 0) {
        return imageData.images[0].url;
    } else if (imageData.image && imageData.image.url) {
        return imageData.image.url;
    } else if (imageData.output && imageData.output.images) {
        return imageData.output.images[0].url;
    }
    return null;
}

// ===== CIRCULAR PROGRESS RING =====
const RING_CIRCUMFERENCE = 2 * Math.PI * 26; // r=26

function createProgressRing(large) {
    const wrap = document.createElement("div");
    wrap.className = "progress-ring" + (large ? " large" : "");
    wrap.innerHTML = `
        <svg viewBox="0 0 60 60">
            <circle class="progress-ring-bg" cx="30" cy="30" r="26" />
            <circle class="progress-ring-fill" cx="30" cy="30" r="26"
                style="stroke-dasharray: ${RING_CIRCUMFERENCE}; stroke-dashoffset: ${RING_CIRCUMFERENCE}" />
        </svg>
        <span class="progress-ring-text">0%</span>`;
    const fill = wrap.querySelector(".progress-ring-fill");
    const text = wrap.querySelector(".progress-ring-text");

    wrap.setProgress = (pct) => {
        const clamped = Math.min(Math.max(pct, 0), 99);
        fill.style.strokeDashoffset = RING_CIRCUMFERENCE - (clamped / 100) * RING_CIRCUMFERENCE;
        text.textContent = `${Math.round(clamped)}%`;
    };
    wrap.complete = () => {
        fill.style.strokeDashoffset = 0;
        text.textContent = "100%";
        if (wrap._timer) { clearInterval(wrap._timer); wrap._timer = null; }
    };
    return wrap;
}

function startProgressTimer(ring, durationMs) {
    const start = Date.now();
    ring._timer = setInterval(() => {
        const pct = ((Date.now() - start) / durationMs) * 100;
        ring.setProgress(pct); // auto-capped at 99
        if (pct >= 99) { clearInterval(ring._timer); ring._timer = null; }
    }, 500);
}

function stopProgressTimer(ring) {
    if (ring && ring._timer) { clearInterval(ring._timer); ring._timer = null; }
}

// ===== STORYBOARD GENERATION =====
function resetStoryboard() {
    generatedImages = [];
    scenePrompts = [];
    actData = [];
    photoToken = null;
    els.storyboard.innerHTML = "";
}

function createActCard(act) {
    const actNum = act.act_number;
    const card = document.createElement("div");
    card.className = "act-card";
    card.dataset.act = actNum;

    // Header
    const header = document.createElement("div");
    header.className = "act-header";
    const numEl = document.createElement("span");
    numEl.className = "act-number";
    numEl.textContent = `ACT ${actNum}`;
    const titleEl = document.createElement("span");
    titleEl.className = "act-title";
    titleEl.textContent = act.title || "";
    header.appendChild(numEl);
    header.appendChild(titleEl);

    // Body
    const body = document.createElement("div");
    body.className = "act-body";

    // Thumbnail
    const thumbWrap = document.createElement("div");
    thumbWrap.className = "act-thumbnail-wrap";
    const placeholder = document.createElement("div");
    placeholder.className = "act-thumbnail-placeholder";
    const imgRing = createProgressRing(false);
    placeholder.appendChild(imgRing);
    startProgressTimer(imgRing, 60000); // 1 min expected
    const img = document.createElement("img");
    img.alt = `Act ${actNum}`;
    thumbWrap.appendChild(placeholder);
    thumbWrap.appendChild(img);

    // Scenes list
    const scenesEl = document.createElement("div");
    scenesEl.className = "act-scenes";
    if (act.scenes && act.scenes.length > 0) {
        act.scenes.forEach((scene, i) => {
            const line = document.createElement("div");
            line.className = "act-scene-line";
            // Strip any "Scene X:" prefix Claude may have included
            const cleanScene = scene.replace(/^Scene\s*\d+\s*:\s*/i, "");
            line.innerHTML = `<span class="scene-num">Scene ${i + 1}:</span> ${cleanScene}`;
            scenesEl.appendChild(line);
        });
    }

    // Generate Video button (hidden until image loads)
    const genBtn = document.createElement("button");
    genBtn.className = "act-generate-btn";
    genBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg> Generate Video`;
    genBtn.addEventListener("click", () => generateActVideo(actNum));

    body.appendChild(thumbWrap);
    body.appendChild(scenesEl);
    body.appendChild(genBtn);

    card.appendChild(header);
    card.appendChild(body);
    els.storyboard.appendChild(card);

    return card;
}

function showActImage(actNumber, imageURL) {
    const card = els.storyboard.querySelector(`.act-card[data-act="${actNumber}"]`);
    if (!card) return;

    const img = card.querySelector(".act-thumbnail-wrap img");
    const placeholder = card.querySelector(".act-thumbnail-placeholder");
    const genBtn = card.querySelector(".act-generate-btn");
    const ring = placeholder.querySelector(".progress-ring");

    img.src = imageURL;
    img.onload = () => {
        if (ring) { ring.complete(); }
        setTimeout(() => {
            placeholder.classList.add("hidden");
            img.classList.add("visible");
            genBtn.classList.add("visible");
        }, 300);
    };

    img.onclick = () => openLightbox(imageURL, actData.find(a => a.act_number === actNumber)?.prompt || "");
}

async function generateActVideo(actNumber) {
    const act = actData.find(a => a.act_number === actNumber);
    if (!act) return;

    const card = els.storyboard.querySelector(`.act-card[data-act="${actNumber}"]`);
    if (!card) return;

    const genBtn = card.querySelector(".act-generate-btn");
    genBtn.disabled = true;
    genBtn.innerHTML = `<div class="spinner-small" style="width:16px;height:16px;"></div> Expanding scenes...`;

    const thumbWrap = card.querySelector(".act-thumbnail-wrap");

    try {
        // Step 1: Call Opus to expand scene descriptions into detailed video prompt
        const expandResp = await authFetch("/api/expand-video-prompt", {
            method: "POST",
            body: JSON.stringify({
                show_name: selectedShow,
                act_title: act.title,
                scenes: act.scenes || [],
                all_acts: actData.map(a => ({ act_number: a.act_number, title: a.title })),
            }),
        });

        if (!expandResp.ok) {
            const err = await expandResp.json().catch(() => ({}));
            throw new Error(err.error || "Failed to expand video prompt");
        }

        const expandData = await expandResp.json();
        const videoPrompt = expandData.video_prompt;

        console.log(`[VIDEO] Act ${actNumber} expanded prompt (${videoPrompt ? videoPrompt.length : 0} chars):`, videoPrompt);

        if (!videoPrompt || videoPrompt.trim().length === 0) {
            console.error(`[VIDEO] Act ${actNumber}: expanded prompt is EMPTY!`);
            throw new Error("Video prompt expansion returned empty result");
        }

        // Step 2: Show progress ring on thumbnail, submit to Sora 2
        genBtn.innerHTML = `<div class="spinner-small" style="width:16px;height:16px;"></div> Generating video...`;

        const overlay = document.createElement("div");
        overlay.className = "subscene-generating";
        const vidRing = createProgressRing(true);
        overlay.appendChild(vidRing);
        startProgressTimer(vidRing, 300000); // 5 min expected
        thumbWrap.appendChild(overlay);

        const videoPayload = {
            image_url: act.imageURL,
            prompt: videoPrompt,
        };
        console.log(`[VIDEO] Act ${actNumber} sending to /api/generate-video:`, JSON.stringify(videoPayload).substring(0, 500));

        const resp = await authFetch("/api/generate-video", {
            method: "POST",
            body: JSON.stringify(videoPayload),
        });

        if (!resp.ok) {
            throw new Error("Video generation request rejected");
        }

        const submitData = await resp.json();

        let videoUrl = null;
        if (!submitData.request_id) {
            if (submitData.video && submitData.video.url) {
                videoUrl = submitData.video.url;
            }
        } else {
            videoUrl = await pollVideoForAct(submitData.request_id);
        }

        // Remove overlay
        stopProgressTimer(vidRing);
        const ov = thumbWrap.querySelector(".subscene-generating");
        if (ov) ov.remove();

        if (videoUrl) {
            // Replace image with video
            const img = thumbWrap.querySelector("img");
            if (img) img.style.display = "none";

            const video = document.createElement("video");
            video.src = videoUrl;
            video.autoplay = true;
            video.loop = true;
            video.muted = true;
            video.playsInline = true;
            video.style.cursor = "pointer";
            video.addEventListener("click", () => openVideoLightbox(videoUrl));
            thumbWrap.appendChild(video);

            genBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg> Done`;
            genBtn.disabled = true;
        } else {
            throw new Error("Video generation failed or was refused");
        }

    } catch (error) {
        console.error(`Generate act ${actNumber} video error:`, error);
        stopProgressTimer(vidRing);
        const ov = thumbWrap.querySelector(".subscene-generating");
        if (ov) ov.remove();
        genBtn.disabled = false;
        genBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg> Generate Video`;
        showError(error.message || "Failed to generate video");
    }
}

async function pollVideoForAct(requestId) {
    const maxAttempts = 180;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 3000));

        try {
            const statusResp = await fetch(`/api/video-status/${requestId}`);
            const statusData = await statusResp.json();

            if (statusData.status === "COMPLETED") {
                const resultResp = await fetch(`/api/video-result/${requestId}`);
                const resultData = await resultResp.json();
                if (resultData.video && resultData.video.url) {
                    return resultData.video.url;
                }
                return null;
            }

            if (statusData.status === "FAILED") {
                return null;
            }
        } catch (error) {
            // Network error — keep polling
        }
    }
    return null;
}

async function pollImageStatus(requestId, sceneNum) {
    const maxAttempts = 180;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));

        try {
            const statusResp = await fetch(`/api/image-status/${requestId}`);
            const statusData = await statusResp.json();
            const status = statusData.status;

            if (status === "COMPLETED") {
                const resultResp = await fetch(`/api/image-result/${requestId}`);
                const resultData = await resultResp.json();
                const imageURL = extractImageURL(resultData);
                if (!imageURL) {
                    console.log(`Scene ${sceneNum} result:`, JSON.stringify(resultData));
                    throw new Error(`No image returned for scene ${sceneNum}`);
                }
                return imageURL;
            }

            if (status === "FAILED") {
                throw new Error(`Image generation failed for scene ${sceneNum}`);
            }
        } catch (error) {
            if (error.message.includes("failed") || error.message.includes("No image")) {
                throw error;
            }
            // Network error — keep polling
        }
    }
    throw new Error(`Scene ${sceneNum} timed out`);
}

async function startGeneration() {
    showScreen("generate");
    resetStoryboard();
    els.generateStatus.style.display = "";
    els.statusText.textContent = "Writing your storyboard...";
    els.statusDetail.textContent =
        "AI is crafting a 5-act plot for " + selectedShow;

    try {
        // Step 1: Generate 5 act prompts with Claude
        const storyboardResponse = await authFetch("/api/generate-storyboard", {
            method: "POST",
            body: JSON.stringify({ show_name: selectedShow, gender: detectedGender, user_name: userName }),
        });

        if (!storyboardResponse.ok) {
            let errMsg = "Failed to generate storyboard";
            try {
                const err = await storyboardResponse.json();
                errMsg = err.error || errMsg;
            } catch {
                errMsg += ` (server returned ${storyboardResponse.status})`;
            }
            throw new Error(errMsg);
        }

        let storyboardData;
        try {
            storyboardData = await storyboardResponse.json();
        } catch {
            throw new Error("Invalid response from storyboard API");
        }
        const acts = storyboardData.acts;

        if (!acts || acts.length === 0) {
            throw new Error("No acts returned from storyboard generation");
        }

        // Build act cards in the DOM
        for (const act of acts) {
            createActCard(act);
        }

        // Step 2: Upload photo once, then submit ALL images in parallel
        let completedCount = 0;

        els.statusText.textContent = `Generating ${acts.length} act images...`;
        els.statusDetail.textContent = "Preparing image requests...";

        // Upload photo once (use saved photo URL or data URI)
        const photoPayload = savedPhotoUrl
            ? { photo_url: savedPhotoUrl }
            : { photo: userPhotoDataURI };

        if (currentUser && userPhotoDataURI) {
            // If logged in and has a new photo, save it to user's collection
            const uploadResp = await authFetch("/api/user/upload-photo", {
                method: "POST",
                body: JSON.stringify({ photo: userPhotoDataURI }),
            });
            if (uploadResp.ok) {
                const uploadData = await uploadResp.json();
                photoToken = uploadData.photo_token;
            } else {
                // Fallback to regular upload
                const fallbackResp = await authFetch("/api/upload-photo", {
                    method: "POST",
                    body: JSON.stringify(photoPayload),
                });
                if (!fallbackResp.ok) throw new Error("Failed to upload photo");
                const fallbackData = await fallbackResp.json();
                photoToken = fallbackData.photo_token;
            }
        } else {
            const uploadResp = await authFetch("/api/upload-photo", {
                method: "POST",
                body: JSON.stringify(photoPayload),
            });
            if (!uploadResp.ok) throw new Error("Failed to upload photo");
            const uploadData = await uploadResp.json();
            photoToken = uploadData.photo_token;
        }

        els.statusDetail.textContent = "Submitting image requests...";

        // Submit all acts to fal.ai queue simultaneously
        const submissions = await Promise.all(
            acts.map(async (act, i) => {
                const actNum = act.act_number || i + 1;
                const submitResponse = await authFetch("/api/generate-image", {
                    method: "POST",
                    body: JSON.stringify({
                        photo_token: photoToken,
                        prompt: act.prompt,
                        show_name: selectedShow,
                        scene_number: actNum,
                        gender: detectedGender,
                    }),
                });

                if (!submitResponse.ok) {
                    let errMsg = `Failed to submit act ${actNum}`;
                    try {
                        const text = await submitResponse.text();
                        try {
                            const err = JSON.parse(text);
                            errMsg = err.error || errMsg;
                        } catch {
                            errMsg += `: ${text.substring(0, 200)}`;
                        }
                    } catch {
                        errMsg += ` (server returned ${submitResponse.status})`;
                    }
                    throw new Error(errMsg);
                }

                let submitData;
                try {
                    submitData = await submitResponse.json();
                } catch {
                    throw new Error(`Invalid response submitting act ${actNum}`);
                }

                return { act, actNum, submitData };
            })
        );

        els.statusDetail.textContent = "All acts submitted, waiting for results...";

        // Pre-allocate actData
        actData = acts.map((act, i) => ({
            act_number: act.act_number || i + 1,
            title: act.title || "",
            prompt: act.prompt,
            scenes: act.scenes || [],
            imageURL: null,
        }));

        // Poll all acts in parallel, but reveal images sequentially
        const imageResults = new Array(acts.length);
        const resolvers = new Array(acts.length);
        const readyPromises = acts.map((_, i) => new Promise((resolve) => { resolvers[i] = resolve; }));

        const pollAll = Promise.all(
            submissions.map(async ({ act, actNum, submitData }, idx) => {
                const requestId = submitData.request_id;
                let imageURL;

                if (!requestId) {
                    imageURL = extractImageURL(submitData);
                    if (!imageURL) throw new Error(`No image returned for act ${actNum}`);
                } else {
                    imageURL = await pollImageStatus(requestId, actNum);
                }

                imageResults[idx] = { imageURL, act, actNum };
                actData[idx].imageURL = imageURL;
                resolvers[idx]();
            })
        );

        // Sequential display
        for (let i = 0; i < acts.length; i++) {
            await readyPromises[i];
            const { imageURL, act, actNum } = imageResults[i];
            showActImage(actNum, imageURL);
            completedCount++;
            const dots = ".".repeat((completedCount % 3) + 1);
            els.statusText.textContent = `${completedCount} of ${acts.length} acts ready${dots}`;
            els.statusDetail.textContent = act.prompt;
        }

        await pollAll;

        // All acts generated — hide status bar
        els.generateStatus.style.display = "none";
    } catch (error) {
        console.error("Generation error:", error);
        els.generateStatus.style.display = "none";
        showError(error.message || "Something went wrong during generation");
    }
}

// ===== VIDEO GENERATION (CLIP-BY-CLIP) =====
function resetClipsUI() {
    generatedClips = [];
    currentClipIndex = 0;
    els.clipsTimeline.innerHTML = "";
    els.clipActions.style.display = "none";
    els.finalActions.style.display = "none";
    els.clipStatus.style.display = "";
}

function createClipCard(index, isGenerating) {
    const card = document.createElement("div");
    card.className = `clip-card ${isGenerating ? "current" : "past"}`;
    card.dataset.clip = index;

    const wrapper = document.createElement("div");
    wrapper.className = "clip-video-wrapper";

    if (isGenerating) {
        const generating = document.createElement("div");
        generating.className = "clip-generating";
        generating.innerHTML = `<div class="spinner-small"></div><span class="clip-generating-text">Generating...</span>`;
        wrapper.appendChild(generating);
    }

    const label = document.createElement("span");
    label.className = "clip-label";
    label.textContent = `Clip ${index + 1}`;

    card.appendChild(wrapper);
    card.appendChild(label);
    els.clipsTimeline.appendChild(card);

    // Scroll to make the new card visible
    card.scrollIntoView({ behavior: "smooth", inline: "center" });

    return card;
}

function showClipVideo(index, videoUrl) {
    const card = els.clipsTimeline.querySelector(`.clip-card[data-clip="${index}"]`);
    if (!card) return;

    const wrapper = card.querySelector(".clip-video-wrapper");
    wrapper.innerHTML = "";

    const video = document.createElement("video");
    video.src = videoUrl;
    video.controls = true;
    video.playsinline = true;
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    wrapper.appendChild(video);
}

async function startClipGeneration() {
    showScreen("result");
    resetClipsUI();
    await generateClip(0);
}

async function generateClip(index) {
    currentClipIndex = index;
    const totalClips = generatedImages.length;

    els.resultHeading.textContent = `Generating Clip ${index + 1} of ${totalClips}`;
    els.clipActions.style.display = "none";
    els.finalActions.style.display = "none";
    els.clipStatus.style.display = "";
    els.clipStatusText.textContent = `Generating clip ${index + 1}...`;
    els.clipStatusDetail.textContent = scenePrompts[index] || "This may take a minute or two...";

    // Mark previous clips as past
    els.clipsTimeline.querySelectorAll(".clip-card").forEach((c) => c.classList.replace("current", "past"));

    // Create or replace the card for this clip
    let existingCard = els.clipsTimeline.querySelector(`.clip-card[data-clip="${index}"]`);
    if (existingCard) {
        existingCard.remove();
    }
    createClipCard(index, true);

    try {
        // Use the scene prompt for this clip
        const scenePrompt = scenePrompts[index] || `Scene ${index + 1}`;

        const videoResponse = await authFetch("/api/generate-video", {
            method: "POST",
            body: JSON.stringify({
                image_url: generatedImages[index],
                prompt: scenePrompt,
            }),
        });

        if (!videoResponse.ok) {
            const err = await videoResponse.json();
            throw new Error(err.error || `Failed to start clip ${index + 1}`);
        }

        const videoData = await videoResponse.json();
        const requestId = videoData.request_id;

        if (!requestId) {
            if (videoData.video && videoData.video.url) {
                onClipReady(index, videoData.video.url);
                return;
            }
            throw new Error("No request ID returned from video generation");
        }

        await pollClipStatus(requestId, index);
    } catch (error) {
        console.error(`Clip ${index + 1} error:`, error);
        els.clipStatus.style.display = "none";
        showError(error.message || `Failed to generate clip ${index + 1}`);
        // Show regenerate button even on error
        els.clipActions.style.display = "";
        els.nextClipBtn.style.display = index < totalClips - 1 ? "" : "none";
    }
}

async function pollClipStatus(requestId, clipIndex) {
    const maxAttempts = 120;
    let attempt = 0;

    while (attempt < maxAttempts) {
        attempt++;

        try {
            const statusResponse = await fetch(`/api/video-status/${requestId}`);
            const statusData = await statusResponse.json();
            const status = statusData.status;

            if (status === "COMPLETED") {
                const resultResponse = await fetch(`/api/video-result/${requestId}`);
                const resultData = await resultResponse.json();

                if (resultData.video && resultData.video.url) {
                    onClipReady(clipIndex, resultData.video.url);
                    return;
                }
                throw new Error("Video completed but no URL returned");
            }

            if (status === "FAILED") {
                throw new Error(`Clip ${clipIndex + 1} generation failed`);
            }

            // Keep showing the scene prompt during polling (no change needed)
        } catch (error) {
            if (
                error.message.includes("failed") ||
                error.message.includes("no URL")
            ) {
                throw error;
            }
        }

        await new Promise((resolve) => setTimeout(resolve, 3000));
    }

    throw new Error(`Clip ${clipIndex + 1} timed out`);
}

function onClipReady(index, videoUrl) {
    generatedClips[index] = videoUrl;
    showClipVideo(index, videoUrl);

    els.clipStatus.style.display = "none";

    const totalClips = generatedImages.length;
    const isLastClip = index >= totalClips - 1;

    // Show merge button when 2+ clips are ready
    const readyCount = generatedClips.filter(Boolean).length;

    if (isLastClip) {
        // Auto-save project
        saveProject();
        // All clips done
        els.resultHeading.textContent = "Your Drama Clips Are Ready";
        els.clipActions.style.display = "none";
        els.finalActions.style.display = "";
    } else {
        // Show regenerate + next buttons
        els.resultHeading.textContent = `Clip ${index + 1} of ${totalClips} Ready`;
        els.clipActions.style.display = "";
        els.nextClipBtn.style.display = "";
        // Show merge button in clip-actions if 2+ clips ready
        els.mergeClipsBtn.style.display = readyCount >= 2 ? "" : "none";
    }
}

// Regenerate current clip
els.regenerateClipBtn.addEventListener("click", () => {
    generateClip(currentClipIndex);
});

// Generate next clip
els.nextClipBtn.addEventListener("click", () => {
    // Mark current clip card as past
    const currentCard = els.clipsTimeline.querySelector(`.clip-card[data-clip="${currentClipIndex}"]`);
    if (currentCard) currentCard.classList.replace("current", "past");

    generateClip(currentClipIndex + 1);
});

// ===== RESULT ACTIONS =====
els.saveBtn.addEventListener("click", async () => {
    // Download each clip
    for (let i = 0; i < generatedClips.length; i++) {
        const url = generatedClips[i];
        if (!url) continue;
        try {
            const response = await fetch(url);
            const blob = await response.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = `SoraShorts-${selectedShow.replace(/\s+/g, "-")}-clip${i + 1}.mp4`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);
        } catch {
            window.open(url, "_blank");
        }
    }
});

els.tryagainBtn.addEventListener("click", () => {
    videoURL = null;
    generatedImages = [];
    scenePrompts = [];
    generatedClips = [];
    currentClipIndex = 0;
    actData = [];
    photoToken = null;
    detectedGender = null;
    selectedShow = null;
    userName = null;
    savedPhotoUrl = null;
    els.clipsTimeline.innerHTML = "";
    els.customShowInput.value = "";
    showScreen("select");
});

// ===== MERGE CLIPS =====
async function mergeClips(btn) {
    const clipUrls = generatedClips.filter(Boolean);
    if (clipUrls.length < 2) {
        showError("Need at least 2 clips to merge.");
        return;
    }

    const origText = btn.textContent;
    btn.textContent = "Merging...";
    btn.disabled = true;

    try {
        const resp = await authFetch("/api/merge-clips", {
            method: "POST",
            body: JSON.stringify({ clip_urls: clipUrls }),
        });

        if (!resp.ok) {
            let errMsg = "Failed to merge clips";
            try {
                const err = await resp.json();
                errMsg = err.error || errMsg;
            } catch {}
            throw new Error(errMsg);
        }

        const blob = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = `SoraShorts-${selectedShow.replace(/\s+/g, "-")}-merged.mp4`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
    } catch (error) {
        console.error("Merge error:", error);
        showError(error.message || "Failed to merge clips");
    } finally {
        btn.textContent = origText;
        btn.disabled = false;
    }
}

els.mergeClipsBtn.addEventListener("click", () => mergeClips(els.mergeClipsBtn));
els.mergeAllBtn.addEventListener("click", () => mergeClips(els.mergeAllBtn));

// ===== AUTH UI EVENT LISTENERS =====
document.getElementById('auth-btn').addEventListener('click', () => {
    document.getElementById('login-overlay').style.display = 'flex';
});

document.getElementById('google-login-btn').addEventListener('click', async () => {
    if (!supabaseClient) return;
    await supabaseClient.auth.signInWithOAuth({
        provider: 'google',
        options: {
            redirectTo: window.location.origin,
        }
    });
});

document.getElementById('login-close-btn').addEventListener('click', () => {
    document.getElementById('login-overlay').style.display = 'none';
    pendingAuthCallback = null;
});

document.getElementById('logout-btn').addEventListener('click', async () => {
    if (supabaseClient) {
        await supabaseClient.auth.signOut();
    }
    currentUser = null;
    onAuthStateChanged(null);
});

// ===== INIT SUPABASE =====
initSupabase();
