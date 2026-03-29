"""
Ứng dụng tính kWh tiêu thụ từ hóa đơn điện (bậc thang + VAT) và
ước lượng công suất pin / inverter tối thiểu theo công thức file DENERGY.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

_BASE_DIR = Path(__file__).resolve().parent
LOGO_FILE = _BASE_DIR / "Annotation 2026-03-25 194156.png"
BACKGROUND_FILE = _BASE_DIR / "anh nen.jpg"


def _inject_background_css(image_path: Path) -> None:
    if not image_path.is_file():
        return
    raw = image_path.read_bytes()
    mime = "image/jpeg"
    if image_path.suffix.lower() in (".png",):
        mime = "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: linear-gradient(
                rgba(255, 255, 255, 0.78),
                rgba(255, 255, 255, 0.78)
            ), url("data:{mime};base64,{b64}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        [data-testid="stSidebar"] {{
            background-color: rgba(250, 250, 250, 0.92);
        }}
        .main .block-container {{
            background-color: rgba(255, 255, 255, 0.96);
            padding: 1.5rem 1.25rem;
            border-radius: 0.5rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

# --- Bậc giá điện (đồng/kWh) — khớp sheet "Tool" trong DENERGY_tool tinh toan.xlsx ---
# Mỗi phần tử: (số kWh tối đa của bậc, đơn giá). Bậc cuối không giới hạn.
TIER_WIDTHS_AND_PRICES: list[tuple[float, int]] = [
    (50, 1984),  # 0–50
    (50, 2050),  # 51–100
    (100, 2380),  # 101–200
    (100, 2998),  # 201–300
    (100, 3350),  # 301–400
]
PRICE_ABOVE_400 = 3460

DEFAULT_VAT = 0.08  # 8% — khớp ví dụ 4 149 900 / 3 842 500 trong file
DEFAULT_SUN_H = 5.0
DEFAULT_EFF_PCT = 90.0
# Tỷ lệ kWp / kW inverter từ file (8.82 / 8) — khi inverter là giới hạn AC
DEFAULT_KP_W_RATIO = 1.24
DEFAULT_DAYS_MONTH = 30
# Hệ số điều chỉnh công suất inverter để fit thực tế
adjust_factor = st.number_input(
    "Hệ số hiệu chỉnh inverter",
    min_value=0.5,
    max_value=1.2,
    value=0.8,
    step=0.05,
    help="Giảm/increase công suất inverter để fit thực tế"
)

def electricity_cost_pretax_vnd(kwh: float) -> float:
    """Tổng tiền điện chưa thuế (VNĐ) theo bậc thang."""
    kwh = max(0.0, float(kwh))
    total = 0.0
    remaining = kwh
    for width, price in TIER_WIDTHS_AND_PRICES:
        if remaining <= 0:
            break
        take = min(remaining, width)
        total += take * price
        remaining -= take
    if remaining > 0:
        total += remaining * PRICE_ABOVE_400
    return total


def electricity_cost_after_tax_vnd(kwh: float, vat_rate: float) -> float:
    return electricity_cost_pretax_vnd(kwh) * (1.0 + vat_rate)


def solve_kwh_from_bill(
    bill_vnd: float, vat_rate: float, bill_includes_vat: bool
) -> float | None:
    """
    Tìm kWh sao cho tiền (sau thuế nếu bill_includes_vat, hoặc chưa thuế nếu không)
    khớp bill_vnd.
    """
    if bill_vnd < 0:
        return None
    if bill_vnd == 0:
        return 0.0

    def target_at(k: float) -> float:
        pretax = electricity_cost_pretax_vnd(k)
        if bill_includes_vat:
            return pretax * (1.0 + vat_rate)
        return pretax

    lo, hi = 0.0, 1.0
    while target_at(hi) < bill_vnd:
        hi *= 2
        if hi > 1e9:
            return None

    for _ in range(80):
        mid = (lo + hi) / 2
        if target_at(mid) < bill_vnd:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def min_inverter_kw_and_kwp(
    monthly_kwh: float,
    sun_hours_per_day: float,
    efficiency_percent: float,
    days_per_month: float,
    kwp_per_kw_inverter: float,
) -> tuple[float, float, float]:
    """
    Theo file: sản lượng/ngày = min(kWp, kW inverter) × giờ nắng × (hiệu suất/100).

    Giả sử thiết kế với kWp = tỷ_lệ × P_inv và tỷ_lệ > 1 → min = P_inv.
    Khi đó P_inv tối thiểu = (điện tháng / số ngày) / (giờ nắng × hiệu suất),
    kWp tối thiểu = P_inv × tỷ_lệ.

    Nếu tỷ_lệ = 1, hai giá trị bằng nhau (không kẹp inverter).
    """
    if sun_hours_per_day <= 0 or efficiency_percent <= 0 or days_per_month <= 0:
        return float("nan"), float("nan"), float("nan")

    daily_need = monthly_kwh / days_per_month
    eta = efficiency_percent / 100.0
    p_req = daily_need / (sun_hours_per_day * eta) * adjust_factor

    r = max(kwp_per_kw_inverter, 1e-9)
    if r >= 1.0:
        p_inv = p_req
        kwp = p_inv * r
    else:
        # r < 1: panel nhỏ hơn inverter → min = kWp
        kwp = p_req
        p_inv = kwp / r

    return p_inv, kwp, daily_need


def main() -> None:
    st.set_page_config(page_title="Điện mặt trời áp mái — DENERGY", layout="wide")
    _inject_background_css(BACKGROUND_FILE)
    if not BACKGROUND_FILE.is_file():
        st.warning(f"Không tìm thấy ảnh nền: {BACKGROUND_FILE.name}")

    title_col, logo_col = st.columns([4, 1])
    with title_col:
        st.title("DENERGY-Tính toán hệ thống điện mặt trời áp mái")
        st.caption(
            "Số kWh suy ra từ giá bậc thang + VAT; công suất pin / inverter theo "
            "công thức: min(kWp, kW inverter) × giờ nắng × hiệu suất (file DENERGY)."
        )
    with logo_col:
        if LOGO_FILE.is_file():
            st.image(str(LOGO_FILE), use_container_width=True)
        else:
            st.caption(f"Không tìm thấy logo: {LOGO_FILE.name}")

    with st.sidebar:
        st.header("Tham số")
        vat_pct = st.number_input(
            "Thuế VAT (%)",
            min_value=0.0,
            max_value=30.0,
            value=DEFAULT_VAT * 100,
            step=0.1,
            help="Ví dụ trong file Excel: 8%",
        )
        vat_rate = vat_pct / 100.0

        st.subheader("Thông số sản lượng điện mặt trời")
        sun_h = st.number_input(
            "Số giờ nắng trung bình (h/ngày)",
            min_value=0.1,
            max_value=16.0,
            value=DEFAULT_SUN_H,
            step=0.1,
            help="Ghi chú file: khoảng 4,5–5,5",
        )
        eff_pct = st.number_input(
            "Hiệu suất hệ thống (%)",
            min_value=1.0,
            max_value=100.0,
            value=DEFAULT_EFF_PCT,
            step=0.5,
            help="Ghi chú file: khoảng 80–95%",
        )
        days_m = st.number_input(
            "Số ngày trong tháng (chia điện năng tiêu thụ)",
            min_value=1.0,
            max_value=31.0,
            value=float(DEFAULT_DAYS_MONTH),
            step=1.0,
        )
        ratio = st.number_input(
            "Tỷ lệ kWp / kW inverter (DC/AC)",
            min_value=0.1,
            max_value=3.0,
            value=float(DEFAULT_KP_W_RATIO),
            step=0.01,
            help="Ví dụ file: 8,82 / 8 = 1,1025. Dùng để suy ra kWp khi inverter là giới hạn.",
        )

    col1, col2 = st.columns(2)
    with col1:
        bill_includes_vat = st.radio(
            "Loại số tiền nhập",
            options=[True, False],
            format_func=lambda x: "Đã gồm VAT (sau thuế)" if x else "Chưa thuế (trước thuế)",
            horizontal=True,
        )
        bill = st.number_input(
            "Số tiền điện hàng tháng (đồng)",
            min_value=0.0,
            value=4_149_900.0 if bill_includes_vat else 3_842_500.0,
            step=1000.0,
            format="%.0f",
        )

    pretax = bill / (1.0 + vat_rate) if bill_includes_vat else bill
    after_tax = pretax * (1.0 + vat_rate)
    kwh_est = solve_kwh_from_bill(bill, vat_rate, bill_includes_vat)

    with col2:
        st.metric("Tiền điện chưa thuế (ước tính)", f"{pretax:,.0f} đ")
        st.metric("Tiền điện sau thuế (ước tính)", f"{after_tax:,.0f} đ")
        if kwh_est is not None:
            st.metric("Sản lượng điện tiêu thụ (ước tính)", f"{kwh_est:,.2f} kWh/tháng")

    if kwh_est is None:
        st.error("Không tìm được mức tiêu thụ phù hợp với số tiền đã nhập.")
        return

    check_after = electricity_cost_after_tax_vnd(kwh_est, vat_rate)
    st.caption(
        f"Kiểm tra ngược từ {kwh_est:.2f} kWh — chưa thuế: {electricity_cost_pretax_vnd(kwh_est):,.2f} đ; "
        f"sau thuế: {check_after:,.2f} đ"
    )

    p_inv, kwp, daily_need = min_inverter_kw_and_kwp(
        kwh_est, sun_h, eff_pct, days_m, ratio
    )

    st.divider()
    st.subheader("Ước lượng công suất lắp đặt tối thiểu (offset tiêu thụ trung bình/ngày)")
    c1, c2, c3 = st.columns(3)
    c1.metric("Công suất inverter tối thiểu", f"{p_inv:,.3f} kW")
    c2.metric("Công suất pin (kWp) tối thiểu", f"{kwp:,.3f} kWp")
    c3.metric("Nhu cầu trung bình/ngày", f"{daily_need:,.2f} kWh/ngày")

    daily_gen = min(kwp, p_inv) * sun_h * (eff_pct / 100.0) * 1.2
    st.info(
        f"Sản lượng ước tính/ngày với cặp (kWp, inverter) trên: **{daily_gen:,.2f} kWh/ngày** "
        f"(min({kwp:.3f}, {p_inv:.3f}) × {sun_h} × {eff_pct/100:.3f})."
    )

    with st.expander("Bảng giá bậc thang (tham khảo)"):
        st.table(
            [
                {"Bậc": "1", "kWh": "0 – 50", "Giá (đ/kWh)": "1 984"},
                {"Bậc": "2", "kWh": "51 – 100", "Giá (đ/kWh)": "2 050"},
                {"Bậc": "3", "kWh": "101 – 200", "Giá (đ/kWh)": "2 380"},
                {"Bậc": "4", "kWh": "201 – 300", "Giá (đ/kWh)": "2 998"},
                {"Bậc": "5", "kWh": "301 – 400", "Giá (đ/kWh)": "3 350"},
                {"Bậc": "6", "kWh": "> 400", "Giá (đ/kWh)": "3 460"},
            ]
        )


if __name__ == "__main__":
    main()
