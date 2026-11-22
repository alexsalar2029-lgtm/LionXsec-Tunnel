"""Build a Windows distribution from application files, including on Linux."""
import argparse
import json
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from desktop_oauth import PUBLISHER_CLIENT, read_client

SOURCE = Path(__file__).resolve().parent
FILES = (
    "run_gui.bat", "start_youtube_uploader.bat", "auto_run.bat", "login_youtube.bat",
    "install_telegram.bat", "yt_uploader.py", "yt_uploader_gui.py", "telegram_downloader.py",
    "uploader.py", "desktop_oauth.py", "video_content.py", "tessdata/eng.traineddata",
    "tessdata/fas.traineddata", "tessdata/LICENSE", "tessdata/NOTICE",
    "requirements.txt", "requirements-oauth.txt", "requirements-uploader.txt",
    "requirements-uploader-telegram.txt", "requirements-uploader-vision.txt",
    "yt_config.example.json", "videos/.keep", "covers/.keep", "make_uploader_package.ps1",
    "make_uploader_package.py", "README_DEPLOYMENT_FA.md", "README_FA.md",
    "test_uploader.py", "test_desktop.py",
)


def build(output, *, archive=False, clean=False, desktop_client=None):
    output = output.resolve()
    if output == SOURCE or output in SOURCE.parents:
        raise ValueError("Choose a distribution directory separate from the application.")
    output.mkdir(parents=True, exist_ok=True)
    if not clean and any(output.iterdir()):
        raise FileExistsError("Output directory is populated. Choose a new one or use --clean.")
    payload = {}
    if desktop_client is not None:
        payload[PUBLISHER_CLIENT] = json.dumps(read_client(desktop_client), indent=2).encode("utf-8")
    elif (output / PUBLISHER_CLIENT).exists():
        raise ValueError("Choose a fresh output directory for a build without the publisher client.")
    for name in FILES:
        data = (SOURCE / name).read_bytes()
        if name.endswith((".bat", ".ps1")):
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        payload[name] = data
    for name, data in payload.items():
        dest = output / name
        if dest.is_symlink() or not dest.resolve().is_relative_to(output):
            raise ValueError(f"Output path redirects outside the package: {name}")
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_bytes(data)
    if archive:
        destination = output.parent / (output.name + ".zip")
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with ZipFile(tmp_path, "w") as bundle:
                for name, data in payload.items():
                    entry = ZipInfo(name)
                    entry.external_attr = 0o100644 << 16
                    bundle.writestr(entry, data, compress_type=ZIP_DEFLATED)
            os.replace(tmp_path, destination)
        finally:
            tmp_path.unlink(missing_ok=True)
        print(destination)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=SOURCE / "dist" / "youtube-uploader")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--clean", action="store_true", help="Replace the listed application files in an existing output directory")
    parser.add_argument("--desktop-client", type=Path, help="Include the publisher's Google Desktop app registration in this customer build")
    args = parser.parse_args()
    print(build(args.output_directory, archive=args.zip, clean=args.clean, desktop_client=args.desktop_client))
