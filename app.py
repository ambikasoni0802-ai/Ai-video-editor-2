"""
AI Video Editor - High Level Version
--------------------------------------
Naya upgrade:
- Groq (free LLM API) se instruction samajhna - ab tum KAISI BHI
  bhasha mein bol sakte ho, app samjhega aur sahi action lega
- Whisper (open-source) se auto-subtitles
- rembg (open-source) se background remove
- FFmpeg se bahut saare effects: blur, vintage, vignette, zoom,
  rotate, crop, watermark, fade, background music

Koi paid API nahi - Groq ka free tier use ho raha hai (bahut generous
free limits deta hai), baaki sab khud ke server par chalta hai.
"""

import gradio as gr
import subprocess
import os
import re
import uuid
import shutil
import json

WORK_DIR = "workdir"
os.makedirs(WORK_DIR, exist_ok=True)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


def run_ffmpeg(cmd):
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-1500:]}")
    return result


def get_duration(video_path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True
    )
    return float(probe.stdout.strip())


# ---------------------------------------------------------------
# LLM se instruction samajhna (Groq free API)
# ---------------------------------------------------------------

ACTIONS_SCHEMA = """
Tum ek video editing assistant ho. User KISI BHI bhasha mein bol sakta hai
- Hindi, English, Spanish, French, Arabic, Chinese, ya duniya ki koi bhi
bhasha - tumhe HAR bhasha samajhni hai aur usko neeche diye actions mein
se sabse sahi ek ya zyada actions mein convert karna hai. SIRF JSON
return karo, kuch aur nahi.

Available actions:
- trim: {"action": "trim", "from_start_seconds": number} ya {"action": "trim", "from_end_seconds": number}
- add_text: {"action": "add_text", "text": string}
- speed: {"action": "speed", "factor": number}  (2.0 = 2x fast, 0.5 = slow motion)
- mute: {"action": "mute"}
- to_gif: {"action": "to_gif"}
- extract_audio: {"action": "extract_audio"}
- grayscale: {"action": "grayscale"}
- remove_bg: {"action": "remove_bg"}
- subtitles: {"action": "subtitles"}
- blur: {"action": "blur", "strength": number}  (5-30 range)
- vintage: {"action": "vintage"}
- vignette: {"action": "vignette"}
- zoom: {"action": "zoom", "factor": number}  (1.2 = 20% zoom in)
- rotate: {"action": "rotate", "degrees": number}  (90, 180, 270)
- crop_square: {"action": "crop_square"}
- watermark_text: {"action": "watermark_text", "text": string}
- fade: {"action": "fade"}
- brightness: {"action": "brightness", "level": number}  (-1 to 1, negative=dark, positive=bright)
- stabilize: {"action": "stabilize"}  (shaky video ko smooth karna)
- greenscreen: {"action": "greenscreen"}  (green background hatana/transparent karna)
- face_blur: {"action": "face_blur"}  (chehra blur karna, privacy ke liye)
- remove_silence: {"action": "remove_silence"}  (khaali/silent parts video se hatana)
- color_grade: {"action": "color_grade", "style": string}  (style: "cinematic", "warm", "cool", "teal_orange")
- crossfade_transition: {"action": "crossfade_transition"}  (smooth transition, sirf do videos hon tab)
- sharpen: {"action": "sharpen"}  (video ko sharp/clear banana)
- old_film: {"action": "old_film"}  (purani film jaisa scratches/grain effect)
- reverse: {"action": "reverse"}  (video ulta chalana)
- split_screen: {"action": "split_screen"}  (do videos ko side-by-side dikhana, sirf tab jab dusri video upload ho)
- add_music: {"action": "add_music"}  (background music mix karna, sirf tab jab music file upload ho)
- text_to_speech: {"action": "text_to_speech", "text": string}  (voiceover banana - text ko awaz mein badalna)

User agar ek se zyada cheezein bole (jaise "cut karo aur text add karo"),
toh multiple actions ki JSON list return karo.

Format: {"actions": [ {...}, {...} ]}

Agar user ka instruction kisi bhi action se match nahi karta,
return: {"actions": [], "clarification": "kya karna hai clearly batao"}
"""


