import json
import re
import yaml
import requests   # <-- added for direct API calls

from langchain_community.document_loaders import PyMuPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.retrievers import BM25Retriever, EnsembleRetriever


# ==========================================================
# LLM WRAPPER (no openai, uses requests)
# ==========================================================

class DeepSeekChat:
    def __init__(self):
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # Try nested 'deepseek' first, then flat root-level
        deepseek_cfg = cfg.get("deepseek", cfg)
        
        self.api_key = deepseek_cfg.get("api_key")
        self.api_url = deepseek_cfg.get("base_url")
        self.model = deepseek_cfg.get("api_model", "deepseek-chat")

        if not self.api_key:
            raise ValueError("❌ API key not found in config.yaml (under 'api_key' or 'deepseek.api_key')")
        if not self.api_url:
            raise ValueError("❌ base_url not found in config.yaml")

    def invoke(self, system_prompt, user_prompt, temperature=0):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }

        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=120)
            
            # --- Better error messages ---
            if response.status_code == 403:
                raise Exception("❌ 403 Forbidden: Invalid API Key or insufficient permissions.")
            if response.status_code == 401:
                raise Exception("❌ 401 Unauthorized: Invalid credentials.")
            if response.status_code == 402:
                raise Exception("❌ 402 Payment Required: Please check your DeepSeek balance.")
            if response.status_code != 200:
                raise Exception(f"❌ API Error {response.status_code}: {response.text[:200]}")

            result = response.json()
            return result["choices"][0]["message"]["content"]
            
        except requests.exceptions.Timeout:
            raise Exception("❌ Connection timeout: API unreachable.")
        except requests.exceptions.ConnectionError:
            raise Exception("❌ Connection error: Check your internet or API endpoint.")

# ==========================================================
# PDF PROCESSING (unchanged)
# ==========================================================

def extract_section_title(text):
    patterns = [
        r'(?:^|\n)([A-Z][A-Z0-9 \-\t]+)(?:\n|$)',
        r'(?:^|\n)(\d+\.\s+.+?)(?:\n|$)'
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1).strip()
    return "Unknown"


def build_retriever(pdf_path):
    loader = PyMuPDFLoader(pdf_path)
    docs = loader.load()
    for doc in docs:
        doc.metadata["page"] = doc.metadata.get("page", 0) + 1

    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    for chunk in chunks:
        chunk.metadata["section"] = extract_section_title(chunk.page_content)

    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    db = FAISS.from_documents(chunks, embedding)
    faiss = db.as_retriever(search_kwargs={"k": 25})

    bm25 = BM25Retriever.from_texts(
        [c.page_content for c in chunks],
        metadatas=[c.metadata for c in chunks]
    )
    bm25.k = 25

    retriever = EnsembleRetriever(retrievers=[faiss, bm25], weights=[0.6, 0.4])
    return retriever


# ==========================================================
# RETRIEVAL (unchanged)
# ==========================================================

RETRIEVAL_QUERIES = [
    "abstract innovation contribution",
    "materials used",
    "fabrication process growth deposition synthesis",
    "thermal properties",
    "mechanical properties",
    "electrical properties",
    "optical properties",
    "interface properties adhesion shear strength",
    "temperature dependent properties",
    "elastic modulus",
    "yield strength",
    "fracture toughness",
    "fatigue crack growth",
    "constitutive model",
    "experimental results",
    "performance metrics"
]


def build_context(retriever):
    docs = []
    for query in RETRIEVAL_QUERIES:
        docs.extend(retriever.get_relevant_documents(query))

    unique_docs = []
    seen = set()
    for d in docs:
        key = (d.metadata.get("page"), d.page_content[:250])
        if key not in seen:
            unique_docs.append(d)
            seen.add(key)

    context = []
    for d in unique_docs[:30]:
        context.append(f"""
PAGE: {d.metadata.get('page')}
SECTION: {d.metadata.get('section')}

{d.page_content}
""")
    return "\n\n-------------------\n\n".join(context)


# ==========================================================
# EXTRACTION (unchanged)
# ==========================================================

def extract_structured_data(llm, context):
    prompt = f"""
You are a scientific literature extraction engine.

Extract ONLY information explicitly reported in the paper.

Never invent values.

Return ONLY valid JSON.

JSON schema:

{{
  "article": {{
      "title": "",
      "authors": [],
      "journal": "",
      "year": null,
      "doi": "",
      "innovation": "",
      "main_findings": "",
      "paper_type": "",
      "fabrication_process": "",
      "device_structure": "",
      "efficiency_percent": null,
      "open_circuit_voltage": null,
      "short_circuit_current": null,
      "fill_factor": null,
      "specific_power": null,
      "areal_density": null
  }},
  "materials": [
      {{"name": "", "role": ""}}
  ],
  "material_properties": [
      {{"material": "", "property": "", "value": "", "unit": "", "conditions": ""}}
  ],
  "temperature_properties": [
      {{"material": "", "property": "", "temperature_c": null, "value": "", "unit": ""}}
  ],
  "interface_properties": [
      {{"material_1": "", "material_2": "", "property": "", "value": "", "unit": ""}}
  ],
  "mechanical_properties": [
      {{"material": "", "property": "", "value": "", "unit": ""}}
  ],
  "constitutive_models": [
      {{"model_name": "", "parameters": {{}}}}
  ],
  "fatigue_properties": [
      {{"material": "", "property": "", "value": "", "unit": ""}}
  ],
  "experimental_measurements": [
      {{"material": "", "measurement": "", "value": "", "unit": "", "conditions": ""}}
  ]
}}

Paper Text:

{context}
"""
    response = llm.invoke(
        "You are a materials science and scientific literature extraction expert.",
        prompt
    )
    # Return the raw string – DO NOT parse JSON here
    return response
# ==========================================================
# MAIN ENTRY
# ==========================================================

# ==========================================================
# MAIN ENTRY (UPDATED to return RAG details)
# ==========================================================

def analyze_pdf(pdf_path):
    llm = DeepSeekChat()
    retriever = build_retriever(pdf_path)
    context = build_context(retriever)
    
    # 1. Get the raw JSON string from the LLM
    raw_response = extract_structured_data(llm, context)  # Now returns a string
    
    # 2. Parse the raw string into a dict
    try:
        parsed_result = json.loads(raw_response)
    except json.JSONDecodeError as e:
        # If parsing fails, store the raw string in the result
        parsed_result = {"error": "Invalid JSON returned from LLM", "raw": raw_response}
        print(f"JSON decode error: {e}")
    
    # 3. Return all information needed for logging and saving
    return {
        "result": parsed_result,
        "raw_response": raw_response,
        "context": context,
        "num_chunks": len(context.split("PAGE:")) - 1
    }
