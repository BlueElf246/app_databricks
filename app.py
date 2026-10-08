import streamlit as st

from database import (
    get_saved_videos,
    init_db,
    log_search_results,
    upsert_channels,
    upsert_comments,
    upsert_videos,
)
from youtube_client import (
    YouTubeClientError,
    get_channels,
    get_comment_threads,
    get_video_details,
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

if "search_videos_result" not in st.session_state:
    st.session_state["search_videos_result"] = []
if "loaded_comments" not in st.session_state:
    st.session_state["loaded_comments"] = {}

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
                st.session_state["search_videos_result"] = []
            else:
                # search.list is discovery-only (snippet, no statistics) — log it,
                # then pull full details per video from videos.list below.
                search_entries = []
                video_ids = []
                for item in items:
                    video_id = item.get("id", {}).get("videoId")
                    snippet = item.get("snippet", {})
                    if not video_id:
                        continue
                    video_ids.append(video_id)
                    search_entries.append({
                        "video_id": video_id,
                        "channel_id": snippet.get("channelId"),
                        "title": snippet.get("title"),
                        "published_at": snippet.get("publishedAt"),
                        "search_keyword": query,
                    })

                details = get_video_details(video_ids)
                videos = [details[vid] for vid in video_ids if vid in details]
                for video in videos:
                    video["video_url"] = f"https://www.youtube.com/watch?v={video['video_id']}"

                channel_ids = list({v["channel_id"] for v in videos if v.get("channel_id")})
                channels = get_channels(channel_ids)

                upsert_channels(list(channels.values()))
                upsert_videos(videos)
                log_search_results(search_entries)

                st.session_state["search_videos_result"] = videos

        except YouTubeClientError as error:
            st.error(f"Lỗi YouTube API: {error.detail}")
        except Exception as error:
            st.error(f"Có lỗi: {error}")

# Rendered from session_state (not gated behind the search button) so that
# clicking "Lấy bình luận" below — which also triggers a rerun — doesn't
# wipe the results back to the pre-search screen.
videos = st.session_state["search_videos_result"]

if videos:
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
            comment_count = video.get("comment_count")
            st.write(
                f"**Bình luận:** {comment_count:,}"
                if comment_count is not None
                else "**Bình luận:** Không có dữ liệu"
            )
            st.write(
                f"**Ngày đăng:** {video.get('published_at', 'Không rõ')}"
            )

            video_url = video.get("video_url")
            if video_url:
                st.link_button("Xem video trên YouTube", video_url)

            with st.expander(f"📄 Mô tả đầy đủ — {video['video_id']}"):
                st.text(video.get("description") or "Không có mô tả.")

            with st.expander(f"💬 Bình luận — {video['video_id']}"):
                if st.button("Lấy bình luận", key=f"cm_{video['video_id']}"):
                    try:
                        with st.spinner("Đang tải bình luận..."):
                            video_comments = get_comment_threads(video["video_id"])
                            upsert_comments(video_comments)
                        st.session_state["loaded_comments"][video["video_id"]] = video_comments
                    except YouTubeClientError as e:
                        st.error(f"❌ {e.detail}")

                loaded = st.session_state["loaded_comments"].get(video["video_id"])
                if loaded is not None:
                    if not loaded:
                        st.info("Video này không có bình luận.")
                    else:
                        for c in loaded:
                            st.write(
                                f"**{c.get('author_display_name', 'Ẩn danh')}** "
                                f"({c.get('like_count', 0)} lượt thích): "
                                f"{c.get('text', '')}"
                            )

        st.divider()
