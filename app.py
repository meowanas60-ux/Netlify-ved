import os
import shutil
import tempfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
import yt_dlp

app = Flask(__name__)

MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "300"))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "downbot",
    })


def find_downloaded_file(folder: Path):
    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.stat().st_size > 0
        and p.suffix.lower() not in {".part", ".ytdl"}
    ]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


@app.post("/api/download")
def download():
    data = request.get_json(silent=True) or request.form
    url = str(data.get("url", "")).strip()
    audio_only = str(data.get("audio_only", "false")).lower() in {
        "1", "true", "yes", "on"
    }

    if not url:
        return jsonify({"success": False, "error": "Please provide a video URL."}), 400

    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"success": False, "error": "Invalid URL."}), 400

    temp_dir = Path(tempfile.mkdtemp(prefix="downbot_"))

    try:
        output_template = str(temp_dir / "%(title).80s.%(ext)s")

        if audio_only:
            format_selector = "bestaudio/best"
        else:
            format_selector = (
                "bestvideo[filesize<300M]+bestaudio/"
                "best[filesize<300M]/best"
            )

        options = {
            "outtmpl": output_template,
            "format": format_selector,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "merge_output_format": "mp4",
            "socket_timeout": 30,
            "retries": 2,
            "overwrites": True,
        }

        if audio_only:
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)

        file_path = find_downloaded_file(temp_dir)

        if not file_path:
            raise RuntimeError("No downloadable file was created.")

        file_size = file_path.stat().st_size
        if file_size > MAX_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"File is larger than the {MAX_DOWNLOAD_MB} MB server limit."
            )

        title = info.get("title") or "download"
        extension = "mp3" if audio_only else (file_path.suffix.lstrip(".") or "mp4")

        response = send_file(
            file_path,
            as_attachment=True,
            download_name=f"{title[:80]}.{extension}",
            mimetype="audio/mpeg" if audio_only else "video/mp4",
        )

        @response.call_on_close
        def cleanup():
            shutil.rmtree(temp_dir, ignore_errors=True)

        return response

    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = str(exc)

        if "Unsupported URL" in message:
            message = "This URL or platform is not supported."
        elif "Private video" in message or "Sign in" in message:
            message = "This video is private or requires login."
        elif "Video unavailable" in message:
            message = "This video is unavailable."

        return jsonify({
            "success": False,
            "error": message or "Download failed. Please try again."
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
