import os
import re
import json
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()


def _get_proxy_url() -> Optional[str]:
    """Build Webshare proxy URL from env vars."""
    user = os.environ.get("PROXY_USER")
    pwd = os.environ.get("PROXY_PASS")
    if user and pwd:
        return f"http://{user}:{pwd}@p.webshare.io:9999"
    return None


def _get_signed_caption_url(video_id: str, lang: str = "vi") -> Optional[str]:
    """Scrape YouTube watch page through proxy to get the signed caption URL.

    Step 1: fetch watch page via proxy (needed to bypass IP block)
    Step 2: extract caption track baseUrl from embedded player response
    """
    proxy = _get_proxy_url()
    proxies = {"http": proxy, "https": proxy} if proxy else None

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(watch_url, headers=headers, proxies=proxies, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch watch page: HTTP {resp.status_code}")

    match = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\});", resp.text)
    if not match:
        raise RuntimeError("Could not find ytInitialPlayerResponse in page")

    player_data = json.loads(match.group(1))
    tracks = (
        player_data.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if not tracks:
        raise RuntimeError("No caption tracks found for this video")

    for track in tracks:
        if track.get("languageCode", "").startswith(lang):
            return track.get("baseUrl")
    return tracks[0].get("baseUrl")


def _download_caption(signed_url: str) -> str:
    """Download caption XML directly (no proxy — signed URLs are not IP-restricted)."""
    resp = requests.get(signed_url + "&fmt=srv3", timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Caption download failed: HTTP {resp.status_code}")
    return resp.text


def _parse_xml(xml_text: str) -> list[dict]:
    """Parse timedtext XML into list of {start, text} segments."""
    root = ET.fromstring(xml_text)
    segments = []
    for p in root.findall(".//p"):
        t = int(p.get("t", 0))
        words = [s.text for s in p.iter() if s.text]
        text = "".join(words).strip()
        if text:
            segments.append({"start": t / 1000.0, "text": text})
    return segments


def get_transcript(video_id: str, lang: str = "vi") -> dict:
    """Fetch transcript for a YouTube video.

    Returns dict with: video_id, language, segments, full_text.
    """
    signed_url = _get_signed_caption_url(video_id, lang=lang)
    xml_text = _download_caption(signed_url)
    segments = _parse_xml(xml_text)

    lang_match = re.search(r"[&?]lang=([a-z-]+)", signed_url)
    detected_lang = lang_match.group(1) if lang_match else lang

    return {
        "video_id": video_id,
        "language": detected_lang,
        "segments": segments,
        "full_text": " ".join(s["text"] for s in segments),
    }