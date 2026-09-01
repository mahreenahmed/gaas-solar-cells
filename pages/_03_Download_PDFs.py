import streamlit as st
from modules.download_articles import download_pdfs
from modules.db_utils import get_connection
import pandas as pd
import yaml
import sqlite3
import os
import re
import time

def ensure_pdf_path_column_exists(db_path="materials_properties.db"):
    """Ensure the pdf_path column exists in the articles table"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(articles)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'pdf_path' not in columns:
            cursor.execute("ALTER TABLE articles ADD COLUMN pdf_path TEXT;")
            conn.commit()
    except Exception as e:
        st.error(f"❌ Error checking/adding pdf_path column: {e}")
    finally:
        conn.close()

def get_articles_without_pdfs():
    """Get articles that need PDF downloading"""
    try:
        conn = get_connection()
        df = pd.read_sql("""
            SELECT a.*, 
                   m.english_name AS material_english, 
                   m.chinese_name AS material_chinese
            FROM articles a
            LEFT JOIN materials m ON a.material_id = m.id
            WHERE a.pdf_path IS NULL OR a.pdf_path = ''
        """, conn)
        conn.close()
        return df
    except:
        try:
            conn = get_connection()
            df = pd.read_sql("""
                SELECT a.*, 
                       m.english_name AS material_english, 
                       m.chinese_name AS material_chinese
                FROM articles a
                LEFT JOIN materials m ON a.material_id = m.id
            """, conn)
            conn.close()
            return df
        except:
            return pd.DataFrame()

def get_download_stats():
    """Get download statistics"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(articles)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'pdf_path' in columns:
            stats_df = pd.read_sql("""
                SELECT 
                    COUNT(*) as total_articles,
                    SUM(CASE WHEN pdf_path IS NULL OR pdf_path = '' THEN 1 ELSE 0 END) as pending_downloads,
                    SUM(CASE WHEN pdf_path IS NOT NULL AND pdf_path != '' THEN 1 ELSE 0 END) as downloaded
                FROM articles
            """, conn)
        else:
            total = pd.read_sql("SELECT COUNT(*) as total_articles FROM articles", conn)
            stats_df = pd.DataFrame({
                'total_articles': [total['total_articles'].iloc[0]],
                'pending_downloads': [total['total_articles'].iloc[0]],
                'downloaded': [0]
            })
        conn.close()
        return stats_df.iloc[0]
    except Exception as e:
        st.error(f"Error getting statistics: {str(e)}")
        return pd.Series({'total_articles': 0, 'pending_downloads': 0, 'downloaded': 0})

def reindex_pdfs(downloads_dir, db_path):
    """
    Scan the downloads directory (with material subfolders) and update
    database pdf_path entries for existing PDFs.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    updated = 0
    errors = []

    # Walk through all subfolders
    for root, dirs, files in os.walk(downloads_dir):
        for file in files:
            if not file.lower().endswith('.pdf'):
                continue
            full_path = os.path.join(root, file)
            # Try to extract article ID from filename (assuming pattern "..._ID.pdf")
            match = re.search(r'_(\d+)\.pdf$', file)
            if match:
                article_id = int(match.group(1))
                try:
                    cursor.execute("UPDATE articles SET pdf_path = ? WHERE id = ?", (full_path, article_id))
                    if cursor.rowcount > 0:
                        updated += 1
                except Exception as e:
                    errors.append(f"ID {article_id}: {e}")
            else:
                # Fallback: try to match by title (slow, but optional)
                # For now, skip
                errors.append(f"Skipped {file} – no article ID in filename")
    conn.commit()
    conn.close()
    return updated, errors

st.title("📥 Download PDFs")

# Load configuration
try:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    default_downloads_dir = config.get("downloads_dir", "downloads")
    db_path = config["db"]["path"]
except FileNotFoundError:
    st.error("config.yaml file not found. Please create a config file.")
    st.stop()
except KeyError as e:
    st.error(f"Missing key in config.yaml: {e}")
    st.stop()

# Initialize session state
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None

# Ensure the pdf_path column exists
ensure_pdf_path_column_exists(db_path)

# Let user choose download directory
downloads_dir = st.text_input(
    "Download directory",
    value=default_downloads_dir,
    help="Directory where PDFs will be saved (material subfolders will be created inside)"
)

# Create directory if it doesn't exist
if downloads_dir and not os.path.exists(downloads_dir):
    if st.button("Create Directory"):
        try:
            os.makedirs(downloads_dir, exist_ok=True)
            st.success(f"Created directory: {downloads_dir}")
            st.session_state.last_refresh = time.time()
            st.rerun()
        except Exception as e:
            st.error(f"Error creating directory: {str(e)}")

# --- NEW: Re-index existing PDFs ---
st.subheader("🔄 Update Existing PDF Paths")
st.info("If you have previously downloaded PDFs that are now stored in material subfolders, use this button to update the database with the correct file paths.")
if st.button("Re‑index PDFs in Download Folder"):
    if not os.path.exists(downloads_dir):
        st.error(f"Download directory '{downloads_dir}' does not exist.")
    else:
        with st.spinner("Scanning for PDFs and updating database..."):
            updated, errors = reindex_pdfs(downloads_dir, db_path)
        st.success(f"✅ Updated {updated} article PDF paths.")
        if errors:
            st.warning(f"Could not update {len(errors)} files. Errors:\n" + "\n".join(errors[:10]))
        st.session_state.last_refresh = time.time()
        st.rerun()

# --- Main download section ---
st.subheader("📥 Download New PDFs")
df = get_articles_without_pdfs()
st.write(f"{len(df)} articles need PDF downloading")

if not df.empty:
    # Display articles that need downloading
    display_cols = ["material_english", "material_chinese", "title", "source", "doi", "link"]
    df_display = df[display_cols].copy()
    
    def make_clickable(url, source, pmid=None):
        if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
            return f'<a href="{url}" target="_blank">🔗 View</a>'
        elif source == 'PubMed' and pmid:
            return f'<a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">🔗 View</a>'
        return "No link"
    
    if 'pmid' in df.columns:
        df_display['link'] = df.apply(
            lambda row: make_clickable(row['link'], row['source'], row.get('pmid')), 
            axis=1
        )
    else:
        df_display['link'] = df.apply(
            lambda row: make_clickable(row['link'], row['source']), 
            axis=1
        )
    
    st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
    
    if st.button("Download PDFs", type="primary"):
        if not downloads_dir:
            st.error("Please specify a download directory")
        else:
            with st.spinner("Downloading PDFs..."):
                try:
                    logs, files = download_pdfs(
                        df.to_dict(orient="records"), 
                        output_folder=downloads_dir, 
                        db_path=db_path, 
                        table_name="articles"
                    )
                    st.success(f"Downloaded {len(files)} PDFs to {downloads_dir}")
                    if logs:
                        st.subheader("Download Log")
                        st.text_area("Logs", "\n".join(logs), height=300)
                    if files:
                        st.subheader("Downloaded Files")
                        for file in files:
                            st.write(f"✅ {file}")
                    st.session_state.last_refresh = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Error during download: {str(e)}")
else:
    st.success("No articles need downloading – all have PDF paths!")

# Show statistics
stats = get_download_stats()
st.subheader("📊 Download Statistics")
col1, col2, col3 = st.columns(3)
col1.metric("Total Articles", stats['total_articles'])
col2.metric("Pending Downloads", stats['pending_downloads'])
col3.metric("Downloaded PDFs", stats['downloaded'])

if st.button("🔄 Refresh Statistics"):
    st.session_state.last_refresh = True
    st.rerun()