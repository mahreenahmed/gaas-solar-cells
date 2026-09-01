# pages/_02_Search_Articles.py

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from modules.search_articles import fetch_arxiv_articles, fetch_pubmed_articles
from modules.db_models import Material, Article

# ---------- Config ----------
DB_PATH = "materials_properties.db"
MAX_RESULTS = 20

st.set_page_config(page_title="🔍 Search Articles by Materials", layout="wide")
st.title("🔍 Search & Import Articles")

# ---------- Database Setup ----------
@st.cache_resource
def get_session():
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    return Session()

session = get_session()

# ---------- Load materials ----------
materials = session.query(Material).all()
material_names = [f"{m.english_name} ({m.chinese_name})" if m.chinese_name else m.english_name for m in materials]

# ---------- Session State ----------
if 'articles_df' not in st.session_state:
    st.session_state.articles_df = pd.DataFrame()
if 'selected_article_index' not in st.session_state:
    st.session_state.selected_article_index = 0
if 'search_executed' not in st.session_state:
    st.session_state.search_executed = False

# ---------- User Input ----------
selected_materials = st.multiselect(
    "Select materials to search articles for",
    options=material_names
)

query_column = st.selectbox(
    "Search term from column",
    ["english_name", "chinese_name", "formula"],
    format_func=lambda x: {"english_name": "English Name", "chinese_name": "Chinese Name", "formula": "Formula"}[x]
)

max_results = st.number_input("Max results per source", min_value=1, max_value=100, value=MAX_RESULTS)

# ---------- Search and Insert ----------
if st.button("Run Search"):
    if not selected_materials:
        st.warning("Please select at least one material.")
    else:
        total_inserted = 0
        progress_bar = st.progress(0)
        status_text = st.empty()

        with st.spinner("Fetching articles..."):
            for i, sel in enumerate(selected_materials):
                progress_bar.progress(i / len(selected_materials))
                status_text.text(f"Processing {i+1}/{len(selected_materials)}: {sel}")

                english_name = sel.split(" (")[0]
                material = session.query(Material).filter_by(english_name=english_name).first()
                if not material:
                    continue

                term_value = getattr(material, query_column)
                if not term_value:
                    continue

                # Fetch articles
                arxiv_articles = fetch_arxiv_articles([term_value], max_results=max_results)
                pubmed_articles = fetch_pubmed_articles([term_value], max_results=max_results)
                all_articles = arxiv_articles + pubmed_articles

                st.write(f"Fetched {len(arxiv_articles)} arXiv & {len(pubmed_articles)} PubMed articles for '{term_value}'")

                for art in all_articles:
                    if not art.get("title"):
                        continue

                    # Check for duplicates (by title and source for this material)
                    existing = session.query(Article).filter(
                        Article.material_id == material.id,
                        Article.title == art["title"],
                        Article.source == art["source"]
                    ).first()
                    if existing:
                        continue

                    # Prepare fields for Article model
                    authors = art.get("authors", [])
                    authors_str = ", ".join(authors) if isinstance(authors, list) else str(authors)

                    # Map date: arXiv uses 'published', PubMed uses 'date'
                    date_value = art.get("date") or art.get("published")
                    # Map link: arXiv returns 'link', PubMed may return None
                    link_value = art.get("link") if art["source"] == "arXiv" else None
                    # Map journal: arXiv has no journal, PubMed has 'journal'
                    journal_value = art.get("journal") if art["source"] == "PubMed" else None

                    new_article = Article(
                        material_id=material.id,
                        title=art["title"],
                        authors=authors_str,
                        source=art["source"],
                        journal=journal_value,
                        date=date_value,
                        doi=art.get("doi"),
                        link=link_value,
                        pmid=art.get("pmid"),
                        abstract=art.get("abstract"),
                        # All other fields (innovations, battery_structure, etc.) remain NULL initially
                    )
                    session.add(new_article)
                    total_inserted += 1

                session.commit()

            progress_bar.progress(1.0)
            status_text.text("Complete!")
            st.success(f"Saved {total_inserted} new articles to DB")

            # Reload articles into session state using ORM query
            query = session.query(
                Article,
                Material.english_name.label("material_english"),
                Material.chinese_name.label("material_chinese")
            ).join(Material, Article.material_id == Material.id).order_by(Article.id.desc())
            df = pd.read_sql(query.statement, session.bind)
            st.session_state.articles_df = df
            st.session_state.search_executed = True

