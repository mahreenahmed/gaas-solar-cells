import streamlit as st
import pandas as pd
import yaml
import os
import sys
import sqlite3
import json
import time
import traceback          # <-- added
from datetime import datetime
from pathlib import Path
import requests
import tempfile
import shutil

# ----- Fix imports -----
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
modules_dir = os.path.join(parent_dir, "modules")
if modules_dir not in sys.path:
    sys.path.insert(0, modules_dir)

from analyzer import analyze_pdf, get_usage_summary

# ----- Locate config -----
def find_config():
    possible = [os.path.join(parent_dir, "config.yaml"), "config.yaml", os.path.join("..", "config.yaml")]
    for p in possible:
        if os.path.exists(p):
            return p
    return None

config_path = find_config()
if config_path is None:
    st.error("❌ config.yaml not found.")
    st.stop()

with open(config_path) as f:
    config = yaml.safe_load(f)
db_path = config["db"]["path"]


# ============================================================
# API HEALTH CHECK
# ============================================================
def check_api_connection():
    """Test the DeepSeek API connection with a minimal ping request."""
    api_key = config.get("deepseek", {}).get("api_key")
    base_url = config.get("deepseek", {}).get("base_url", "https://api.deepseek.com/v1/chat/completions")
    
    if not api_key:
        return False, "❌ API key not found in config.yaml"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1
    }
    
    try:
        response = requests.post(base_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return True, "✅ API Connected"
        elif response.status_code == 403:
            return False, "❌ 403 Forbidden: Invalid API Key or insufficient permissions"
        elif response.status_code == 401:
            return False, "❌ 401 Unauthorized: Invalid credentials"
        elif response.status_code == 429:
            return False, "❌ 429 Rate Limit Exceeded: Too many requests"
        elif response.status_code == 402:
            return False, "❌ 402 Payment Required: Please check your DeepSeek balance"
        else:
            return False, f"❌ Error {response.status_code}: {response.text[:100]}"
            
    except requests.exceptions.Timeout:
        return False, "❌ Connection timeout: API unreachable"
    except requests.exceptions.ConnectionError:
        return False, "❌ Connection error: Check your internet or API endpoint"
    except Exception as e:
        return False, f"❌ Unexpected error: {str(e)[:100]}"


# ----- Constants -----
ARTICLES_BASE = os.path.join(parent_dir, "articles")
UPLOAD_DIR = os.path.join(ARTICLES_BASE, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================
# DATABASE HELPERS
# ============================================================
def get_connection():
    """Return a new connection with increased timeout and busy handler."""
    conn = sqlite3.connect(db_path, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def ensure_material_exists(conn, material_name):
    """Get material ID; create if missing."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM materials WHERE english_name = ?", (material_name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    else:
        cursor.execute(
            "INSERT INTO materials (english_name, chinese_name, formula) VALUES (?, ?, ?)",
            (material_name, "", "")
        )
        conn.commit()
        return cursor.lastrowid


# ----- Property Category Mapping -----
PROPERTY_CATEGORY_MAP = {
    # Mechanics
    "elastic modulus": "Mechanics",
    "shear modulus": "Mechanics",
    "bulk modulus": "Mechanics",
    "poisson ratio": "Mechanics",
    "poisson's ratio": "Mechanics",
    "yield strength": "Mechanics",
    "strength": "Mechanics",
    "toughness": "Mechanics",
    "fracture toughness": "Mechanics",
    "hardness": "Mechanics",
    "fatigue limit": "Mechanics",
    
    # Electrical
    "resistivity": "Electrical",
    "conductivity": "Electrical",
    "dielectric constant": "Electrical",
    "breakdown field": "Electrical",
    "carrier mobility": "Electrical",
    "mobility": "Electrical",
    "doping concentration": "Electrical",
    "carrier lifetime": "Electrical",
    "recombination coefficient": "Electrical",
    
    # Thermal
    "thermal conductivity": "Thermal",
    "specific heat": "Thermal",
    "specific heat capacity": "Thermal",
    "cte": "Thermal",
    "coefficient of thermal expansion": "Thermal",
    "thermal expansion coefficient": "Thermal",
    "thermal diffusivity": "Thermal",
    "melting point": "Thermal",
    "glass transition temperature": "Thermal",
    "glass transition temp": "Thermal",
    
    # Optical
    "band gap": "Optical",
    "refractive index": "Optical",
    "absorption coefficient": "Optical",
    
    # Magnetic
    "gilbert damping": "Magnetic",
    "landé g-factor": "Magnetic",
    "lande g-factor": "Magnetic",
    "damping anisotropy": "Magnetic",
    
    # Interface
    "adhesion": "Interface",
    "shear strength": "Interface",
    "interfacial shear strength": "Interface",
    "thermal resistance": "Interface",
}


def get_or_create_property(conn, property_name, category=None):
    """Get property ID; create if missing with auto-category mapping."""
    cursor = conn.cursor()
    prop_lower = property_name.lower().strip()
    
    # Auto-detect category if not provided
    if category is None or category == "unknown":
        category = "General"
        for key, cat in PROPERTY_CATEGORY_MAP.items():
            if key in prop_lower:
                category = cat
                break
    
    # Check if property already exists (case-insensitive)
    cursor.execute("SELECT id FROM properties WHERE LOWER(english_name) = ?", (prop_lower,))
    row = cursor.fetchone()
    if row:
        # Update category if it was NULL
        cursor.execute(
            "UPDATE properties SET category = ? WHERE id = ? AND category IS NULL",
            (category, row[0])
        )
        conn.commit()
        return row[0]
    else:
        cursor.execute(
            "INSERT INTO properties (english_name, category) VALUES (?, ?)",
            (property_name, category)
        )
        conn.commit()
        return cursor.lastrowid


# ============================================================
# SMART ROUTER: Store to Dedicated Tables
# ============================================================

def store_temperature_property(conn, article_id, data):
    cursor = conn.cursor()
    material = data.get("material")
    property_name = data.get("property")
    temp_c = data.get("temperature_c")
    value = data.get("value")
    unit = data.get("unit")
    
    if not material or not property_name:
        return 0
    
    mat_id = ensure_material_exists(conn, material)
    
    try:
        temp_val = float(temp_c) if temp_c else None
    except (ValueError, TypeError):
        temp_val = None
    try:
        prop_val = float(value) if value else None
    except (ValueError, TypeError):
        prop_val = None
    
    cursor.execute("""
        INSERT INTO temperature_properties
        (material_id, property_type, temperature_celsius, value, unit, article_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mat_id, property_name, temp_val, prop_val, unit, article_id))
    return 1


def store_fatigue_property(conn, article_id, data):
    cursor = conn.cursor()
    material = data.get("material")
    property_name = data.get("property")
    value = data.get("value")
    unit = data.get("unit")
    conditions = data.get("conditions", "")
    
    if not material or not property_name:
        return 0
    
    mat_id = ensure_material_exists(conn, material)
    
    try:
        prop_val = float(value) if value else None
    except (ValueError, TypeError):
        prop_val = None
    
    cycles = None
    stress = None
    if "cycle" in property_name.lower():
        cycles = prop_val
    else:
        stress = prop_val
    
    cursor.execute("""
        INSERT INTO fatigue_properties
        (material_id, property_type, stress_amplitude_MPa, cycles_to_failure, source, article_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mat_id, property_name, stress, cycles, conditions, article_id))
    return 1


def store_interface_property(conn, article_id, data):
    cursor = conn.cursor()
    mat1 = data.get("material_1")
    mat2 = data.get("material_2")
    property_name = data.get("property")
    value = data.get("value")
    unit = data.get("unit")
    conditions = data.get("conditions", "")
    temp_c = data.get("temperature_c")
    
    if not mat1 or not mat2:
        return 0
    
    mat1_id = ensure_material_exists(conn, mat1)
    mat2_id = ensure_material_exists(conn, mat2)
    
    try:
        prop_val = float(value) if value else None
    except (ValueError, TypeError):
        prop_val = None
    try:
        temp_val = float(temp_c) if temp_c else None
    except (ValueError, TypeError):
        temp_val = None
    
    cursor.execute("""
        INSERT INTO interface_properties
        (material_id, material12_id, property_type, value, unit, source, temperature_celsius, article_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (mat1_id, mat2_id, property_name, prop_val, unit, conditions, temp_val, article_id))
    return 1


def store_constitutive_model(conn, article_id, data):
    cursor = conn.cursor()
    material = data.get("material")
    model_name = data.get("model_name")
    params = data.get("parameters", {})
    
    if not model_name or not material:
        return 0
    
    mat_id = ensure_material_exists(conn, material)
    linear_coeff = params.get("linear_coefficient") or params.get("linear")
    exp_coeff = params.get("exponential_coefficient") or params.get("exponential")
    sat_stress = params.get("saturation_stress") or params.get("saturation")
    yield_stress = params.get("yield_stress_initial") or params.get("yield")
    
    temp_dep = {k: v for k, v in params.items() 
                if k not in ["linear_coefficient", "exponential_coefficient", 
                             "saturation_stress", "yield_stress_initial",
                             "linear", "exponential", "saturation", "yield"]}
    temp_dep_json = json.dumps(temp_dep) if temp_dep else None
    
    cursor.execute("SELECT id FROM constitutive_models WHERE material_id = ?", (mat_id,))
    row = cursor.fetchone()
    
    if row:
        cursor.execute("""
            UPDATE constitutive_models
            SET model_type = ?,
                linear_coefficient = ?,
                exponential_coefficient = ?,
                saturation_stress = ?,
                yield_stress_initial = ?,
                temperature_dependence = ?,
                article_id = ?
            WHERE material_id = ?
        """, (model_name, linear_coeff, exp_coeff, sat_stress, yield_stress,
              temp_dep_json, article_id, mat_id))
    else:
        cursor.execute("""
            INSERT INTO constitutive_models
            (material_id, model_type, linear_coefficient, exponential_coefficient,
             saturation_stress, yield_stress_initial, temperature_dependence, article_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (mat_id, model_name, linear_coeff, exp_coeff, sat_stress, yield_stress,
              temp_dep_json, article_id))
    
    conn.commit()
    return 1


def store_bending_property(conn, article_id, data):
    cursor = conn.cursor()
    material = data.get("material")
    property_name = data.get("property")
    value = data.get("value")
    unit = data.get("unit")
    conditions = data.get("conditions", "")
    
    if not material:
        return 0
    
    mat_id = ensure_material_exists(conn, material)
    
    try:
        prop_val = float(value) if value else None
    except (ValueError, TypeError):
        prop_val = None
    
    bend_radius = strain = critical_load = deflection = load = None
    if "radius" in property_name.lower():
        bend_radius = prop_val
    elif "strain" in property_name.lower():
        strain = prop_val
    elif "buckling" in property_name.lower() or "critical" in property_name.lower():
        critical_load = prop_val
    elif "deflection" in property_name.lower():
        deflection = prop_val
    elif "load" in property_name.lower() and "buckling" not in property_name.lower():
        load = prop_val
    
    cursor.execute("""
        INSERT INTO bending_parameters
        (material_id, bending_radius_mm, strain_at_radius,
         critical_buckling_load_N, deflection_mm, load_N, source, article_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (mat_id, bend_radius, strain, critical_load, deflection, load, conditions, article_id))
    return 1


def store_generic_property(conn, article_id, data):
    cursor = conn.cursor()
    material = data.get("material")
    property_name = data.get("property")
    value = data.get("value")
    unit = data.get("unit")
    conditions = data.get("conditions", "")
    
    if not material or not property_name:
        return 0
    
    mat_id = ensure_material_exists(conn, material)
    prop_id = get_or_create_property(conn, property_name)
    
    try:
        prop_val = float(value) if value else None
    except (ValueError, TypeError):
        prop_val = None
    
    cursor.execute("""
        INSERT INTO material_properties
        (material_id, property_id, value, unit, source, article_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (mat_id, prop_id, prop_val, unit, conditions, article_id))
    return 1


# ============================================================
# UPSERT ARTICLE METADATA
# ============================================================
def get_or_create_article_from_pdf(pdf_path, result_metadata):
    abs_pdf_path = os.path.abspath(pdf_path)
    article_info = result_metadata.get("article", {})
    
    authors_raw = article_info.get("authors", "")
    if isinstance(authors_raw, list):
        authors = "; ".join(authors_raw)
    else:
        authors = str(authors_raw) if authors_raw else ""
    
    data = {
        "title": article_info.get("title", os.path.basename(pdf_path)),
        "authors": authors,
        "journal": article_info.get("journal", ""),
        "date": str(article_info.get("year", "")) if article_info.get("year") else "",
        "source": article_info.get("doi", ""),
        "pdf_path": abs_pdf_path,
        "innovations": article_info.get("innovation", ""),
        "main_findings": article_info.get("main_findings", ""),
        "abstract": article_info.get("abstract", ""),
        "fabrication_process": article_info.get("fabrication_process", ""),
        "battery_structure": article_info.get("device_structure", ""),
        "efficiency_percent": article_info.get("efficiency_percent"),
        "open_circuit_voltage": article_info.get("open_circuit_voltage"),
        "short_circuit_current": article_info.get("short_circuit_current"),
        "fill_factor": article_info.get("fill_factor"),
        "areal_density": article_info.get("areal_density"),
        "specific_power": article_info.get("specific_power"),
        "is_solar_cell": article_info.get("is_solar_cell"),
        "is_gaas": article_info.get("is_gaas"),
        "is_flexible_thin_film_gaas": article_info.get("is_flexible_thin_film_gaas"),
        "is_flexible_substrate": article_info.get("is_flexible_substrate"),
        "recall_rate": article_info.get("recall_rate"),
        "matching_degree": article_info.get("matching_degree"),
    }
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM articles WHERE pdf_path = ?", (abs_pdf_path,))
    row = cursor.fetchone()
    
    if row:
        article_id = row[0]
        set_parts = []
        params = []
        for col, val in data.items():
            if col == "pdf_path":
                continue
            if val is not None:
                set_parts.append(f"{col} = ?")
                params.append(val)
        if set_parts:
            query = f"UPDATE articles SET {', '.join(set_parts)} WHERE id = ?"
            params.append(article_id)
            cursor.execute(query, params)
            conn.commit()
    else:
        columns = list(data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        values = [data[col] for col in columns]
        query = f"INSERT INTO articles ({', '.join(columns)}) VALUES ({placeholders})"
        cursor.execute(query, values)
        conn.commit()
        article_id = cursor.lastrowid
    
    conn.close()
    return article_id


# ============================================================
# LOAD ARTICLES
# ============================================================
@st.cache_data(ttl=300)
def get_articles_with_pdfs():
    conn = get_connection()
    try:
        df = pd.read_sql("""
            SELECT a.*, m.english_name AS material_english
            FROM articles a
            LEFT JOIN materials m ON a.material_id = m.id
            WHERE a.pdf_path IS NOT NULL AND a.pdf_path != ''
            ORDER BY a.id DESC
        """, conn)
        return df
    finally:
        conn.close()


def get_folder_structure(base_path):
    base = Path(base_path)
    if not base.exists():
        return {}
    structure = {}
    for subdir in base.iterdir():
        if subdir.is_dir():
            pdfs = list(subdir.glob("*.pdf"))
            if pdfs:
                structure[subdir.name] = [str(p) for p in pdfs]
    return structure


# ============================================================
# SIDEBAR USAGE DASHBOARD (helper)
# ============================================================
def sidebar_usage_dashboard():
    """Display token usage summary in the sidebar."""
    st.divider()
    st.subheader("📊 Weekly Token Usage")
    summary = get_usage_summary()
    st.metric("Used", f"{summary['total_used']:,}")
    st.metric("Remaining", f"{summary['remaining']:,}")
    st.progress(min(100, summary['percent']) / 100)
    st.caption(f"Limit: {summary['limit']:,} tokens")


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="PDF Analyzer", layout="wide")
st.title("🔬 PDF Structured Extractor")
st.markdown("Extract materials science data using hybrid retrieval + DeepSeek LLM")

# ----- Source selection -----
source_mode = st.radio(
    "Select article source:",
    ["📁 Local folder (articles)", "🗄️ Database", "📤 Upload PDF"],
    horizontal=True,
)

article_id = None
pdf_path = None
uploaded_file = None

if source_mode == "📁 Local folder (articles)":
    st.subheader("📂 Choose from articles folder")
    
    if not os.path.exists(ARTICLES_BASE):
        st.error(f"Articles folder not found at: {ARTICLES_BASE}")
        st.stop()
    
    folder_structure = get_folder_structure(ARTICLES_BASE)
    if not folder_structure:
        st.warning("No subfolders containing PDF files found.")
        st.stop()
    
    selected_subfolder = st.selectbox("Select subfolder", list(folder_structure.keys()))
    pdf_files = folder_structure[selected_subfolder]
    selected_pdf_name = st.selectbox("Select PDF file", [os.path.basename(p) for p in pdf_files])
    pdf_path = [p for p in pdf_files if os.path.basename(p) == selected_pdf_name][0]
    
    st.info(f"📄 Selected: `{pdf_path}`")
    
    with st.sidebar:
        st.subheader("ℹ️ File Info")
        st.markdown(f"**Subfolder:** {selected_subfolder}")
        st.markdown(f"**PDF:** {os.path.basename(pdf_path)}")
        st.divider()
        st.subheader("🔌 API Status")
        is_connected, status_message = check_api_connection()
        if is_connected:
            st.success(status_message)
        else:
            st.error(status_message)
            st.caption("⚠️ Extraction will fail. Check your API key in config.yaml")
        
        # ---- USAGE DASHBOARD ----
        sidebar_usage_dashboard()

elif source_mode == "🗄️ Database":
    df = get_articles_with_pdfs()
    if df.empty:
        st.warning("No PDFs available in database.")
        st.stop()
    
    st.subheader("📄 Select an Article from Database")
    article_options = []
    for _, row in df.iterrows():
        title = row['title'][:80] + "..." if len(row['title']) > 80 else row['title']
        material = f" ({row['material_english']})" if pd.notna(row['material_english']) else ""
        article_options.append(f"{title}{material}")
    
    selected_idx = st.selectbox("Choose article", options=range(len(article_options)), format_func=lambda i: article_options[i])
    selected_article = df.iloc[selected_idx]
    pdf_path = selected_article['pdf_path']
    article_id = selected_article['id']
    
    with st.sidebar:
        st.subheader("ℹ️ Article Info")
        st.markdown(f"**Title**\n{selected_article['title'][:100]}...")
        if pd.notna(selected_article.get('authors')):
            st.markdown(f"**Authors**\n{selected_article['authors']}")
        if pd.notna(selected_article.get('journal')):
            st.markdown(f"**Journal**\n{selected_article['journal']}")
        st.divider()
        st.subheader("🔌 API Status")
        is_connected, status_message = check_api_connection()
        if is_connected:
            st.success(status_message)
        else:
            st.error(status_message)
        
        # ---- USAGE DASHBOARD ----
        sidebar_usage_dashboard()

else:  # Upload PDF
    st.subheader("📤 Upload a PDF File")
    uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")
    
    if uploaded_file is not None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"upload_{timestamp}_{uploaded_file.name}"
        save_path = os.path.join(UPLOAD_DIR, safe_name)
        
        with open(save_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        pdf_path = save_path
        st.success(f"✅ File saved to: `{save_path}`")
        st.info("📄 File is now stored permanently and can be accessed later from the database.")
    else:
        st.info("Please upload a PDF to begin.")
    
    with st.sidebar:
        st.subheader("ℹ️ Upload Info")
        if uploaded_file is not None:
            st.markdown(f"**Filename:** {uploaded_file.name}")
            st.markdown(f"**Size:** {uploaded_file.size // 1024} KB")
            st.markdown(f"**Saved as:** `{os.path.basename(pdf_path)}`")
        else:
            st.markdown("No file uploaded yet.")
        st.divider()
        st.subheader("🔌 API Status")
        is_connected, status_message = check_api_connection()
        if is_connected:
            st.success(status_message)
        else:
            st.error(status_message)
            st.caption("⚠️ Extraction will fail. Check your API key in config.yaml")
        
        # ---- USAGE DASHBOARD ----
        sidebar_usage_dashboard()

# ----- Analysis button -----
api_ok, _ = check_api_connection()
button_disabled = not api_ok or (source_mode == "📤 Upload PDF" and uploaded_file is None)

if st.button("🚀 Run Extraction", type="primary", use_container_width=True, disabled=button_disabled):
    if not pdf_path or not os.path.exists(pdf_path):
        st.error(f"PDF file not found: {pdf_path}")
        st.stop()
    
    with st.status("📄 Analyzing PDF...", expanded=True) as status:
        try:
            status.update(label="⏳ Loading PDF and building retriever...")
            rag_output = analyze_pdf(pdf_path)
            
            status.update(label="📊 Extracting structured data...")
            result = rag_output["result"]
            raw_response = rag_output["raw_response"]
            st.session_state['last_result'] = result
            
            status.update(label="💾 Saving article metadata...")
            article_id = get_or_create_article_from_pdf(pdf_path, result)
            st.session_state['last_article_id'] = article_id
            
            status.update(label="💾 Saving properties to database...")
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rag_workflow 
                (article_id, query, response, retrieved_chunks, temperature, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                article_id,
                rag_output.get("context", "")[:5000],
                rag_output.get("raw_response", ""),
                rag_output.get("num_chunks", 0),
                0.0,
                datetime.now()
            ))
            conn.commit()
            conn.close()
            
            # ---- Save all property types ----
            conn = get_connection()
            total_saved = 0
            try:
                for item in result.get("temperature_properties", []):
                    total_saved += store_temperature_property(conn, article_id, item)
                for item in result.get("fatigue_properties", []):
                    total_saved += store_fatigue_property(conn, article_id, item)
                for item in result.get("interface_properties", []):
                    total_saved += store_interface_property(conn, article_id, item)
                for item in result.get("constitutive_models", []):
                    total_saved += store_constitutive_model(conn, article_id, item)
                for item in result.get("bending_properties", []):
                    total_saved += store_bending_property(conn, article_id, item)
                
                fallback_items = (
                    result.get("material_properties", []) +
                    result.get("mechanical_properties", []) +
                    result.get("experimental_measurements", [])
                )
                for item in fallback_items:
                    total_saved += store_generic_property(conn, article_id, item)
                
                conn.commit()
            except Exception as db_err:
                conn.rollback()
                raise db_err
            finally:
                conn.close()
            
            status.update(label=f"✅ Done – {total_saved} properties saved!", state="complete")
            st.balloons()
            
        except Exception as e:
            status.update(label=f"❌ Error: {str(e)}", state="error")
            st.code(traceback.format_exc())


# ============================================================
# DISPLAY RESULTS (unchanged)
# ============================================================
if 'last_result' in st.session_state and (source_mode == "🗄️ Database" or ('last_article_id' in st.session_state and st.session_state.get('last_article_id') == article_id)):
    result = st.session_state['last_result']
    article = result.get("article", {})

    st.markdown("## 📌 Article Summary")
    col1, col2, col3 = st.columns(3)
    with col1:
        title = article.get("title") or "N/A"
        title_display = (title[:60] + "...") if len(title) > 60 else title
        st.metric("Title", title_display)
        st.metric("Year", article.get("year") or "N/A")
    with col2:
        st.metric("Journal", article.get("journal") or "N/A")
        st.metric("Paper Type", article.get("paper_type") or "N/A")
    with col3:
        st.metric("DOI", article.get("doi") or "N/A")
        eff = article.get("efficiency_percent")
        st.metric("Efficiency (%)", f"{eff:.2f}" if eff is not None else "N/A")
    
    st.markdown("**Innovation**  \n" + (article.get("innovation") or "Not reported"))
    st.markdown("**Main Findings**  \n" + (article.get("main_findings") or "Not reported"))

    if article.get("fabrication_process"):
        with st.expander("🏭 Fabrication Process"):
            st.write(article["fabrication_process"])
    if article.get("device_structure"):
        with st.expander("📐 Device Structure"):
            st.write(article["device_structure"])

    materials = result.get("materials", [])
    if materials:
        st.markdown("## 🧪 Materials Used")
        st.dataframe(pd.DataFrame(materials), use_container_width=True)

    categories = {
        "📊 Material Properties": "material_properties",
        "🌡️ Temperature‑Dependent Properties": "temperature_properties",
        "🔗 Interface Properties": "interface_properties",
        "⚙️ Mechanical Properties": "mechanical_properties",
        "📐 Constitutive Models": "constitutive_models",
        "🔁 Fatigue Properties": "fatigue_properties",
        "🧪 Experimental Measurements": "experimental_measurements"
    }
    for title, key in categories.items():
        items = result.get(key, [])
        if items:
            st.markdown(f"## {title}")
            if key == "constitutive_models":
                for model in items:
                    st.markdown(f"**{model.get('model_name')}**  \nParameters: `{model.get('parameters')}`")
            else:
                df_items = pd.DataFrame(items)
                df_items = df_items.dropna(axis=1, how='all')
                st.dataframe(df_items, use_container_width=True)

    with st.expander("📄 Export as JSON"):
        st.json(result)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="Download JSON",
            data=json.dumps(result, indent=2, ensure_ascii=False),
            file_name=f"extraction_{article_id}_{timestamp}.json",
            mime="application/json"
        )


# ============================================================
# DISPLAY PREVIOUSLY STORED PROPERTIES FROM DB
# ============================================================
if article_id is not None:
    st.markdown("## 💾 Previously Extracted Properties (Database)")
    try:
        conn = get_connection()
        existing = pd.read_sql("""
            SELECT m.english_name AS material, 
                   p.english_name AS property,
                   p.category AS category,
                   mp.value, mp.unit, mp.source, mp.extraction_date
            FROM material_properties mp
            JOIN materials m ON mp.material_id = m.id
            JOIN properties p ON mp.property_id = p.id
            WHERE mp.article_id = ?
            ORDER BY mp.extraction_date DESC
        """, conn, params=(article_id,))
        conn.close()
        
        if not existing.empty:
            st.markdown("#### 📋 Properties Grouped by Category")
            
            category_display = {
                "Mechanics": "⚙️ Mechanics",
                "Electrical": "⚡ Electrical",
                "Thermal": "🌡️ Thermal",
                "Magnetic": "🧲 Magnetic",
                "Optical": "🔦 Optical",
                "Interface": "🔗 Interface",
                "unknown": "📊 Uncategorized"
            }
            
            for category, group in existing.groupby("category"):
                display_name = category_display.get(category, category)
                st.markdown(f"**{display_name}**")
                display_df = group.drop(columns=["category"])
                st.dataframe(display_df, use_container_width=True)
        else:
            st.info("No properties stored for this article yet.")
            
    except Exception as e:
        st.warning(f"Could not load previous properties: {e}")


# ============================================================
# ENHANCED RAG WORKFLOW VIEWER
# ============================================================
with st.expander("🧠 RAG Workflow Inspector (Debugging)", expanded=False):
    try:
        conn = get_connection()
        
        df_rag = pd.read_sql("""
            SELECT 
                id,
                article_id,
                timestamp,
                retrieved_chunks,
                temperature,
                CASE 
                    WHEN response IS NOT NULL AND response != '' 
                    THEN '✅ Success' 
                    ELSE '❌ Failed' 
                END AS status,
                SUBSTR(query, 1, 100) AS query_preview,
                SUBSTR(response, 1, 100) AS response_preview
            FROM rag_workflow
            ORDER BY timestamp DESC
            LIMIT 15
        """, conn)
        conn.close()

        if not df_rag.empty:
            total_runs = len(df_rag)
            success_count = len(df_rag[df_rag['status'] == '✅ Success'])
            st.caption(f"📊 Last {total_runs} runs: {success_count} successful, {total_runs - success_count} failed.")
            
            for idx, row in df_rag.iterrows():
                with st.expander(f"🔎 Run {row['id']} | Article: {row['article_id']} | {row['timestamp']} | {row['status']}"):
                    col_a, col_b = st.columns(2)
                    
                    with col_a:
                        st.markdown("**📥 Retrieved Context (Query)**")
                        conn_detail = get_connection()
                        cursor_detail = conn_detail.cursor()
                        cursor_detail.execute("SELECT query FROM rag_workflow WHERE id = ?", (row['id'],))
                        full_query = cursor_detail.fetchone()[0]
                        conn_detail.close()
                        st.text_area("Full Context", full_query[:3000] + "..." if len(full_query) > 3000 else full_query, height=200, key=f"query_{row['id']}")
                    
                    with col_b:
                        st.markdown("**📤 LLM Response**")
                        conn_detail = get_connection()
                        cursor_detail = conn_detail.cursor()
                        cursor_detail.execute("SELECT response FROM rag_workflow WHERE id = ?", (row['id'],))
                        full_response = cursor_detail.fetchone()[0]
                        conn_detail.close()
                        
                        try:
                            response_json = json.loads(full_response)
                            st.json(response_json)
                        except:
                            st.text_area("Raw Response", full_response[:2000] + "..." if len(full_response) > 2000 else full_response, height=200, key=f"resp_{row['id']}")
                    
                    st.caption(f"🧩 Chunks Retrieved: {row['retrieved_chunks']} | 🌡️ Temperature: {row['temperature']} | 🆔 Article ID: {row['article_id']}")
        else:
            st.info("No RAG workflow records found yet.")
            
    except Exception as e:
        st.warning(f"Could not load workflow: {e}")