def understand_instruction_with_llm(instruction):
    """Groq API use karke instruction ko structured actions mein convert karta hai."""
    if not GROQ_API_KEY:
        return None  # LLM available nahi hai, fallback use hoga

    import urllib.request

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": ACTIONS_SCHEMA},
            {"role": "user", "content": instruction}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return parsed.get("actions", [])
    except Exception as e:
        print("LLM error:", e)
        return None


# ---------------------------------------------------------------
# Fallback: simple keyword matching (agar LLM available na ho)
# ---------------------------------------------------------------

def extract_number(text, default=None):
    match = re.search(r"(\d+(\.\d+)?)", text)
    if match:
        return float(match.group(1))
    return default


def fallback_parse(instruction):
    text = instruction.lower()
    actions = []

    if any(w in text for w in ["background hatao", "background remove", "bg hatao"]):
        actions.append({"action": "remove_bg"})
    elif any(w in text for w in ["subtitle", "caption add", "likhawat"]):
        actions.append({"action": "subtitles"})
    elif any(w in text for w in ["cut", "trim", "kaato", "kato", "hatao"]):
        sec = extract_number(text, default=10)
        if "end" in text or "aakhir" in text:
            actions.append({"action": "trim", "from_end_seconds": sec})
        else:
            actions.append({"action": "trim", "from_start_seconds": sec})
    elif any(w in text for w in ["text", "likho", "overlay"]):
        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', instruction)
        t = (quoted[0][0] or quoted[0][1]) if quoted else "Sample Text"
        actions.append({"action": "add_text", "text": t})
    elif any(w in text for w in ["speed", "fast", "slow", "tez", "dheere"]):
        factor = extract_number(text, default=1.5)
        if any(w in text for w in ["slow", "dheere"]):
            factor = 1 / factor if factor > 1 else factor
        actions.append({"action": "speed", "factor": factor})
    elif any(w in text for w in ["mute", "audio hatao", "silent"]):
        actions.append({"action": "mute"})
    elif any(w in text for w in ["gif"]):
        actions.append({"action": "to_gif"})
    elif any(w in text for w in ["audio nikaalo", "mp3", "audio extract"]):
        actions.append({"action": "extract_audio"})
    elif any(w in text for w in ["black white", "grayscale", "bw"]):
        actions.append({"action": "grayscale"})
    elif any(w in text for w in ["blur"]):
        actions.append({"action": "blur", "strength": 15})
    elif any(w in text for w in ["vintage", "purana", "retro"]):
        actions.append({"action": "vintage"})
    elif any(w in text for w in ["vignette"]):
        actions.append({"action": "vignette"})
    elif any(w in text for w in ["zoom"]):
        actions.append({"action": "zoom", "factor": 1.3})
    elif any(w in text for w in ["rotate", "ghumao"]):
        actions.append({"action": "rotate", "degrees": 90})
    elif any(w in text for w in ["crop", "square"]):
        actions.append({"action": "crop_square"})
    elif any(w in text for w in ["watermark"]):
        actions.append({"action": "watermark_text", "text": "My Video"})
    elif any(w in text for w in ["fade"]):
        actions.append({"action": "fade"})
    elif any(w in text for w in ["bright", "ujala"]):
        actions.append({"action": "brightness", "level": 0.3})
    elif any(w in text for w in ["dark", "andhera"]):
        actions.append({"action": "brightness", "level": -0.3})
    elif any(w in text for w in ["stabilize", "shake hatao", "smooth karo"]):
        actions.append({"action": "stabilize"})
    elif any(w in text for w in ["green screen", "greenscreen", "chroma"]):
        actions.append({"action": "greenscreen"})
    elif any(w in text for w in ["face blur", "chehra blur", "chehra chhupao"]):
        actions.append({"action": "face_blur"})
    elif any(w in text for w in ["silence hatao", "khaali", "chup"]):
        actions.append({"action": "remove_silence"})
    elif any(w in text for w in ["cinematic"]):
        actions.append({"action": "color_grade", "style": "cinematic"})
    elif any(w in text for w in ["warm", "garam"]):
        actions.append({"action": "color_grade", "style": "warm"})
    elif any(w in text for w in ["cool", "thanda"]):
        actions.append({"action": "color_grade", "style": "cool"})
    elif any(w in text for w in ["teal", "orange"]):
        actions.append({"action": "color_grade", "style": "teal_orange"})
    elif any(w in text for w in ["sharp", "clear karo"]):
        actions.append({"action": "sharpen"})
    elif any(w in text for w in ["old film", "purani film", "scratches"]):
        actions.append({"action": "old_film"})
    elif any(w in text for w in ["reverse", "ulta"]):
        actions.append({"action": "reverse"})
    elif any(w in text for w in ["split screen", "side by side"]):
        actions.append({"action": "split_screen"})
    elif any(w in text for w in ["music add", "background music", "gaana"]):
        actions.append({"action": "add_music"})

    return actions


