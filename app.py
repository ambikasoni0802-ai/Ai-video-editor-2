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
                roi = cv2.GaussianBlur(roi, (35, 35), 0)
                frame[y:y+fh, x:x+fw] = roi
            writer.write(frame)
            frame_idx += 1
            if progress is not None and frame_idx % 10 == 0:
                pass  # progress skip for speed
        cap.release()
        writer.release()
        # Original audio wapas jodo
        cmd = ["ffmpeg", "-y", "-i", temp_video, "-i", video_path,
               "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0?",
               "-shortest", out_path]
        run_ffmpeg(cmd)
        os.remove(temp_video)

    elif name == "remove_silence":
        vf_af = "silenceremove=stop_periods=-1:stop_threshold=-35dB:stop_duration=0.3"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-af", vf_af, out_path]
        run_ffmpeg(cmd)

    elif name == "color_grade":
        style = action.get("style", "cinematic")
        presets = {
            "cinematic": "curves=preset=darker,eq=contrast=1.1:saturation=0.9",
            "warm": "eq=gamma_r=1.1:gamma_b=0.9:saturation=1.2",
            "cool": "eq=gamma_b=1.15:gamma_r=0.9:saturation=1.1",
            "teal_orange": "curves=preset=medium_contrast,eq=gamma_r=1.05:gamma_b=1.1:saturation=1.3",
        }
        vf = presets.get(style, presets["cinematic"])
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "sharpen":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf",
               "unsharp=5:5:1.0:5:5:0.0", out_path]
        run_ffmpeg(cmd)

    elif name == "old_film":
        vf = "curves=vintage,noise=alls=20:allf=t,vignette"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, out_path]
        run_ffmpeg(cmd)

    elif name == "reverse":
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", "reverse", "-af", "areverse", out_path]
        run_ffmpeg(cmd)

    elif name == "split_screen":
        if not extra_file:
            raise RuntimeError("Split screen ke liye dusri video bhi upload karo (neeche wale box mein).")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", extra_file,
               "-filter_complex",
               "[0:v]scale=640:720[left];[1:v]scale=640:720[right];[left][right]hstack=inputs=2[v]",
               "-map", "[v]", "-map", "0:a?", out_path]
        run_ffmpeg(cmd)

    elif name == "add_music":
        if not extra_file:
            raise RuntimeError("Background music add karne ke liye music file bhi upload karo (neeche wale box mein).")
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", extra_file,
               "-filter_complex",
               "[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.4[a]",
               "-map", "0:v", "-map", "[a]", "-c:v", "copy", out_path]
        run_ffmpeg(cmd)

    elif name == "text_to_speech":
        text = action.get("text", "")
        if not text:
            raise RuntimeError("Voiceover ke liye text batao kya bolna hai.")
        import pyttsx3
        tts_path = os.path.join(WORK_DIR, f"tts_{uid}.mp3")
        engine = pyttsx3.init()
        engine.save_to_file(text, tts_path)
        engine.runAndWait()
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", tts_path,
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", out_path]
        run_ffmpeg(cmd)
        os.remove(tts_path)

    else:
        return video_path  # koi change nahi

    return out_path


# ---------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------

def apply_edit(video_path, instruction, extra_file, progress=gr.Progress()):
    if video_path is None:
        return None, "Pehle ek video upload karo."

    extra_file_path = extra_file.name if extra_file is not None else None

    progress(0, desc="Instruction samajh raha hoon...")

    actions = None
    used_llm = False
    if GROQ_API_KEY:
        actions = understand_instruction_with_llm(instruction)
        if actions:
            used_llm = True

    if not actions:
        actions = fallback_parse(instruction)

    if not actions:
        return None, (
            "Instruction samajh nahi aaya. Thoda clearly batao, jaise:\n"
            "- 'background hatao' / 'subtitles add karo' / 'blur karo'\n"
            "- 'pehle 10 second kaato' / 'text likho Hello'\n"
            "- 'speed 2x karo' / 'vintage effect lagao' / 'zoom karo'"
        )

    current_video = video_path
    total = len(actions)
    try:
        for idx, action in enumerate(actions):
            def sub_progress(frac, desc=""):
                overall = (idx + frac) / total
                progress(overall, desc=desc or f"Step {idx+1}/{total}")
            current_video = apply_single_action(current_video, action, progress=sub_progress, extra_file=extra_file_path)

        engine = "Groq AI (LLM ne samjha)" if used_llm else "Keyword matching"
        msg = f"Ho gaya! ({engine}) Actions apply hue: " + ", ".join(a["action"] for a in actions)
        return current_video, msg

    except Exception as e:
        return None, f"Error aa gaya: {str(e)}"


# ---------------------------------------------------------------
# UI Language translations (major world languages)
# ---------------------------------------------------------------

