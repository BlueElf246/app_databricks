from googleapiclient.discovery import build
from dotenv import load_dotenv
import os
import json
from datetime import datetime
from pathlib import Path

load_dotenv()

API_KEY = os.environ.get("YOUTUBE_API_KEY")

"""
Script kiểm tra (test) YouTube Data API cho công việc cào dữ liệu (scraping) video review ẩm thực.
Yêu cầu: pip install google-api-python-client
"""

import sys
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def init_youtube_client(api_key):
    """Khởi tạo YouTube API Client."""
    return build("youtube", "v3", developerKey=api_key)

def test_search_food_videos(youtube, query="review ẩm thực Sài Gòn", max_results=5):
    """Phần 1: Tìm kiếm video review đồ ăn theo từ khóa."""
    print("\n" + "="*50)
    print(f"1. TEST SEARCH: Từ khóa '{query}'")
    print("="*50)
    
    try:
        response = youtube.search().list(
            q=query,
            part="snippet",
            type="video",
            maxResults=max_results
        ).execute()
        print(response)
        video_ids = []
        for idx, item in enumerate(response.get("items", []), 1):
            v_id = item["id"]["videoId"]
            title = item["snippet"]["title"]
            channel_title = item["snippet"]["channelTitle"]
            video_ids.append(v_id)
            print(f"[{idx}] ID: {v_id}")
            print(f"    Tiêu đề: {title}")
            print(f"    Kênh: {channel_title}")
        
        return video_ids
    except HttpError as e:
        print(f"Lỗi khi tìm kiếm: {e}")
        return []

def test_get_video_details(youtube, video_ids):
    """Phần 2: Lấy chi tiết thông tin và thống kê của các video."""
    print("\n" + "="*50)
    print("2. TEST VIDEO DETAILS: Lấy chi tiết thông tin & chỉ số")
    print("="*50)
    
    if not video_ids:
        print("Không có Video ID nào để kiểm tra.")
        return None
        
    try:
        response = youtube.videos().list(
            id=",".join(video_ids),
            part="snippet,statistics,contentDetails"
        ).execute()

        channel_id = None
        for item in response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            channel_id = snippet.get("channelId")
            
            print(f"• Tiêu đề: {snippet.get('title')}")
            print(f"  Kênh ID: {channel_id}")
            print(f"  Mô tả: {snippet.get('description', '')[:100]}...")
            print(f"  Lượt xem: {stats.get('viewCount', '0')} | Lượt thích: {stats.get('likeCount', '0')} | Bình luận: {stats.get('commentCount', '0')}")
            print(f"  Thời lượng: {item['contentDetails'].get('duration')}")
            print("-" * 40)
            
        return channel_id
    except HttpError as e:
        print(f"Lỗi khi lấy thông tin video: {e}")
        return None

def test_list_captions(youtube, video_id):
    """Phần 3: Kiểm tra danh sách phụ đề (captions) của 1 video."""
    print("\n" + "="*50)
    print(f"3. TEST CAPTIONS: Kiểm tra phụ đề của Video ID '{video_id}'")
    print("="*50)
    
    try:
        response = youtube.captions().list(
            videoId=video_id,
            part="snippet"
        ).execute()

        items = response.get("items", [])
        if not items:
            print("Video này không có bản phụ đề nào (hoặc phụ đề tự động chưa sẵn sàng).")
        else:
            for cap in items:
                snippet = cap["snippet"]
                print(f"• Caption ID: {cap['id']}")
                print(f"  Ngôn ngữ: {snippet.get('language')}")
                print(f"  Loại phụ đề: {snippet.get('trackKind')}")
                print(f"  Tên hiển thị: {snippet.get('name')}")
    except HttpError as e:
        print(f"Lỗi khi lấy danh sách phụ đề: {e}")

def test_get_comments(youtube, video_id, max_results=5):
    """Phần 4: Thu thập bình luận bên dưới video."""
    print("\n" + "="*50)
    print(f"4. TEST COMMENTS: Lấy bình luận của Video ID '{video_id}'")
    print("="*50)
    
    try:
        response = youtube.commentThreads().list(
            videoId=video_id,
            part="snippet",
            maxResults=max_results,
            textFormat="plainText"
        ).execute()

        for idx, item in enumerate(response.get("items", []), 1):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            author = comment.get("authorDisplayName")
            text = comment.get("textDisplay")
            likes = comment.get("likeCount", 0)
            print(f"[{idx}] {author} ({likes} likes):")
            print(f"    \"{text}\"")
    except HttpError as e:
        print(f"Lỗi khi lấy bình luận: {e}")

