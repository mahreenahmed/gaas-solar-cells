import requests
import os
import sqlite3
import re
import time

def sanitize_folder_name(name):
    """Sanitize string to be used as a folder name."""
    # Remove invalid characters for Windows/Linux
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Remove leading/trailing spaces and dots
    name = name.strip('. ')
    # Limit length
    if len(name) > 100:
        name = name[:100]
    return name

def get_material_name(conn, material_id):
    """Return English name of material given its ID, or None if not found."""
    if not material_id:
        return None
    cursor = conn.cursor()
    cursor.execute("SELECT english_name FROM materials WHERE id = ?", (material_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def download_pdfs(articles, output_folder="downloads", db_path="materials_properties.db", table_name="articles"):
    """
    Download PDFs for articles and update database with file paths.
    Each PDF is saved into a subfolder named after the article's material.
    
    Args:
        articles: List of article dictionaries (must include 'id', 'material_id', 'title', 'link', 'source', 'pmid')
        output_folder: Base directory to save PDFs (subfolders created per material)
        db_path: Path to SQLite database
        table_name: Name of articles table
    
    Returns:
        tuple: (list of log messages, list of downloaded file paths)
    """
    logs = []
    downloaded_files = []

    # Create base output directory
    try:
        os.makedirs(output_folder, exist_ok=True)
        logs.append(f"📁 Base directory: {output_folder}")
    except Exception as e:
        logs.append(f"❌ Error creating directory {output_folder}: {str(e)}")
        return logs, downloaded_files

    # Connect to database (to look up material names)
    conn = sqlite3.connect(db_path)

    for article in articles:
        try:
            title = article.get('title', 'Unknown')
            article_id = article.get('id')
            material_id = article.get('material_id')
            source = article.get('source', '')
            link = article.get('link', '')
            pmid = article.get('pmid', '')

            logs.append(f"Processing: {title[:50]}...")

            # Determine material name
            material_name = get_material_name(conn, material_id)
            if not material_name:
                material_name = "Unknown_Material"

            # Create material‑specific subfolder
            material_folder = sanitize_folder_name(material_name)
            target_dir = os.path.join(output_folder, material_folder)
            os.makedirs(target_dir, exist_ok=True)
            logs.append(f"  📁 Material folder: {material_folder}")

            # Determine PDF URL based on source
            pdf_url = None
            if source == 'arXiv' and link:
                if '/abs/' in link:
                    pdf_url = link.replace('/abs/', '/pdf/') + '.pdf'
                elif not link.endswith('.pdf'):
                    pdf_url = link + '.pdf'
            elif source == 'PubMed' and pmid:
                pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmid}/pdf/"

            if not pdf_url and link and link.endswith('.pdf'):
                pdf_url = link

            if not pdf_url:
                logs.append(f"  ⚠️  No PDF URL found for {source} article")
                continue

            # Create filename
            if title and title != 'Unknown':
                clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                filename = f"{clean_title[:50]}_{article_id}.pdf"
            else:
                filename = f"article_{article_id}.pdf"

            filepath = os.path.join(target_dir, filename)

            # Download the PDF
            logs.append(f"  📥 Downloading from: {pdf_url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(pdf_url, headers=headers, timeout=30)

            if response.status_code == 200 and response.content.startswith(b'%PDF'):
                with open(filepath, 'wb') as f:
                    f.write(response.content)

                # Update database with the full path
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE {table_name} SET pdf_path = ? WHERE id = ?",
                    (filepath, article_id)
                )
                conn.commit()

                downloaded_files.append(filepath)
                logs.append(f"  ✅ Successfully downloaded: {filename} into {material_folder}/")
            else:
                logs.append(f"  ❌ Failed to download PDF (status: {response.status_code})")

            time.sleep(1)  # Be respectful to servers

        except Exception as e:
            logs.append(f"  ❌ Error processing article: {str(e)}")
            continue

    conn.close()
    return logs, downloaded_files

def check_pdf_url(url):
    """Check if a URL points to a PDF file."""
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        content_type = response.headers.get('content-type', '').lower()
        return 'pdf' in content_type or url.lower().endswith('.pdf')
    except:
        return False