UI_TEXT = {
    "English": {
        "title": "🎬 AI Video Editor (High-Level, Free & Open Source)",
        "video_label": "Upload Video",
        "extra_label": "Extra file (optional) - second video for split screen, or audio for background music",
        "instruction_label": "Give Instruction (speak in ANY language)",
        "placeholder": 'e.g. "remove the background and add subtitles"',
        "button": "Edit Video",
        "output_label": "Edited Output",
        "status_label": "Status",
    },
    "हिंदी (Hindi)": {
        "title": "🎬 AI वीडियो एडिटर (हाई-लेवल, फ्री और ओपन सोर्स)",
        "video_label": "वीडियो अपलोड करो",
        "extra_label": "अतिरिक्त फाइल (वैकल्पिक)",
        "instruction_label": "निर्देश दो (किसी भी भाषा में बोलो)",
        "placeholder": 'जैसे: "बैकग्राउंड हटाओ और सबटाइटल जोड़ो"',
        "button": "एडिट करो",
        "output_label": "एडिटेड वीडियो",
        "status_label": "स्थिति",
    },
    "Español (Spanish)": {
        "title": "🎬 Editor de Video IA (Nivel Alto, Gratis y Código Abierto)",
        "video_label": "Subir Video",
        "extra_label": "Archivo extra (opcional) - segundo video para pantalla dividida, o audio para música",
        "instruction_label": "Da Instrucciones (en CUALQUIER idioma)",
        "placeholder": 'ej. "quita el fondo y añade subtítulos"',
        "button": "Editar Video",
        "output_label": "Video Editado",
        "status_label": "Estado",
    },
    "Français (French)": {
        "title": "🎬 Éditeur Vidéo IA (Niveau Avancé, Gratuit et Open Source)",
        "video_label": "Télécharger la Vidéo",
        "extra_label": "Fichier supplémentaire (optionnel) - deuxième vidéo pour écran partagé, ou audio pour musique",
        "instruction_label": "Donnez des Instructions (dans N'IMPORTE QUELLE langue)",
        "placeholder": 'ex. "enlève le fond et ajoute des sous-titres"',
        "button": "Éditer",
        "output_label": "Vidéo Éditée",
        "status_label": "Statut",
    },
    "العربية (Arabic)": {
        "title": "🎬 محرر فيديو الذكاء الاصطناعي (مستوى عالٍ، مجاني ومفتوح المصدر)",
        "video_label": "رفع الفيديو",
        "extra_label": "ملف إضافي (اختياري) - فيديو ثانٍ للشاشة المقسمة، أو صوت للموسيقى",
        "instruction_label": "أعط تعليمات (بأي لغة)",
        "placeholder": 'مثال: "أزل الخلفية وأضف ترجمة"',
        "button": "تعديل الفيديو",
        "output_label": "الفيديو المعدل",
        "status_label": "الحالة",
    },
    "中文 (Chinese)": {
        "title": "🎬 AI视频编辑器（高级版，免费开源）",
        "video_label": "上传视频",
        "extra_label": "额外文件（可选）- 用于分屏的第二个视频，或用于背景音乐的音频",
        "instruction_label": "给出指令（用任何语言）",
        "placeholder": '例如："去除背景并添加字幕"',
        "button": "编辑视频",
        "output_label": "编辑后的视频",
        "status_label": "状态",
    },
    "Português (Portuguese)": {
        "title": "🎬 Editor de Vídeo IA (Alto Nível, Grátis e Código Aberto)",
        "video_label": "Enviar Vídeo",
        "extra_label": "Arquivo extra (opcional) - segundo vídeo para tela dividida, ou áudio para música",
        "instruction_label": "Dê Instruções (em QUALQUER idioma)",
        "placeholder": 'ex: "remova o fundo e adicione legendas"',
        "button": "Editar Vídeo",
        "output_label": "Vídeo Editado",
        "status_label": "Status",
    },
    "Русский (Russian)": {
        "title": "🎬 AI Видеоредактор (Продвинутый, Бесплатно и Открытый Код)",
        "video_label": "Загрузить Видео",
        "extra_label": "Доп. файл (необязательно)",
        "instruction_label": "Дайте Инструкцию (на ЛЮБОМ языке)",
        "placeholder": 'напр. "убери фон и добавь субтитры"',
        "button": "Редактировать",
        "output_label": "Готовое Видео",
        "status_label": "Статус",
    },
    "বাংলা (Bengali)": {
        "title": "🎬 AI ভিডিও এডিটর (হাই-লেভেল, ফ্রি এবং ওপেন সোর্স)",
        "video_label": "ভিডিও আপলোড করুন",
        "extra_label": "অতিরিক্ত ফাইল (ঐচ্ছিক)",
        "instruction_label": "নির্দেশ দিন (যেকোনো ভাষায়)",
        "placeholder": 'ব্যাকগ্রাউন্ড সরান',
        "button": "এডিট করুন",
        "output_label": "এডিট করা ভিডিও",
        "status_label": "অবস্থা",
    },
    "اردو (Urdu)": {
        "title": "🎬 AI ویڈیو ایڈیٹر (اعلیٰ سطح، مفت اور اوپن سورس)",
        "video_label": "ویڈیو اپ لوڈ کریں",
        "extra_label": "اضافی فائل (اختیاری)",
        "instruction_label": "ہدایت دیں (کسی بھی زبان میں)",
        "placeholder": 'مثلاً: "بیک گراؤنڈ ہٹائیں اور سب ٹائٹل شامل کریں"',
        "button": "ویڈیو ایڈٹ کریں",
        "output_label": "ایڈٹ شدہ ویڈیو",
        "status_label": "حالت",
    },
    "日本語 (Japanese)": {
        "title": "🎬 AIビデオエディター（高レベル、無料・オープンソース）",
        "video_label": "動画をアップロード",
        "extra_label": "追加ファイル（任意）- 分割画面用の2つ目の動画、または音楽用の音声",
        "instruction_label": "指示を出す（どの言語でもOK）",
        "placeholder": '例：「背景を削除して字幕を追加」',
        "button": "編集する",
        "output_label": "編集済み動画",
        "status_label": "状態",
    },
    "Deutsch (German)": {
        "title": "🎬 KI-Videoeditor (Hochleistung, Kostenlos & Open Source)",
        "video_label": "Video Hochladen",
        "extra_label": "Zusätzliche Datei (optional) - zweites Video für Split-Screen, oder Audio für Musik",
        "instruction_label": "Anweisung Geben (in JEDER Sprache)",
        "placeholder": 'z.B. "entferne den Hintergrund und füge Untertitel hinzu"',
        "button": "Video Bearbeiten",
        "output_label": "Bearbeitetes Video",
        "status_label": "Status",
    },
}

