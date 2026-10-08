import streamlit as st

from database import get_saved_videos, init_db

st.set_page_config(
    page_title="Dữ liệu đã lưu",
    page_icon="💾",
    layout="wide",
)

st.title("💾 Dữ liệu video đã lưu")
st.caption("Danh sách video trong Lakebase Postgres")

try:
    init_db()
except Exception as e:
    st.error(f"Không thể kết nối tới database: {e}")
    st.stop()

if st.button("🔄 Tải lại dữ liệu"):
    st.rerun()

min_view_count = st.number_input("Lượt xem tối thiểu", min_value=0, value=0)
min_like_count = st.number_input("Lượt thích tối thiểu", min_value=0, value=0)

page = st.number_input("Trang", min_value=1, value=1, step=1)
page_size = st.selectbox("Số video mỗi trang", options=[10, 20, 50, 100])

sort_by = st.selectbox(
    "Sắp xếp theo",
    options=["updated_at", "view_count", "like_count"],
)
sort_order = st.selectbox("Thứ tự", options=["desc", "asc"])

keyword = st.text_input("Tìm trong tiêu đề hoặc mô tả")

try:
    with st.spinner("Đang tải dữ liệu từ database..."):
        data = get_saved_videos(
            page=int(page),
            page_size=page_size,
            min_view_count=min_view_count if min_view_count > 0 else None,
            min_like_count=min_like_count if min_like_count > 0 else None,
            keyword=keyword.strip() if keyword.strip() else None,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    items = data.get("items", [])
    total = data.get("total", 0)
    total_pages = data.get("total_pages", 0)
    current_page = data.get("page", page)

    if not items:
        st.info("Database chưa có video nào.")
    else:
        st.metric("Tổng số video", total)
        st.caption(f"Trang {current_page} / {total_pages}")

        table_data = [
            {
                "Video ID": video.get("video_id"),
                "Tiêu đề": video.get("title"),
                "Kênh": video.get("channel_title"),
                "Lượt xem": video.get("view_count"),
                "Lượt thích": video.get("like_count"),
                "Bình luận": video.get("comment_count"),
                "Ngày đăng": video.get("published_at"),
                "Cập nhật": str(video.get("updated_at", "")),
            }
            for video in items
        ]

        st.dataframe(table_data, hide_index=True, use_container_width=True)

        st.subheader("Chi tiết video")

        for index, video in enumerate(items, start=1):
            title = video.get("title") or "Không có tiêu đề"

            with st.expander(f"{index}. {title}"):
                left, right = st.columns([1, 2])

                with left:
                    thumbnail_url = video.get("thumbnail_url")
                    if thumbnail_url:
                        st.image(thumbnail_url, use_container_width=True)

                with right:
                    st.write(f"**Video ID:** {video.get('video_id', 'Không rõ')}")
                    st.write(f"**Kênh:** {video.get('channel_title', 'Không rõ')}")
                    st.write(f"**Lượt xem:** {video.get('view_count', 'Không có dữ liệu')}")
                    st.write(f"**Lượt thích:** {video.get('like_count', 'Không có dữ liệu')}")
                    st.write(f"**Bình luận:** {video.get('comment_count', 'Không có dữ liệu')}")
                    st.write(f"**Cập nhật lần cuối:** {str(video.get('updated_at', 'Không rõ'))}")

                    video_url = video.get("video_url")
                    if video_url:
                        st.link_button("Xem trên YouTube", video_url)

except Exception as error:
    st.error(f"Không thể tải dữ liệu: {error}")