import json
import re
import yaml
import requests
import time   # <-- added for sleep

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


# ==========================================================
# RETRY WRAPPER
# ==========================================================

def call_api_with_retry(
    url,
    headers,
    payload,
    max_retries=5,
    initial_timeout=300,
    backoff_factor=2
):
    """
    Makes an API request with automatic retries for 429 (rate limit) and timeouts.
    Returns the response object on success, or raises an exception after exhausting retries.
    """
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=initial_timeout)

            # If rate limited (429)
            if response.status_code == 429:
                # Try to read Retry-After header, fallback to 10 seconds
                retry_after = int(response.headers.get('Retry-After', 10))
                wait_time = retry_after + 1   # add a small buffer
                print(f"⚠️ Rate limited (429). Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                continue  # retry

            # For other 4xx/5xx errors that are not 429, raise immediately
            response.raise_for_status()
            return response  # success

        except requests.exceptions.Timeout:
            # Exponential backoff for timeouts
            wait_time = backoff_factor ** attempt
            print(f"⏰ Timeout (attempt {attempt+1}/{max_retries}). Retrying in {wait_time}s...")
            time.sleep(wait_time)

        except requests.exceptions.ConnectionError:
            wait_time = backoff_factor ** attempt
            print(f"🔌 Connection error (attempt {attempt+1}/{max_retries}). Retrying in {wait_time}s...")
            time.sleep(wait_time)

        except requests.exceptions.RequestException as e:
            # For other request exceptions (e.g., 500), retry with backoff
            if attempt < max_retries - 1:
                wait_time = backoff_factor ** attempt
                print(f"❌ Request error: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise  # re-raise if max retries exceeded

    raise Exception(f"❌ Max retries ({max_retries}) exceeded. Request failed.")


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

        # ---- Replace the old requests.post call with the retry wrapper ----
        try:
            response = call_api_with_retry(
                url=self.api_url,
                headers=headers,
                payload=payload,
                max_retries=5,
                initial_timeout=300   # increased from 120 to 300
            )

            result = response.json()
            return result["choices"][0]["message"]["content"]

        except Exception as e:
            # Re-raise a clear error message
            raise Exception(f"❌ API call failed after retries: {str(e)}")


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
    retriever = db.as_retriever(search_kwargs={"k": 25})
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
        docs.extend(retriever.invoke(query))

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
    return response


# ==========================================================
# MAIN ENTRY (unchanged)
# ==========================================================

def analyze_pdf(pdf_path):
    llm = DeepSeekChat()
    retriever = build_retriever(pdf_path)
    context = build_context(retriever)
    
    raw_response = extract_structured_data(llm, context)
    
    try:
        parsed_result = json.loads(raw_response)
    except json.JSONDecodeError as e:
        parsed_result = {"error": "Invalid JSON returned from LLM", "raw": raw_response}
        print(f"JSON decode error: {e}")
    
    return {
        "result": parsed_result,
        "raw_response": raw_response,
        "context": context,
        "num_chunks": len(context.split("PAGE:")) - 1
    }
