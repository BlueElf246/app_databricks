"""Search ingest: run the on-demand search job (YouTube or Google Places) with a form.

The page only starts the job (Jobs API run-now with job parameters); the job does
the API calls and writes Lakebase, so runs have logs, the Places quota guard and
the same dry-run switch as the job. Needs env SEARCH_JOB_ID (app resource
search-job, permission CAN_MANAGE_RUN).
"""
import os

import streamlit as st
from databricks.sdk import WorkspaceClient

from database import get_search_runs

st.set_page_config(page_title="Search ingest", page_icon="📥", layout="wide")
st.title("📥 Search ingest")
st.caption(
    "Chạy job tìm kiếm theo yêu cầu: YouTube (video review) hoặc Google Maps (Places Text Search). "
    "Kết quả vào Lakebase ngay, vào Gold ở lần chạy job daily kế tiếp. "
    "Google Maps giới hạn 800 call / tháng (1 call = 20 quán)."
)

job_id = os.getenv("SEARCH_JOB_ID")
if not job_id:
    st.error("Chưa cấu hình SEARCH_JOB_ID (resource search-job của app).")
    st.stop()

MODES = {"youtube": "YouTube", "places": "Google Maps (Places API)"}
WRITE_OPTIONS = {
    "default": "Theo mặc định của job (ghi vào Lakebase)",
    "true": "Dry run: gọi API, không ghi",
    "false": "Ghi vào Lakebase",
}

with st.form("search"):
    mode = st.radio("Tìm trên", options=list(MODES), format_func=MODES.get, horizontal=True)
    keyword = st.text_input("Từ khóa", placeholder="Ví dụ: quán sushi")
    col1, col2 = st.columns(2)
    with col1:
        max_results = st.selectbox(
            "Số kết quả", options=[10, 20, 25, 40, 50, 60], index=1,
            help="YouTube tối đa 50; Google Maps làm tròn lên trang 20 quán, tối đa 60.",
        )
        order = st.selectbox("YouTube: sắp xếp", options=["relevance", "date", "viewCount"])
    with col2:
        location = st.text_input(
            "Google Maps: ưu tiên khu vực (lat,lng,bán kính m)", value="10.7769,106.7009,20000",
            help="Mặc định trung tâm TP.HCM, bán kính 20 km. Để trống = không ưu tiên khu vực.",
        )
        included_type = st.text_input("Google Maps: loại địa điểm", placeholder="restaurant (để trống = tất cả)")
    dry_run = st.radio("Ghi dữ liệu", options=list(WRITE_OPTIONS), format_func=WRITE_OPTIONS.get)
    submitted = st.form_submit_button("Chạy", type="primary")

if submitted:
    if not keyword.strip():
        st.warning("Nhập từ khóa trước khi chạy.")
    elif mode == "youtube" and max_results > 50:
        st.warning("YouTube trả tối đa 50 kết quả.")
    else:
        params = {
            "mode": mode,
            "keyword": keyword.strip(),
            "max_results": str(max_results),
            "order": order,
            "location": location.strip(),
            "included_type": included_type.strip(),
        }
        if dry_run != "default":
            params["dry_run"] = dry_run
        try:
            w = WorkspaceClient()
            run = w.jobs.run_now(job_id=int(job_id), job_parameters=params)
            run_info = w.jobs.get_run(run.run_id)
            st.success(f"Đã chạy job (run {run.run_id}). Thường mất 1-2 phút.")
            if run_info.run_page_url:
                st.link_button("Mở run trên Databricks", run_info.run_page_url)
        except Exception as error:  # noqa: BLE001 - show any API error on the page
            st.error(f"Không chạy được job: {error}")

st.subheader("Các lần search gần đây")
if st.button("🔄 Tải lại"):
    st.rerun()
try:
    runs = get_search_runs(limit=20)
except Exception as error:  # noqa: BLE001 - table appears after the first real run
    runs = []
    st.info(f"Chưa có lần search nào được ghi ({error.__class__.__name__}).")
if runs:
    st.dataframe(runs, use_container_width=True, hide_index=True)
else:
    st.caption("Dry run không được ghi vào lịch sử.")
