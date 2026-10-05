import streamlit as st

from database import get_saved_videos, init_db, upsert_videos
from transcript import get_transcript
from youtube_client import (
    YouTubeClientError,
    get_video_statistics,
    search_videos,
)

st.set_page_config(
    page_title="YouTube Restaurant Search",
    page_icon="🔎",
    layout="wide",
)

# Initialize database on first run
try:
    init_db()
except Exception as e:
    st.error(f"Không thể kết nối tới database: {e}")
    st.info("Đảm bảo Lakebase Postgres đang chạy và được cấu hình.")
    st.stop()

st.title("🔎 Tìm kiếm video YouTube")
st.caption("Tìm video review quán ăn — kết nối trực tiếp Lakebase Postgres")

# --- Transcript section ---
with st.expander("📝 Lấy transcript video"):
    transcript_vid = st.text_input("Video ID", value="", key="transcript_vid")
    if st.button("Lấy transcript", key="btn_transcript"):
        try:
            with st.spinner("Đang tải transcript..."):
                result = get_transcript(transcript_vid, lang="vi")
            st.success(f"✅ {len(result['segments'])} đoạn | Ngôn ngữ: {result['language']}")
            st.text_area("Transcript", value=result["full_text"], height=300, key="transcript_output")
        except Exception as e:
            st.error(f"❌ {e}")

query = st.text_input(
    "Từ khóa tìm kiếm",
    value="quán ăn Hồ Chí Minh",
    placeholder="Ví dụ: phở ngon Sài Gòn",
)

max_results = st.number_input(
    "Số lượng kết quả",
    min_value=1,
    max_value=50,
    value=10,
)

order = st.selectbox(
    "Sắp xếp theo",
    options=["relevance", "date", "viewCount"],
    format_func=lambda value: {
        "relevance": "Liên quan nhất",
        "date": "Mới nhất",
        "viewCount": "Lượt xem cao nhất",
    }[value],
)

published_after = st.date_input("Đăng sau ngày", value=None)
published_before = st.date_input("Đăng trước ngày", value=None)

if st.button("Tìm kiếm", type="primary"):
    query = query.strip()

    if not query:
        st.warning("Vui lòng nhập từ khóa tìm kiếm.")
    else:
        try:
            with st.spinner("Đang tìm kiếm..."):
                data = search_videos(
                    query=query,
                    max_results=max_results,
                    order=order,
                    published_after=published_after,
                    published_before=published_before,
                )

            items = data.get("items", [])

            if not items:
                st.info("Không tìm thấy video.")
            else:
                videos = []
                for item in items:
                    video_id = item.get("id", {}).get("videoId")
                    snippet = item.get("snippet", {})
                    if not video_id:
                        continue
                    videos.append({
                        "video_id": video_id,
                        "title": snippet.get("title"),
                        "description": snippet.get("description"),
                        "channel_id": snippet.get("channelId"),
                        "channel_title": snippet.get("channelTitle"),
                        "published_at": snippet.get("publishedAt"),
                        "thumbnail_url": (
                            snippet.get("thumbnails", {})
                            .get("high", {})
                            .get("url")
                        ),
                        "video_url": f"https://www.youtube.com/watch?v={video_id}",
                    })

                stats = get_video_statistics(
                    [v["video_id"] for v in videos]
                )
                for video in videos:
                    video.update(stats.get(video["video_id"], {}))

                upsert_videos(videos)

                st.success(f"Tìm thấy {len(videos)} video. Đã lưu vào database.")

                for video in videos:
                    left, right = st.columns([1, 3])

                    with left:
                        thumbnail_url = video.get("thumbnail_url")
                        if thumbnail_url:
                            st.image(thumbnail_url, use_container_width=True)

                    with right:
                        st.subheader(video.get("title", "Không có tiêu đề"))
                        st.write(
                            f"**Kênh:** {video.get('channel_title', 'Không rõ')}"
                        )

                        view_count = video.get("view_count")
                        like_count = video.get("like_count")

                        st.write(
                            f"**Lượt xem:** {view_count:,}"
                            if view_count is not None
                            else "**Lượt xem:** Không có dữ liệu"
                        )
                        st.write(
                            f"**Lượt thích:** {like_count:,}"
                            if like_count is not None
                            else "**Lượt thích:** Không có dữ liệu"
                        )
                        st.write(
                            f"**Ngày đăng:** {video.get('published_at', 'Không rõ')}"
                        )

                        video_url = video.get("video_url")
                        if video_url:
                            st.link_button("Xem video trên YouTube", video_url)

                        with st.expander(f"📝 Transcript — {video['video_id']}"):
                            if st.button("Lấy transcript", key=f"tc_{video['video_id']}"):
                                try:
                                    with st.spinner("Đang tải..."):
                                        tc = get_transcript(video["video_id"], lang="vi")
                                    st.text_area("Nội dung", value=tc["full_text"], height=200, key=f"tc_text_{video['video_id']}")
                                except Exception as e:
                                    st.error(f"❌ {e}")

                    st.divider()

        except YouTubeClientError as error:
            st.error(f"Lỗi YouTube API: {error.detail}")
        except Exception as error:
            st.error(f"Có lỗi: {error}")