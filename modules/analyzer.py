import json
import re
import yaml
import requests
import time
import os
from datetime import datetime, timedelta

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS


# ==========================================================
# USAGE TRACKING
# ==========================================================

# Path to store usage data (same directory as this file)
USAGE_FILE = os.path.join(os.path.dirname(__file__), "token_usage.json")
WEEKLY_LIMIT = 1_000_000_000   # 1 billion tokens (from your approval email)

def get_usage_data():
    """Read usage data from file, reset if a new week has started."""
    if not os.path.exists(USAGE_FILE):
        return {"week_start": datetime.now().isoformat(), "total_used": 0}
    
    with open(USAGE_FILE, 'r') as f:
        data = json.load(f)
    
    # Reset if the week has changed (e.g., every Monday)
    last_start = datetime.fromisoformat(data["week_start"])
    if datetime.now() - last_start > timedelta(days=7):
        print("🔄 New week detected. Resetting token counter.")
        return {"week_start": datetime.now().isoformat(), "total_used": 0}
    
    return data

def save_usage_data(total_used):
    """Save updated usage data."""
    data = {
        "week_start": datetime.now().isoformat(),
        "total_used": total_used
    }
    with open(USAGE_FILE, 'w') as f:
        json.dump(data, f)

def update_usage(tokens_used):
    """Add tokens_used to the weekly total and save."""
    data = get_usage_data()
    new_total = data["total_used"] + tokens_used
    save_usage_data(new_total)
    return new_total

def get_usage_summary():
    """Return a dict with total_used, remaining, percent, and limit."""
    data = get_usage_data()
    total = data["total_used"]
    remaining = max(0, WEEKLY_LIMIT - total)
    percent = min(100, (total / WEEKLY_LIMIT) * 100)
    return {
        "total_used": total,
        "remaining": remaining,
        "percent": percent,
        "limit": WEEKLY_LIMIT
    }


# ==========================================================
# RETRY WRAPPER
# ==========================================================

def call_api_with_retry(
    url,
    headers,
    payload,
    max_retries=3,
    connect_timeout=10,
    read_timeout=180,
    backoff_factor=2
):
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(connect_timeout, read_timeout)
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 5))
                wait_time = retry_after + 1
                print(f"⚠️ Rate limited (429). Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            return response

        except requests.exceptions.ConnectTimeout:
            print(f"🔌 Connection timeout (attempt {attempt+1}).")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise Exception("❌ Cannot reach API. Check network/VPN.")

        except requests.exceptions.ReadTimeout:
            wait_time = backoff_factor ** attempt
            print(f"⏰ Read timeout (attempt {attempt+1}/{max_retries}). Retrying in {wait_time}s...")
            if attempt < max_retries - 1:
                time.sleep(wait_time)
            else:
                raise Exception("❌ LLM took too long. Reduce PDF size or increase timeout.")

        except requests.exceptions.ConnectionError:
            print(f"🔌 Connection error (attempt {attempt+1}). Retrying...")
            time.sleep(2)

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                print(f"❌ Request error: {e}. Retrying...")
                time.sleep(2)
            else:
                raise

    raise Exception("❌ Max retries exceeded.")


# ==========================================================
# LLM WRAPPER
# ==========================================================

class DeepSeekChat:
    def __init__(self):
        with open("config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        deepseek_cfg = cfg.get("deepseek", cfg)
        self.api_key = deepseek_cfg.get("api_key")
        self.api_url = deepseek_cfg.get("base_url")
        self.model = deepseek_cfg.get("api_model", "deepseek-chat")

        if not self.api_key:
            raise ValueError("❌ API key not found in config.yaml")
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
            ],
            "max_tokens": 2048   # limit output to speed up
        }

        response = call_api_with_retry(
            url=self.api_url,
            headers=headers,
            payload=payload,
            max_retries=3,
            connect_timeout=10,
            read_timeout=180
        )

        result = response.json()
        content = result["choices"][0]["message"]["content"]

        # ----- Track token usage -----
        usage = result.get('usage', {})
        total_tokens = usage.get('total_tokens', 0)
        if total_tokens > 0:
            new_total = update_usage(total_tokens)
            print(f"📊 Tokens used: {total_tokens:,} | Weekly total: {new_total:,}")

        return content


# ==========================================================
# PDF PROCESSING (optimised)
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

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(docs)
    for chunk in chunks:
        chunk.metadata["section"] = extract_section_title(chunk.page_content)

    embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    db = FAISS.from_documents(chunks, embedding)
    retriever = db.as_retriever(search_kwargs={"k": 10})
    return retriever


# ==========================================================
# RETRIEVAL (reduced context)
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
    for d in unique_docs[:10]:
        context.append(f"""
PAGE: {d.metadata.get('page')}
SECTION: {d.metadata.get('section')}

{d.page_content}
""")
    full_context = "\n\n-------------------\n\n".join(context)

    # Truncate to avoid overly long prompts
    MAX_CONTEXT_CHARS = 8000
    if len(full_context) > MAX_CONTEXT_CHARS:
        full_context = full_context[:MAX_CONTEXT_CHARS] + "\n... (truncated)"
        print(f"⚠️ Context truncated to {MAX_CONTEXT_CHARS} chars")

    return full_context


# ==========================================================
# EXTRACTION
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
# MAIN ENTRY
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