def test_get_channel_details(youtube, channel_id):
    """Phần 5: Lấy thông tin tổng quan của kênh Food Reviewer."""
    print("\n" + "="*50)
    print(f"5. TEST CHANNEL DETAILS: Kênh ID '{channel_id}'")
    print("="*50)
    
    if not channel_id:
        print("Không có Channel ID để kiểm tra.")
        return

    try:
        response = youtube.channels().list(
            id=channel_id,
            part="snippet,statistics"
        ).execute()

        for ch in response.get("items", []):
            snippet = ch["snippet"]
            stats = ch.get("statistics", {})
            print(f"• Tên kênh: {snippet.get('title')}")
            print(f"  Mô tả: {snippet.get('description', '')[:100]}...")
            print(f"  Người đăng ký (Subscribers): {stats.get('subscriberCount', 'Ẩn')}")
            print(f"  Tổng số video: {stats.get('videoCount', 0)}")
            print(f"  Tổng lượt xem kênh: {stats.get('viewCount', 0)}")
    except HttpError as e:
        print(f"Lỗi khi lấy thông tin kênh: {e}")

def export_search_results(youtube, query="review ẩm thực Sài Gòn", max_results=3):
    """Export kết quả tìm kiếm ra JSON."""
    print("\n📤 Exporting search results...")
    try:
        response = youtube.search().list(
            q=query,
            part="snippet",
            type="video",
            maxResults=max_results
        ).execute()

        search_data = {
            "query": query,
            "total_results": response.get("pageInfo", {}).get("totalResults"),
            "results_per_page": response.get("pageInfo", {}).get("resultsPerPage"),
            "items": []
        }

        for item in response.get("items", []):
            search_data["items"].append({
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel_id": item["snippet"]["channelId"],
                "channel_title": item["snippet"]["channelTitle"],
                "published_at": item["snippet"]["publishedAt"],
                "description": item["snippet"]["description"],
                "thumbnail_url": item["snippet"]["thumbnails"].get("high", {}).get("url")
            })

        return search_data
    except HttpError as e:
        print(f"Error exporting search results: {e}")
        return None

def export_video_details(youtube, video_ids):
    """Export chi tiết video ra JSON."""
    print("📤 Exporting video details...")
    try:
        response = youtube.videos().list(
            id=",".join(video_ids),
            part="snippet,statistics,contentDetails"
        ).execute()

        videos_data = {"videos": []}

        for item in response.get("items", []):
            videos_data["videos"].append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "channel_id": item["snippet"]["channelId"],
                "published_at": item["snippet"]["publishedAt"],
                "description": item["snippet"]["description"],
                "category_id": item["snippet"].get("categoryId"),
                "tags": item["snippet"].get("tags", []),
                "view_count": int(item["statistics"].get("viewCount", 0)),
                "like_count": int(item["statistics"].get("likeCount", 0)) if item["statistics"].get("likeCount") else None,
                "comment_count": int(item["statistics"].get("commentCount", 0)) if item["statistics"].get("commentCount") else None,
                "duration": item["contentDetails"]["duration"],
                "definition": item["contentDetails"].get("definition"),
                "caption_available": item["contentDetails"].get("caption") == "true"
            })

        return videos_data
    except HttpError as e:
        print(f"Error exporting video details: {e}")
        return None

def export_captions_info(youtube, video_id):
    """Export thông tin phụ đề ra JSON."""
    print("📤 Exporting captions info...")
    try:
        response = youtube.captions().list(
            videoId=video_id,
            part="snippet"
        ).execute()

        captions_data = {
            "video_id": video_id,
            "captions": []
        }

        for cap in response.get("items", []):
            captions_data["captions"].append({
                "caption_id": cap["id"],
                "language": cap["snippet"]["language"],
                "language_name": cap["snippet"].get("name"),
                "track_kind": cap["snippet"].get("trackKind"),
                "is_cc": cap["snippet"].get("isCC", False),
                "is_draft": cap["snippet"].get("isDraft", False),
                "is_auto_synced": cap["snippet"].get("isAutoSynced", False),
                "status": cap["snippet"].get("status")
            })

        return captions_data
    except HttpError as e:
        print(f"Error exporting captions: {e}")
        return None

def download_caption_transcript(youtube, video_id, caption_id):
    """Download noi dung phu de thuc te tu YouTube."""
    print(f"📥 Downloading transcript for caption {caption_id}...")
    try:
        # Download caption (format: srt)
        caption_content = youtube.captions().download(
            id=caption_id,
            tfmt="srt"  # SubRip Text format
        ).execute()

        return caption_content.decode('utf-8') if isinstance(caption_content, bytes) else caption_content
    except HttpError as e:
        print(f"Error downloading caption: {e}")
        return None