LANGUAGE_NAMES = list(UI_TEXT.keys())


with gr.Blocks(title="AI Video Editor - High Level") as demo:
    lang_state = gr.State("English")

    with gr.Row():
        lang_dropdown = gr.Dropdown(
            choices=LANGUAGE_NAMES, value="English",
            label="🌐 Website Language / वेबसाइट भाषा"
        )

    title_md = gr.Markdown(UI_TEXT["English"]["title"])

    if GROQ_API_KEY:
        gr.Markdown("✅ **Smart mode ON** — speak in any language, AI will understand.")
    else:
        gr.Markdown(
            "⚠️ **Basic mode** — abhi sirf fixed keywords samajhta hai. "
            "Smart mode on karne ke liye `GROQ_API_KEY` environment variable set karo."
        )

    with gr.Row():
        with gr.Column():
            video_input = gr.Video(label=UI_TEXT["English"]["video_label"])
            extra_file_input = gr.File(
                label=UI_TEXT["English"]["extra_label"],
                file_types=["video", "audio"]
            )
            instruction_input = gr.Textbox(
                label=UI_TEXT["English"]["instruction_label"],
                placeholder=UI_TEXT["English"]["placeholder"],
                lines=2
            )
            submit_btn = gr.Button(UI_TEXT["English"]["button"], variant="primary")

        with gr.Column():
            video_output = gr.File(label=UI_TEXT["English"]["output_label"])
            status_output = gr.Textbox(label=UI_TEXT["English"]["status_label"], interactive=False, lines=3)

    def switch_language(lang):
        t = UI_TEXT.get(lang, UI_TEXT["English"])
        return (
            gr.update(value=t["title"]),
            gr.update(label=t["video_label"]),
            gr.update(label=t["extra_label"]),
            gr.update(label=t["instruction_label"], placeholder=t["placeholder"]),
            gr.update(value=t["button"]),
            gr.update(label=t["output_label"]),
            gr.update(label=t["status_label"]),
        )

    lang_dropdown.change(
        fn=switch_language,
        inputs=[lang_dropdown],
        outputs=[title_md, video_input, extra_file_input, instruction_input,
                 submit_btn, video_output, status_output]
    )

    submit_btn.click(
        fn=apply_edit,
        inputs=[video_input, instruction_input, extra_file_input],
        outputs=[video_output, status_output]
    )

    gr.Markdown(
        "### Yeh sab kar sakta hai / What this can do\n"
        "Background remove • Subtitles • Trim • Text overlay • Speed • Mute • "
        "GIF • Audio extract • Black&white • Blur • Vintage • Vignette • Zoom • "
        "Rotate • Square crop • Watermark • Fade in/out • Brightness • "
        "**Stabilize** • **Green screen removal** • "
        "**Face blur** • **Silence remove** • "
        "**Color grading (cinematic/warm/cool/teal-orange)** • Sharpen • Old film look • "
        "**Reverse** • **Split screen** • **Background music**\n\n"
        "**Multi-language:** Type your instruction in ANY language — Hindi, English, "
        "Spanish, Arabic, Chinese, or any other. The AI understands them all."
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
