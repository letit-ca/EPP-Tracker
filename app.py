import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime
import plotly.express as px
import sqlite3

# --- CONFIGURATION ---
st.set_page_config(page_title="Volt: Alectra Budget Tracker", layout="wide", page_icon="⚡")

# --- DATABASE LOGIC ---
DB_PATH = "bills_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS bills
                 (bill_date TEXT, usage_val REAL, paid_val REAL, balance_val REAL, 
                  month_str TEXT, filename TEXT, 
                  UNIQUE(bill_date, filename))''')
    conn.commit()
    conn.close()

def save_to_db(data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO bills VALUES (?, ?, ?, ?, ?, ?)", 
                  (data['Date'].strftime('%Y-%m-%d') if isinstance(data['Date'], datetime) else "Unknown", 
                   data['Actual Usage ($)'], 
                   data['Budget Paid ($)'], 
                   data['EPP Balance ($)'], 
                   data['Month'], 
                   data['FileName']))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def load_from_db():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM bills", conn)
    conn.close()
    return df

def parse_bill(pdf_file):
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = "".join([page.extract_text() for page in pdf.pages])
            
            date_pattern = r"(?:Statement|Bill|Issue|Budget)\s*Date[:\s]+([a-zA-Z]+\s+\d{1,2},?\s+\d{4})"
            date_match = re.search(date_pattern, text, re.IGNORECASE)
            bill_date = None
            if date_match:
                date_str = date_match.group(1).replace(',', '').strip()
                for fmt in ("%b %d %Y", "%B %d %Y"):
                    try:
                        bill_date = datetime.strptime(date_str, fmt)
                        break
                    except: continue

            actual_balance_match = re.search(r"Total Actual Balance.*?\s*\$?([\d,]+\.\d{2})(.*)", text, re.IGNORECASE)
            budget_amount_match = re.search(r"Budget Amount.*?\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)
            actual_usage_match = re.search(r"Total Current Bill.*?\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)

            if not actual_balance_match:
                return None

            bal_val = float(actual_balance_match.group(1).replace(',', ''))
            is_credit = "CR" in actual_balance_match.group(2).upper()
            
            return {
                "Date": bill_date if bill_date else "Unknown",
                "Actual Usage ($)": float(actual_usage_match.group(1).replace(',', '')) if actual_usage_match else 0.0,
                "Budget Paid ($)": float(budget_amount_match.group(1).replace(',', '')) if budget_amount_match else 0.0,
                "EPP Balance ($)": -bal_val if is_credit else bal_val,
                "Month": bill_date.strftime("%b %Y") if bill_date else "Unknown",
                "FileName": pdf_file.name
            }
    except Exception:
        return None

# --- INITIALIZE ---
init_db()

# --- DASHBOARD UI ---
st.title("⚡ Alectra Budget Billing Tracker")
st.markdown("Reconcile your Equal Payment Plan history and monitor your 'True-up' risk.")

with st.sidebar:
    st.header("What is the Running Balance?")
    st.info("""
    The **Running Account Balance** is the cumulative difference between your 
    **actual electricity usage charges** and the **EPP installments** you have paid.
    
    - **Positive Balance (DEBT):** You have used more electricity than you've paid for.
    - **Negative Balance (CREDIT):** You have paid more than you've used.
    """)

files = st.file_uploader("Upload Alectra PDF Bills", type="pdf", accept_multiple_files=True)

if files:
    for f in files:
        data = parse_bill(f)
        if data: save_to_db(data)
    st.success(f"Processed {len(files)} file(s).")

db_df = load_from_db()

if not db_df.empty:
    df = db_df.copy()
    df.columns = ["Date", "Actual Usage ($)", "Budget Paid ($)", "EPP Balance ($)", "Month", "FileName"]
    df['Date'] = pd.to_datetime(df['Date'])
    df = df[df['Date'] != "Unknown"].sort_values("Date")
    
    # --- 0. STATUS BANNER ---
    latest = df.iloc[-1]
    latest_date_str = latest['Date'].strftime('%B %d, %Y')
    bal = latest['EPP Balance ($)']

    if bal > 0:
        st.error(f"### 🚩 As of {latest_date_str}: You owe Alectra **${bal:,.2f}**")
    elif bal < 0:
        st.success(f"### ✅ As of {latest_date_str}: Alectra owes you a credit of **${abs(bal):,.2f}**")
    else:
        st.info(f"### ⚖️ As of {latest_date_str}: Your account is perfectly balanced ($0.00)")
    
    st.divider()

    # --- 1. PERIOD COMPARISONS ---
    st.subheader("📊 Comparison & Historical Benchmarks")
    
    # Logic for periods
    previous = df.iloc[-2] if len(df) > 1 else None
    
    # Calendar YTD (Current Year)
    current_year = latest['Date'].year
    ytd_cal_df = df[df['Date'].dt.year == current_year]
    
    # YoY (Last Year same month)
    yoy_target_date = latest['Date'] - pd.DateOffset(years=1)
    yoy_match = df[df['Date'].dt.to_period('M') == yoy_target_date.to_period('M')]
    yoy_record = yoy_match.iloc[0] if not yoy_match.empty else None

    m1, m2, m3, m4 = st.columns(4)
    
    with m1:
        st.markdown("**Previous Month**")
        if previous is not None:
            st.metric(label=previous['Month'], value=f"${previous['Actual Usage ($)']:,.2f}")
            st.caption(f"Budget: ${previous['Budget Paid ($)']:,.2f}")
        else:
            st.write("N/A")

    with m2:
        st.markdown("**Current Month**")
        st.metric(label=latest['Month'], value=f"${latest['Actual Usage ($)']:,.2f}")
        st.caption(f"Budget: ${latest['Budget Paid ($)']:,.2f}")

    with m3:
        st.markdown("**Year-over-Year**")
        if yoy_record is not None:
            diff = latest['Actual Usage ($)'] - yoy_record['Actual Usage ($)']
            pct = (diff / yoy_record['Actual Usage ($)']) * 100
            st.metric(label=yoy_record['Month'], value=f"${yoy_record['Actual Usage ($)']:,.2f}", 
                      delta=f"{pct:+.1f}% vs last year", delta_color="inverse")
        else:
            st.write("No data from 1 year ago")

    with m4:
        st.markdown("**All-Time Stats**")
        st.metric(label="Total Spend", value=f"${df['Actual Usage ($)'].sum():,.2f}")
        st.caption(f"Avg: ${df['Actual Usage ($)'].mean():,.2f}/mo")

    st.divider()

    col1, col2 = st.columns([1, 1.2])

    # --- 2. ENBRIDGE-STYLE SUMMARY CARD ---
    # This reflects the current EPP Cycle (last 12 months or total records if < 12)
    ytd_df = df.tail(12)
    ytd_actual_charges = ytd_df['Actual Usage ($)'].sum()
    ytd_previous_installments = ytd_df['Budget Paid ($)'].iloc[:-1].sum() if len(ytd_df) > 1 else 0.0

    with col1:
        st.subheader("📋 EPP Reconciliation Summary")
        with st.container(border=True):
            st.markdown(f"**Cycle View:** {ytd_df.iloc[0]['Month']} to {latest['Month']}")
            st.write("")
            
            # Calendar YTD Info
            ytd_usage = ytd_cal_df['Actual Usage ($)'].sum()
            st.markdown(f"**{current_year} Calendar YTD Spend:** `${ytd_usage:,.2f}`")
            st.markdown("---")
            
            # Row 1
            r1c1, r1c2 = st.columns([3, 1])
            r1c1.write("Total Actual Charges (Current Cycle)")
            r1c2.write(f"**${ytd_actual_charges:,.2f}**")
            
            # Row 2
            r2c1, r2c2 = st.columns([3, 1])
            r2c1.write("EPP Previous Installments")
            r2c2.write(f"**${ytd_previous_installments:,.2f}**")
            
            # Row 3
            r3c1, r3c2 = st.columns([3, 1])
            r3c1.write("This Month's Installment")
            r3c2.write(f"**${latest['Budget Paid ($)']:,.2f}**")
            
            st.markdown("---")
            
            # Row 4 (Balance)
            r4c1, r4c2 = st.columns([3, 1])
            r4c1.write("### EPP Balance")
            r4c2.write(f"### ${bal:,.2f}")

    # --- 3. VOLT'S ADVANCED INSIGHTS ---
    with col2:
        st.subheader("🧠 Intelligence & Forecasting")
        with st.container(border=True):
            avg_usage = ytd_df['Actual Usage ($)'].mean()
            highest_month = ytd_df.loc[ytd_df['Actual Usage ($)'].idxmax()]
            
            # Forecasting Logic
            months_tracked = len(ytd_df)
            current_diff = latest['EPP Balance ($)']
            monthly_drift = current_diff / months_tracked if months_tracked > 0 else 0
            projected_true_up = current_diff + (monthly_drift * (12 - months_tracked)) if months_tracked < 12 else current_diff

            st.markdown(f"- **True Cost:** Your actual electricity usage averages **${avg_usage:,.2f}/month**.")
            
            # Budget Check
            if avg_usage > latest['Budget Paid ($)']:
                st.warning(f"- **Budget Deficit:** You are underpaying by roughly **${(avg_usage - latest['Budget Paid ($)']):,.2f}** every month. Alectra may raise your installment soon.")
            else:
                st.success(f"- **Budget Surplus:** Your monthly payment is safely covering your average usage.")
                
            st.markdown(f"- **Peak Consumption:** Your most expensive month was **{highest_month['Month']}** at **${highest_month['Actual Usage ($)']:,.2f}**.")
            
            # Forecast
            if months_tracked < 12:
                st.info(f"- **True-Up Forecast:** Based on your current trajectory, if Alectra settles your account at month 12, your True-up balance will be approximately **${projected_true_up:,.2f}**.")
            else:
                st.info(f"- **True-Up Status:** You have a full 12 months of data. Your current balance of **${latest['EPP Balance ($)']:,.2f}** is your final true-up amount for this cycle.")

    # --- 4. CHARTS ---
    st.divider()
    st.subheader("📈 Budget vs. Reality Trend")
    fig = px.line(df, x="Date", y=["Actual Usage ($)", "Budget Paid ($)"], 
                  markers=True, title="Monthly Comparison")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("📉 Your Reconciliation Path")
    fig_area = px.area(df, x="Date", y="EPP Balance ($)", 
                       title="Running Balance (Above $0 = You owe Alectra)")
    fig_area.add_hline(y=0, line_dash="dash", line_color="white")
    st.plotly_chart(fig_area, use_container_width=True)

    # --- 5. RAW DATA ---
    with st.expander("View Full Reconciliation Table"):
        st.dataframe(
            df.drop(columns=["
                "Date": st.column_config.DateColumn(label="Invoice Date", format="MMMM DD, YYYY"),
                "Actual Usage ($)": st.column_config.NumberColumn(format="$%.2f"),
                "Budget Paid ($)": st.column_config.NumberColumn(format="$%.2f"),
                "EPP Balance ($)": st.column_config.NumberColumn(format="$%.2f"),
            },
            use_container_width=True,
            hide_index=True
        )
else:
    st.info("No data found. Please upload your Alectra PDF bills to begin tracking.")