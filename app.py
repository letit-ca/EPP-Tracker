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
    c.execute('''CREATE TABLE IF NOT EXISTS bills (bill_date TEXT PRIMARY KEY, usage_val REAL, paid_val REAL, balance_val REAL)''')
    conn.commit()
    conn.close()

def save_to_db(data):
    if data['Date'] == "Unknown":
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Use REPLACE to overwrite existing data for the same date, preventing duplicates
    c.execute("INSERT OR REPLACE INTO bills (bill_date, usage_val, paid_val, balance_val) VALUES (?, ?, ?, ?)", 
              (data['Date'].strftime('%Y-%m-%d'), 
               data['Actual Usage ($)'], 
               data['Budget Paid ($)'], 
               data['EPP Balance ($)']))
    conn.commit()
    conn.close()

def load_from_db():
    conn = sqlite3.connect(DB_PATH)
    # Group by bill_date to ensure we only get one record per date even if the underlying schema allowed duplicates
    df = pd.read_sql_query("SELECT bill_date, usage_val, paid_val, balance_val FROM bills GROUP BY bill_date", conn)
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
    
    # --- ADMIN CONTROLS ---
    st.divider()
    admin_key = st.text_input("Admin Access", type="password", help="Enter key to upload bills or clear data")
    
    # Safely handle missing secrets file on local host
    try:
        correct_password = st.secrets.get("ADMIN_PASSWORD", "admin")
    except Exception:
        correct_password = "admin"

    if admin_key == correct_password:
        st.subheader("Admin Tools")
        
        # Moved Upload to Sidebar
        files = st.file_uploader("Upload Alectra PDF Bills", type="pdf", accept_multiple_files=True)

        if files:
            for f in files:
                data = parse_bill(f)
                if data: 
                    save_to_db(data)
            st.success(f"Processed {len(files)} file(s).")
            st.rerun()

        # Moved Clear Data to Sidebar inside Admin block
        if st.button("🗑️ Clear All Data", help="Permanently delete all stored bill data"):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DROP TABLE IF EXISTS bills")
            conn.close()
            init_db()
            st.rerun()

db_df = load_from_db()

