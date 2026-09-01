import streamlit as st

st.set_page_config(page_title="Materials Research Portal", layout="wide", page_icon="🧪")
st.title("🧪 Materials Research Portal")

st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", [
    "📂 Database Viewer",
    "🔍 Search Articles",
    "📥 Download PDFs",
    "📄 PDF Analyzer"
])

# Dynamically load the page modules
if page == "📂 Database Viewer":
    import pages._01_DB_Viewer
elif page == "🔍 Search Articles":
    import pages._02_Search_Articles
elif page == "📥 Download PDFs":
    import pages._03_Download_PDFs
elif page == "📄 PDF Analyzer":
    import pages._04_Analyzer
else:
    st.write("Select a page from the sidebar.")