def export_comments(youtube, video_id, max_results=10):
    """Export bình luận ra JSON."""
    print("📤 Exporting comments...")
    try:
        response = youtube.commentThreads().list(
            videoId=video_id,
            part="snippet",
            maxResults=max_results,
            textFormat="plainText"
        ).execute()

        comments_data = {
            "video_id": video_id,
            "total_comments": response.get("pageInfo", {}).get("totalResults"),
            "comments": []
        }

        for item in response.get("items", []):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            comments_data["comments"].append({
                "comment_id": item["id"],
                "author": comment["authorDisplayName"],
                "author_channel_id": comment.get("authorChannelId", {}).get("value"),
                "text": comment["textDisplay"],
                "like_count": comment["likeCount"],
                "published_at": comment["publishedAt"],
                "reply_count": item["snippet"]["totalReplyCount"]
            })

        return comments_data
    except HttpError as e:
        print(f"Error exporting comments: {e}")
        return None

def export_channel_details(youtube, channel_id):
    """Export thông tin kênh ra JSON."""
    print("📤 Exporting channel details...")
    try:
        response = youtube.channels().list(
            id=channel_id,
            part="snippet,statistics"
        ).execute()

        channel_data = {}

        for ch in response.get("items", []):
            channel_data = {
                "channel_id": ch["id"],
                "title": ch["snippet"]["title"],
                "custom_url": ch["snippet"].get("customUrl"),
                "description": ch["snippet"]["description"],
                "published_at": ch["snippet"]["publishedAt"],
                "subscriber_count": ch["statistics"].get("subscriberCount", "Hidden"),
                "video_count": int(ch["statistics"].get("videoCount", 0)),
                "view_count": int(ch["statistics"].get("viewCount", 0))
            }

        return channel_data
    except HttpError as e:
        print(f"Error exporting channel details: {e}")
        return None

def save_all_data_to_json(search_data, videos_data, captions_data, comments_data, channel_data):
    """Lưu tất cả dữ liệu vào file JSON."""
    output_dir = Path("api_test_results")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save search results
    if search_data:
        with open(output_dir / f"search_results_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump(search_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved: search_results_{timestamp}.json")

    # Save video details
    if videos_data:
        with open(output_dir / f"video_details_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump(videos_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved: video_details_{timestamp}.json")

    # Save captions
    if captions_data:
        with open(output_dir / f"captions_info_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump(captions_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved: captions_info_{timestamp}.json")

    # Save comments
    if comments_data:
        with open(output_dir / f"comments_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump(comments_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved: comments_{timestamp}.json")

    # Save channel details
    if channel_data:
        with open(output_dir / f"channel_details_{timestamp}.json", "w", encoding="utf-8") as f:
            json.dump(channel_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved: channel_details_{timestamp}.json")

    print(f"\n📁 All files saved to: {output_dir.absolute()}")

def main():
    if API_KEY == "YOUR_YOUTUBE_API_KEY":
        print("⚠️ Vui lòng thay 'YOUR_YOUTUBE_API_KEY' bằng API Key thật của bạn trước khi chạy script!")
        return

    youtube = init_youtube_client(API_KEY)

    # 1. Chạy test tìm kiếm video
    video_ids = test_search_food_videos(youtube, query="review ẩm thực Sài Gòn", max_results=3)
    search_data = export_search_results(youtube, query="review ẩm thực Sài Gòn", max_results=3)

    if video_ids:
        first_video_id = video_ids[0]

        # 2. Chạy test lấy chi tiết các video
        channel_id = test_get_video_details(youtube, video_ids)
        videos_data = export_video_details(youtube, video_ids)

        # 3. Chạy test kiểm tra phụ đề video đầu tiên
        test_list_captions(youtube, first_video_id)
        captions_data = export_captions_info(youtube, first_video_id)

        # 3b. Download transcript noi dung phu de
        transcript_content = None
        if captions_data and captions_data.get("captions"):
            caption_id = captions_data["captions"][0]["caption_id"]
            transcript_content = download_caption_transcript(youtube, first_video_id, caption_id)

            # Save transcript to file
            if transcript_content:
                output_dir = Path("api_test_results")
                output_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                transcript_file = output_dir / f"transcript_{first_video_id}_{timestamp}.srt"
                with open(transcript_file, "w", encoding="utf-8") as f:
                    f.write(transcript_content)
                print(f"✅ Transcript saved: {transcript_file.name}")

        # 4. Chạy test lấy bình luận của video đầu tiên
        test_get_comments(youtube, first_video_id, max_results=5)
        comments_data = export_comments(youtube, first_video_id, max_results=10)

        # 5. Chạy test lấy thông tin kênh phát video
        channel_data = None
        if channel_id:
            test_get_channel_details(youtube, channel_id)
            channel_data = export_channel_details(youtube, channel_id)

        # Save all data to JSON
        print("\n" + "="*50)
        print("SAVING ALL DATA TO JSON FILES")
        print("="*50)
        save_all_data_to_json(search_data, videos_data, captions_data, comments_data, channel_data)
if __name__ == "__main__":
    main()