# ---------------------------------------------------------------
# Background removal (rembg - open source)
# ---------------------------------------------------------------

def remove_background_video(video_path, out_path, progress=None):
    from rembg import remove, new_session
    import cv2

    session = new_session("u2netp")  # halka/tez model (u2net ki jagah)
    uid = uuid.uuid4().hex[:6]
    frames_dir = os.path.join(WORK_DIR, f"frames_{uid}")
    out_frames_dir = os.path.join(WORK_DIR, f"out_frames_{uid}")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(out_frames_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Speed ke liye: agar video badi hai, processing ke liye chhota kar do
    max_dim = 480
    scale = min(1.0, max_dim / max(orig_w, orig_h))
    proc_w, proc_h = int(orig_w * scale), int(orig_h * scale)

    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if scale < 1.0:
            frame = cv2.resize(frame, (proc_w, proc_h))
        cv2.imwrite(os.path.join(frames_dir, f"f_{frame_count:05d}.png"), frame)
        frame_count += 1
    cap.release()

    for i in range(frame_count):
        with open(os.path.join(frames_dir, f"f_{i:05d}.png"), "rb") as f_in:
            result = remove(f_in.read(), session=session)
        out_frame_path = os.path.join(out_frames_dir, f"f_{i:05d}.png")
        with open(out_frame_path, "wb") as f_out:
            f_out.write(result)
        if progress is not None:
            progress((i + 1) / frame_count, desc=f"Background remove: frame {i+1}/{frame_count}")

    cmd = ["ffmpeg", "-y", "-framerate", str(fps),
           "-i", os.path.join(out_frames_dir, "f_%05d.png"),
           "-vf", f"scale={orig_w}:{orig_h}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    run_ffmpeg(cmd)
    shutil.rmtree(frames_dir, ignore_errors=True)
    shutil.rmtree(out_frames_dir, ignore_errors=True)


# ---------------------------------------------------------------
# Subtitles (Whisper - open source)
# ---------------------------------------------------------------

def add_subtitles(video_path, out_path, progress=None):
    from faster_whisper import WhisperModel

    if progress is not None:
        progress(0.1, desc="Audio sun ke samajh raha hoon (Whisper AI)...")

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(video_path)

    srt_path = os.path.join(WORK_DIR, f"sub_{uuid.uuid4().hex[:6]}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            start = format_timestamp(seg.start)
            end = format_timestamp(seg.end)
            f.write(f"{i}\n{start} --> {end}\n{seg.text.strip()}\n\n")

    if progress is not None:
        progress(0.7, desc="Subtitles video mein daal raha hoon...")

    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
           f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&Hffffff'",
           out_path]
    run_ffmpeg(cmd)
    os.remove(srt_path)


def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------------------------------------------------------------
# Ek action ko video par apply karna (FFmpeg based)
# ---------------------------------------------------------------

def apply_single_action(video_path, action, progress=None, extra_file=None):
    uid = uuid.uuid4().hex[:8]
    out_path = os.path.join(WORK_DIR, f"step_{uid}.mp4")
    name = action.get("action")

    if name == "trim":
        if "from_start_seconds" in action:
            sec = action["from_start_seconds"]
            cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", str(sec), "-c", "copy", out_path]
        else:
            sec = action.get("from_end_seconds", 5)
            duration = get_duration(video_path)
            new_dur = max(duration - sec, 1)
            cmd = ["ffmpeg", "-y", "-i", video_path, "-t", str(new_dur), "-c", "copy", out_path]
        run_ffmpeg(cmd)

    elif name == "add_text":
        text = action.get("text", "Text").replace(":", r"\:").replace("'", "")
        vf = (f"drawtext=text='{text}':fontcolor=white:fontsize=48:"
              f"box=1:boxcolor=black@0.5:boxborderw=10:x=(w-text_w)/2:y=h-th-40")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-codec:a", "copy", out_path]
        run_ffmpeg(cmd)

    elif name == "speed":
        factor = action.get("factor", 1.5)
        atempo_chain = []
        f = factor
        while f > 2.0:
            atempo_chain.append("atempo=2.0")
            f /= 2.0
        while f < 0.5:
            atempo_chain.append("atempo=0.5")
            f /= 0.5
        atempo_chain.append(f"atempo={f:.3f}")
        cmd = ["ffmpeg", "-y", "-i", video_path,
               "-vf", f"setpts={1/factor:.3f}*PTS",
               "-af", ",".join(atempo_chain), out_path]
        run_ffmpeg(cmd)

    elif name == "mute":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-c", "copy", "-an", out_path]
        run_ffmpeg(cmd)

    elif name == "to_gif":
        out_path = os.path.join(WORK_DIR, f"step_{uid}.gif")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
               "fps=10,scale=480:-1:flags=lanczos", out_path]
        run_ffmpeg(cmd)

    elif name == "extract_audio":
        out_path = os.path.join(WORK_DIR, f"step_{uid}.mp3")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", out_path]
        run_ffmpeg(cmd)

    elif name == "grayscale":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", "hue=s=0", out_path]
        run_ffmpeg(cmd)

    elif name == "remove_bg":
        remove_background_video(video_path, out_path, progress=progress)

    elif name == "subtitles":
        add_subtitles(video_path, out_path, progress=progress)

    elif name == "blur":
        strength = action.get("strength", 15)
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"boxblur={strength}", out_path]
        run_ffmpeg(cmd)

    elif name == "vintage":
        vf = "curves=vintage,vignette"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "vignette":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", "vignette", out_path]
        run_ffmpeg(cmd)

    elif name == "zoom":
        factor = action.get("factor", 1.3)
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
               f"scale=iw*{factor}:ih*{factor},crop=iw/{factor}:ih/{factor}", out_path]
        run_ffmpeg(cmd)

    elif name == "rotate":
        degrees = action.get("degrees", 90)
        transpose_map = {90: "1", 180: "2,2", 270: "2"}
        if degrees == 180:
            vf = "transpose=2,transpose=2"
        elif degrees == 270:
            vf = "transpose=2"
        else:
            vf = "transpose=1"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "crop_square":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
               "crop='min(iw,ih)':'min(iw,ih)'", out_path]
        run_ffmpeg(cmd)

    elif name == "watermark_text":
        text = action.get("text", "My Video").replace(":", r"\:").replace("'", "")
        vf = (f"drawtext=text='{text}':fontcolor=white@0.6:fontsize=24:"
              f"x=w-tw-20:y=h-th-20")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-codec:a", "copy", out_path]
        run_ffmpeg(cmd)

    elif name == "fade":
        duration = get_duration(video_path)
        fade_out_start = max(duration - 1.5, 0)
        vf = f"fade=t=in:st=0:d=1.5,fade=t=out:st={fade_out_start}:d=1.5"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "brightness":
        level = action.get("level", 0.2)
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"eq=brightness={level}", out_path]
        run_ffmpeg(cmd)

    elif name == "stabilize":
        # Do-pass stabilization (FFmpeg vidstab - free, built-in)
        transform_file = os.path.join(WORK_DIR, f"transforms_{uid}.trf")
        pass1 = ["ffmpeg", "-y", "-i", video_path, "-vf",
                 f"vidstabdetect=shakiness=8:accuracy=9:result={transform_file}",
                 "-f", "null", "-"]
        run_ffmpeg(pass1)
        pass2 = ["ffmpeg", "-y", "-i", video_path, "-vf",
                 f"vidstabtransform=input={transform_file}:zoom=0:smoothing=15",
                 out_path]
        run_ffmpeg(pass2)
        if os.path.exists(transform_file):
            os.remove(transform_file)

    elif name == "greenscreen":
        vf = "colorkey=0x00FF00:0.3:0.2,format=yuva420p"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "face_blur":
        import cv2
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        temp_video = os.path.join(WORK_DIR, f"faceblur_{uid}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_video, fourcc, fps, (w, h))
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 5)
            for (x, y, fw, fh) in faces:
                roi = frame[y:y+fh, x:x+fw]
                roi = c