if not db_df.empty:
    df = db_df.copy()
    df.columns = ["Date", "Actual Usage ($)", "Budget Paid ($)", "EPP Balance ($)"]
    df['Date'] = pd.to_datetime(df['Date'])
    df['Month'] = df['Date'].dt.strftime("%b %Y")
    df = df.sort_values("Date", ascending=False)
    
    # Calculate additional metrics
    df['Usage vs Budget'] = df['Actual Usage ($)'] - df['Budget Paid ($)']
    df['Variance %'] = (df['Usage vs Budget'] / df['Budget Paid ($)'] * 100).round(1)
    df['MoM Change $'] = df['Actual Usage ($)'].diff(-1).round(2)
    df['MoM Change %'] = (df['Actual Usage ($)'].pct_change(-1) * 100).round(1)
    
    # --- 0. STATUS BANNER ---
    latest = df.iloc[0]  # First row is newest (descending sort)
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
    
    # Logic for periods (df is now descending)
    previous = df.iloc[1] if len(df) > 1 else None
    
    # Calendar YTD (Current Year)
    current_year = latest['Date'].year
    ytd_cal_df = df[df['Date'].dt.year == current_year]
    
    # YoY (Last Year same month)
    yoy_target_date = latest['Date'] - pd.DateOffset(years=1)
    yoy_match = df[df['Date'].dt.to_period('M') == yoy_target_date.to_period('M')]
    yoy_record = yoy_match.iloc[0] if not yoy_match.empty else None
    
    # 3-month trend
    recent_3m = df.head(3)['Actual Usage ($)'].mean() if len(df) >= 3 else df.iloc[0]['Actual Usage ($)']
    prior_3m = df.iloc[3:6]['Actual Usage ($)'].mean() if len(df) >= 6 else (df.iloc[1]['Actual Usage ($)'] if len(df) > 1 else recent_3m)
    trend_direction = "📈" if recent_3m > prior_3m else "📉" if recent_3m < prior_3m else "➡️"
    trend_pct = ((recent_3m - prior_3m) / prior_3m * 100) if prior_3m > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    
    with m1:
        st.markdown("**Previous Month**")
        if previous is not None:
            prev_var = previous['Usage vs Budget']
            var_color = "🔴" if prev_var > 0 else "🟢"
            st.metric(label=previous['Month'], value=f"${previous['Actual Usage ($)']:,.2f}")
            st.caption(f"Budget: ${previous['Budget Paid ($)']:,.2f} | {var_color} ${prev_var:+,.2f}")
        else:
            st.write("N/A")

    with m2:
        st.markdown("**Current Month**")
        curr_var = latest['Usage vs Budget']
        var_color = "🔴" if curr_var > 0 else "🟢"
        st.metric(label=latest['Month'], value=f"${latest['Actual Usage ($)']:,.2f}")
        st.caption(f"Budget: ${latest['Budget Paid ($)']:,.2f} | {var_color} ${curr_var:+,.2f}")

    with m3:
        st.markdown("**3-Month Trend**")
        if prior_3m > 0:
            st.metric(label="vs Prior Quarter", value=f"${recent_3m:,.2f}", 
                      delta=f"{trend_pct:+.1f}% {trend_direction}", delta_color="inverse")
        else:
            st.write("Insufficient data")

    with m4:
        st.markdown("**All-Time Stats**")
        start_date = df['Date'].min().strftime('%b %Y')
        st.metric(label="Total Spend", value=f"${df['Actual Usage ($)'].sum():,.2f}")
        st.caption(f"From: {start_date} | Avg: ${df['Actual Usage ($)'].mean():,.2f}/mo")

    st.divider()

    col1, col2 = st.columns([1, 1.2])

    # --- 2. ENBRIDGE-STYLE SUMMARY CARD ---
    # This reflects the current EPP Cycle (first 12 months for descending order)
    ytd_df = df.head(12)
    ytd_actual_charges = ytd_df['Actual Usage ($)'].sum()
    ytd_budget_charges = ytd_df['Budget Paid ($)'].sum()

    with col1:
        st.subheader("📋 EPP Reconciliation Summary")
        with st.container(border=True):
            cycle_start = ytd_df.iloc[-1]['Month'] if len(ytd_df) > 1 else latest['Month']
            cycle_end = latest['Month']
            st.markdown(f"**Cycle View:** {cycle_start} to {cycle_end} (most recent)")
            st.write("")
            
            # Calendar YTD Info
            ytd_usage = ytd_cal_df['Actual Usage ($)'].sum()
            ytd_start_date = "Jan 1" if len(ytd_cal_df) > 0 else "N/A"
            st.markdown(f"**{current_year} Calendar YTD Spend:** `${ytd_usage:,.2f}` ({ytd_start_date} - {latest_date_str.split(',')[0]})")
            st.markdown("---")
            
            # Row 1
            r1c1, r1c2 = st.columns([3, 1])
            r1c1.write("Total Actual Charges (Current Cycle)")
            r1c2.write(f"**${ytd_actual_charges:,.2f}**")
            
            # Row 1b - Cycle Variance
            r1bc1, r1bc2 = st.columns([3, 1])
            cycle_var = ytd_actual_charges - ytd_budget_charges
            var_indicator = "🔴 Over" if cycle_var > 0 else "🟢 Under"
            r1bc1.write(f"├─ Variance vs Budget")
            r1bc2.write(f"{var_indicator}: ${abs(cycle_var):,.2f}")
            
            # Row 2
            r2c1, r2c2 = st.columns([3, 1])
            r2c1.write("Total EPP Installments Paid")
            r2c2.write(f"**${ytd_budget_charges:,.2f}**")
            
            # Row 3
            r3c1, r3c2 = st.columns([3, 1])
            r3c1.write("Current Month's Installment")
            r3c2.write(f"**${latest['Budget Paid ($)']:,.2f}**")
            
            st.markdown("---")
            
            # Row 4 (Balance)
            r4c1, r4c2 = st.columns([3, 1])
            r4c1.write("### Running Account Balance")
            balance_indicator = "🔴" if bal > 0 else "🟢" if bal < 0 else "⚖️"
            r4c2.write(f"### {balance_indicator} ${bal:,.2f}")

    # --- 3. VOLT'S ADVANCED INSIGHTS ---
    with col2:
        st.subheader("🧠 Intelligence & Forecasting")
        with st.container(border=True):
            avg_usage = ytd_df['Actual Usage ($)'].mean()
            avg_budget = ytd_df['Budget Paid ($)'].mean()
            highest_month = ytd_df.loc[ytd_df['Actual Usage ($)'].idxmax()]
            lowest_month = ytd_df.loc[ytd_df['Actual Usage ($)'].idxmin()]
            
            # Forecasting Logic
            months_tracked = len(ytd_df)
            current_diff = latest['EPP Balance ($)']
            monthly_drift = current_diff / months_tracked if months_tracked > 0 else 0
            projected_true_up = current_diff + (monthly_drift * (12 - months_tracked)) if months_tracked < 12 else current_diff
            budget_efficiency = (avg_budget / avg_usage * 100) if avg_usage > 0 else 100

            st.markdown(f"- **True Cost:** Your actual electricity usage averages **${avg_usage:,.2f}/month** (Budgeted: **${avg_budget:,.2f}/month**).")
            st.markdown(f"- **Budget Efficiency:** Your EPP covers **{budget_efficiency:.1f}%** of average actual usage.")
            
            # Budget Check
            monthly_gap = avg_usage - avg_budget
            if monthly_gap > 0:
                st.warning(f"⚠️ **Monthly Deficit:** Underpaying by **${monthly_gap:,.2f}** per month on average. Alectra may adjust installment.")
            else:
                st.success(f"✓ **Budget Status:** Monthly payment covers usage with **${abs(monthly_gap):,.2f}** cushion.")
                
            st.markdown(f"- **Consumption Range:** Peak **{highest_month['Month']}** (${highest_month['Actual Usage ($)']:,.2f}) | Low **{lowest_month['Month']}** (${lowest_month['Actual Usage ($)']:,.2f})")
            
            # Forecast
            if months_tracked < 12:
                st.info(f"📊 **True-Up Forecast (Month {months_tracked}/12):** Projected balance at cycle end: **${projected_true_up:,.2f}**")
            else:
                st.info(f"✅ **True-Up Status:** Full 12-month cycle. Balance of **${latest['EPP Balance ($)']:,.2f}** is final for true-up.")

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
    with st.expander("📊 View Full Reconciliation Table", expanded=True):
        display_df = df[['Date', 'Month', 'Actual Usage ($)', 'Budget Paid ($)', 'Usage vs Budget', 'Variance %', 'EPP Balance ($)', 'MoM Change $', 'MoM Change %']].copy()
        display_df['Date'] = display_df['Date'].dt.strftime("%b %d, %Y")
        
        st.dataframe(
            display_df,
            column_config={
                "Date": st.column_config.TextColumn(label="Invoice Date"),
                "Month": st.column_config.TextColumn(label="Period"),
                "Actual Usage ($)": st.column_config.NumberColumn(label="Actual Charge", format="$%.2f"),
                "Budget Paid ($)": st.column_config.NumberColumn(label="EPP Payment", format="$%.2f"),
                "Usage vs Budget": st.column_config.NumberColumn(label="Variance $", format="$%.2f"),
                "Variance %": st.column_config.NumberColumn(label="Variance %", format="%.1f%%"),
                "EPP Balance ($)": st.column_config.NumberColumn(label="Running Balance", format="$%.2f"),
                "MoM Change $": st.column_config.NumberColumn(label="MoM $ Change", format="$%.2f"),
                "MoM Change %": st.column_config.NumberColumn(label="MoM % Change", format="%.1f%%"),
            },
            use_container_width=True,
            hide_index=True
        )
        st.caption("📌 Most recent invoices first. MoM = Month-over-Month change.")
else:
    st.info("No data found. Please upload your Alectra PDF bills to begin tracking.")