# ---------- Display Articles ----------
def display_articles():
    df = st.session_state.articles_df
    if df.empty:
        if st.session_state.search_executed:
            st.info("No articles found. Run a search above.")
        return

    # Prepare display DataFrame
    display_cols = ["material_english", "material_chinese", "title", "authors", "source", "journal", "date"]
    df_show = df[display_cols].copy()

    # Create clickable links
    def make_clickable(row):
        if row["source"] == "arXiv" and row.get("link"):
            return f'<a href="{row["link"]}" target="_blank">🔗 View</a>'
        elif row["source"] == "PubMed" and row.get("pmid"):
            return f'<a href="https://pubmed.ncbi.nlm.nih.gov/{row["pmid"]}/" target="_blank">🔗 View</a>'
        elif row.get("doi"):
            return f'<a href="https://doi.org/{row["doi"]}" target="_blank">🔗 View</a>'
        return "No link"

    df_show["link"] = df.apply(make_clickable, axis=1)
    display_cols.append("link")

    st.markdown("### 📄 Articles in Database")
    st.write(df_show[display_cols].to_html(escape=False, index=False), unsafe_allow_html=True)

    # Article selection for details
    article_options = [
        f"{row['title'][:70]}... ({row['source']})" if len(row['title']) > 70
        else f"{row['title']} ({row['source']})"
        for _, row in df.iterrows()
    ]

    selected_idx = st.selectbox(
        "Select article to view details",
        options=range(len(article_options)),
        format_func=lambda x: article_options[x],
        index=st.session_state.selected_article_index,
        key="article_selector"
    )
    st.session_state.selected_article_index = selected_idx

    # Show detailed view
    art = df.iloc[selected_idx]
    st.subheader(art["title"])
    st.markdown(f"**Material:** {art['material_english']} ({art['material_chinese']})")
    st.markdown(f"**Authors:** {art['authors']}")
    st.markdown(f"**Source:** {art['source']}")
    if art.get("journal"):
        st.markdown(f"**Journal:** {art['journal']}")
    if art.get("date"):
        st.markdown(f"**Date:** {art['date']}")
    if art.get("doi"):
        st.markdown(f"**DOI:** {art['doi']}")
    if art.get("link"):
        st.markdown(f"**Link:** [Open article]({art['link']})")
    elif art.get("pmid"):
        st.markdown(f"**PubMed ID:** {art['pmid']} – [View on PubMed](https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}/)")

    if art.get("abstract"):
        st.markdown("**Abstract:**")
        st.write(art["abstract"])

    # Show extended fields (extraction will be added later)
    st.markdown("#### 🧪 Extracted Parameters (to be filled by RAG)")
    col1, col2 = st.columns(2)
    with col1:
        st.text_input("Innovations / Highlights", value=art.get("innovations") or "", key="innov_input", disabled=True)
        st.text_input("Battery Structure", value=art.get("battery_structure") or "", key="batt_struct_input", disabled=True)
        st.text_input("Fabrication Process", value=art.get("fabrication_process") or "", key="fab_input", disabled=True)
    with col2:
        st.number_input("Efficiency (%)", value=art.get("efficiency_percent") or 0.0, key="eff_input", disabled=True, format="%.2f")
        st.number_input("Open Circuit Voltage (V)", value=art.get("open_circuit_voltage") or 0.0, key="voc_input", disabled=True, format="%.3f")
        st.number_input("Short Circuit Current (mA/cm²)", value=art.get("short_circuit_current") or 0.0, key="jsc_input", disabled=True, format="%.2f")
        st.number_input("Fill Factor", value=art.get("fill_factor") or 0.0, key="ff_input", disabled=True, format="%.3f")

    # Download button
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download Full Articles CSV", data=csv, file_name="articles.csv", mime="text/csv")

# Load existing articles on first run (if any)
if not st.session_state.search_executed and st.session_state.articles_df.empty:
    try:
        query = session.query(
            Article,
            Material.english_name.label("material_english"),
            Material.chinese_name.label("material_chinese")
        ).join(Material, Article.material_id == Material.id).order_by(Article.id.desc())
        df_existing = pd.read_sql(query.statement, session.bind)
        if not df_existing.empty:
            st.session_state.articles_df = df_existing
            st.info(f"Loaded {len(df_existing)} existing articles from database.")
    except Exception as e:
        st.error(f"Error loading existing articles: {str(e)}")

# Display articles if we have them
display_articles()

# ---------- Delete All Articles ----------
if st.button("🗑 Delete All Articles", type="primary"):
    try:
        session.query(Article).delete()
        session.commit()
        st.success("All articles deleted.")
        st.session_state.articles_df = pd.DataFrame()
        st.session_state.search_executed = False
        st.rerun()
    except Exception as e:
        st.error(f"Error deleting articles: {str(e)}")

session.close()