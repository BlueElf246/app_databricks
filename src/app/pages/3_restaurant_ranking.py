import streamlit as st

from gold_client import get_cities, get_restaurant_ranking, get_restaurant_videos

st.set_page_config(
    page_title="Xếp hạng quán ăn",
    page_icon="🏆",
    layout="wide",
)

st.title("🏆 Xếp hạng quán ăn")
st.caption(
    "Đọc từ Gold model (`prod.gold.agg_restaurant_ranking`), cập nhật mỗi ngày lúc 01:00. "
    "Điểm = 0.7 × lượt xem tăng trong 30 ngày + 0.3 × tỷ lệ thích, chuẩn hóa 0–1 trong từng thành phố; "
    "lượt xem của một video được chia đều cho các quán trong video đó."
)

try:
    cities = get_cities()
except Exception as error:
    st.error(f"Không thể kết nối tới Gold model: {error}")
    st.info("Cần quyền CAN USE trên SQL warehouse và SELECT trên prod.gold.")
    st.stop()

default_city = cities.index("Hồ Chí Minh") + 1 if "Hồ Chí Minh" in cities else 0
city = st.selectbox("Thành phố", options=[None, *cities], index=default_city,
                    format_func=lambda value: "Tất cả" if value is None else value)
keyword = st.text_input("Tìm theo tên quán hoặc địa chỉ")
limit = st.selectbox("Số quán hiển thị", options=[20, 50, 100], index=1)

try:
    with st.spinner("Đang tải xếp hạng..."):
        restaurants = get_restaurant_ranking(city=city, keyword=keyword.strip() or None, limit=limit)
except Exception as error:
    st.error(f"Không thể tải xếp hạng: {error}")
    st.stop()

if not restaurants:
    st.info("Không có quán nào khớp.")
    st.stop()

as_of = restaurants[0]["as_of_date_key"]
st.caption(f"Dữ liệu tính đến ngày {as_of[:4]}-{as_of[4:6]}-{as_of[6:]}")

st.dataframe(
    [
        {
            "Hạng": r["rank_in_city"],
            "Quán": r["restaurant_name"],
            "Địa chỉ": r["address"],
            "Quận": r["district"],
            "Thành phố": r["city"],
            "Số video": r["video_count"],
            "Lượt xem (chia)": r["credited_views"],
            "Lượt xem 30 ngày": r["credited_views_30d"],
            "Tỷ lệ thích": f"{r['like_rate']:.2%}" if r["like_rate"] is not None else None,
            "Điểm": round(r["ranking_score"], 3) if r["ranking_score"] is not None else None,
        }
        for r in restaurants
    ],
    hide_index=True,
    width="stretch",
)

st.subheader("Video nhắc tới quán")
for r in restaurants[:20]:
    with st.expander(f"#{r['rank_in_city']} {r['restaurant_name']} — {r['district'] or r['city']}"):
        for video in get_restaurant_videos(r["restaurant_id"]):
            views = f"{video['view_count']:,}" if video["view_count"] is not None else "?"
            st.markdown(f"- [{video['title']}]({video['video_url']}) · {views} lượt xem · "
                        f"chia {video['mention_weight']:.0%} lượt xem cho quán này")
