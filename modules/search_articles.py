# modules/search_articles.py

import requests
import xml.etree.ElementTree as ET
import time
from Bio import Entrez

# Remove the fixed MAX_RESULTS constant from here
# MAX_RESULTS = 50  # REMOVE THIS LINE

# Define namespaces for consistent parsing
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom'
}

def fetch_arxiv_articles(query_terms, max_results=50):  # Add max_results parameter
    """Fetch real articles from arXiv API"""
    base_url = "http://export.arxiv.org/api/query?"
    articles = []

    for term in query_terms:
        # Broader query: search anywhere in title or abstract, relevant categories
        query = f"all:{term} AND (cat:cond-mat.mtrl-sci OR cat:physics.app-ph)"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,  # Use the parameter
            "sortBy": "submittedDate",
            "sortOrder": "descending"
        }

        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            root = ET.fromstring(response.content)

            # Register namespaces for easier parsing
            for prefix, uri in NAMESPACES.items():
                ET.register_namespace(prefix, uri)

            # Find all entries
            entries = root.findall('.//atom:entry', NAMESPACES)
            
            for entry in entries:
                title_node = entry.find('atom:title', NAMESPACES)
                if title_node is None or not title_node.text.strip():
                    continue

                # Extract authors
                authors = []
                author_nodes = entry.findall('atom:author', NAMESPACES)
                for author in author_nodes:
                    name_node = author.find('atom:name', NAMESPACES)
                    if name_node is not None and name_node.text:
                        authors.append(name_node.text.strip())

                # Extract abstract
                abstract_node = entry.find('atom:summary', NAMESPACES)
                abstract_text = abstract_node.text.strip() if abstract_node is not None and abstract_node.text else ""

                # Extract other metadata
                doi_node = entry.find('arxiv:doi', NAMESPACES)
                link_node = entry.find('atom:id', NAMESPACES)
                published_node = entry.find('atom:published', NAMESPACES)

                article = {
                    "category": term,
                    "source": "arXiv",
                    "title": title_node.text.strip(),
                    "authors": authors,
                    "abstract": abstract_text,
                    "published": published_node.text if published_node is not None else "",
                    "doi": doi_node.text if doi_node is not None else None,
                    "link": link_node.text if link_node is not None else ""
                }

                articles.append(article)

        except Exception as e:
            print(f"arXiv API error for term '{term}': {str(e)}")

        time.sleep(3)

    return [a for a in articles if a.get("title")]


def fetch_pubmed_articles(query_terms, max_results=50):  # Add max_results parameter
    """Fetch real articles from PubMed"""
    articles = []
    Entrez.email = "mehreen@sjtu.edu.cn"  # You should change this to your email

    for term in query_terms:
        try:
            # Search PubMed
            handle = Entrez.esearch(
                db="pubmed",
                term=f"{term}[All Fields] AND (polymer OR semiconductor)",
                retmax=max_results,  # Use the parameter
                sort="relevance"
            )
            result = Entrez.read(handle)
            handle.close()

            id_list = result.get("IdList", [])
            if not id_list:
                continue

            # Fetch details
            handle = Entrez.efetch(db="pubmed", id=id_list, retmode="xml")
            records = Entrez.read(handle)
            handle.close()

            # Process each article
            for record in records.get('PubmedArticle', []):
                try:
                    # Extract title
                    medline_citation = record.get('MedlineCitation', {})
                    article_data = medline_citation.get('Article', {})
                    title = str(article_data.get('ArticleTitle', '')).strip()
                    
                    if not title or title == '[]':
                        continue

                    # Extract authors
                    authors = []
                    author_list = article_data.get('AuthorList', [])
                    for author in author_list:
                        try:
                            if isinstance(author, dict):
                                last_name = author.get('LastName', '')
                                initials = author.get('Initials', '')
                                if last_name or initials:
                                    authors.append(f"{last_name} {initials}".strip())
                        except:
                            continue

                    # Extract abstract
                    abstract_text = ""
                    abstract = article_data.get('Abstract', {})
                    if abstract:
                        abstract_text_list = abstract.get('AbstractText', [])
                        if isinstance(abstract_text_list, list):
                            abstract_text = ' '.join([str(text) for text in abstract_text_list if text])
                        else:
                            abstract_text = str(abstract_text_list)

                    # Extract DOI
                    doi = None
                    article_id_list = record.get('PubmedData', {}).get('ArticleIdList', [])
                    for article_id in article_id_list:
                        if hasattr(article_id, 'attributes') and article_id.attributes.get('IdType') == 'doi':
                            doi = str(article_id)
                            break
                        elif isinstance(article_id, dict) and article_id.get('@IdType') == 'doi':
                            doi = article_id.get('#text', '')
                            break

                    # Extract journal info
                    journal = article_data.get('Journal', {}).get('Title', '')
                    journal_issue = article_data.get('Journal', {}).get('JournalIssue', {})
                    pub_date = journal_issue.get('PubDate', {})
                    date_str = f"{pub_date.get('Year', '')}-{pub_date.get('Month', '')}-{pub_date.get('Day', '')}"

                    # Extract PMID
                    pmid = str(medline_citation.get('PMID', ''))

                    article = {
                        "category": term,
                        "source": "PubMed",
                        "pmid": pmid,
                        "title": title,
                        "authors": authors,
                        "abstract": abstract_text,
                        "doi": doi,
                        "journal": str(journal),
                        "date": date_str
                    }

                    articles.append(article)
                    
                except Exception as e:
                    print(f"Error processing PubMed article: {str(e)}")
                    continue

        except Exception as e:
            print(f"PubMed API error for term '{term}': {str(e)}")
            continue

        time.sleep(1)

    return [a for a in articles if a.get("title")]